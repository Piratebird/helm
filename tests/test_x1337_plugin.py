import os
import re
import sys

import responses

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins")))

import novaprinter  # noqa: E402
import x1337  # noqa: E402

from helm.core.lite_plugin_loader import load_plugins  # noqa: E402

BUNDLED_PLUGINS = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins"))


def _list_page(*titles):
    rows = []
    for i, title in enumerate(titles):
        rows.append(
            (
                f'<td class="coll-1 name"><a href="/torrent/{i + 1}/{title.replace(chr(33), chr(33))}/">'
                f"{title}</a></td>"
                f'<td class="coll-2 seeds">120</td>'
                f'<td class="coll-3 leeches">30</td>'
                f'<td class="coll-4 size">2.1 GB</td>'
            )
        )
    return '<div class="table-list"><table><tbody><tr>' + "</tr><tr>".join(rows) + "</tr></tbody></table></div>"


MAGNET = "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&dn=x"


def _detail_page(slug):
    return f'<a href="{MAGNET}" title="magnet">Download</a><!-- {slug} -->'


def _plugin():
    plugins = load_plugins(BUNDLED_PLUGINS)
    for plugin in plugins:
        if plugin.__class__.__name__ == "x1337":
            return plugin
    raise AssertionError("x1337 plugin failed to load from bundled plugins")


def _collect():
    results = list(novaprinter.get_results())
    novaprinter.get_results().clear()
    return results


def _dune_list():
    return _list_page(
        "Dune.2021.1080p.WEBRip", "Dune.Part.Two.2024.1080p", "Dune.2021.2160p.x265", "Dune.2021.720p.WEBRip"
    )


def test_parse_rows_and_magnet():
    rows = x1337.parse_rows(_dune_list())
    assert len(rows) == 4
    assert rows[0]["name"] == "Dune.2021.1080p.WEBRip"
    assert rows[0]["seeds"] == 120
    assert rows[0]["size"] == "2.1 GB"
    assert x1337.extract_magnet(_detail_page("x")) == MAGNET


def test_relevance_gate():
    assert x1337._relevance("Dune.2021.1080p.WEBRip", "dune") is True
    assert x1337._relevance("Ready Player One (2018) [YTS]", "one piece") is False
    assert x1337._relevance("Birds of Prey and One Harley Quinn [YTS]", "one piece") is False
    assert x1337._relevance("One Piece - 1089 [1080p] [SubsPlease]", "one piece") is True


@responses.activate
def test_x1337_plugin_emits_relevant_top_rows():
    x1337._working_host = 0
    host = x1337.HOSTS[0]
    list_url = f"https://{host}/category-search/dune/Movies/1/"
    responses.add(responses.GET, list_url, body=_dune_list(), status=200)
    for row in x1337.parse_rows(_dune_list()):
        responses.add(responses.GET, f"https://{host}{row['path']}", body=_detail_page("x"), status=200)

    _plugin().search("dune")
    items = _collect()

    assert len(items) == 4
    assert all("urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in item.link for item in items)
    assert items[0].title == "Dune.2021.1080p.WEBRip"
    assert items[0].seeders == 120


@responses.activate
def test_x1337_plugin_skips_garbage_list():
    # A mirror can fall back to a generic list on a miss; nothing matching the
    # query may be emitted.
    x1337._working_host = 0
    host = x1337.HOSTS[0]
    list_url = f"https://{host}/category-search/one+piece/Movies/1/"
    responses.add(
        responses.GET,
        list_url,
        body=_list_page("Ready Player One (2018) [YTS]", "Birds of Prey (2020) [YTS]"),
        status=200,
    )

    _plugin().search("one piece")
    assert _collect() == []


@responses.activate
def test_x1337_plugin_returns_zero_when_all_mirrors_walled():
    x1337._working_host = 0
    for host in x1337.HOSTS:
        responses.add(
            responses.GET,
            f"https://{host}/category-search/the+boys/Movies/1/",
            body="Just a moment...</html>",
            status=403,
        )

    _plugin().search("the boys")
    assert _collect() == []


def test_extract_magnet_regex():
    assert re.fullmatch(r"magnet:\?xt=urn:btih:[a-f0-9]{40}&dn=x", MAGNET)
