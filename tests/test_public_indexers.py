import json
from types import SimpleNamespace

import responses

from helm.search.indexer_manager import ProwlarrManager
from helm.search.public_indexers import PUBLIC_INDEXER_IDS, PUBLIC_INDEXERS
from helm.stack import stack_setup


def test_public_indexer_list_is_unique_and_https():
    assert len(set(PUBLIC_INDEXER_IDS)) == len(PUBLIC_INDEXER_IDS) >= 10
    for name, link in PUBLIC_INDEXERS:
        assert name == name.lower() and link.startswith("https://") and link.endswith("/")
    # Dead or legacy entries that used to be seeded.
    assert "nyaapantsu" not in PUBLIC_INDEXER_IDS and "torrentgalaxy" not in PUBLIC_INDEXER_IDS
    assert dict(PUBLIC_INDEXERS)["yts"] != "https://yts.mx/"


def test_seed_jackett_writes_every_indexer_in_jacketts_format(tmp_path, monkeypatch):
    monkeypatch.setattr(stack_setup, "say", lambda text="": None)
    stack_setup.seed_jackett(SimpleNamespace(state=str(tmp_path)))
    directory = tmp_path / "jackett" / "Jackett" / "Indexers"
    assert sorted(p.stem for p in directory.iterdir()) == sorted(PUBLIC_INDEXER_IDS)
    for name, link in PUBLIC_INDEXERS:
        (entry,) = json.loads((directory / f"{name}.json").read_text())
        assert entry == {"id": "sitelink", "type": "inputstring", "name": "Site Link", "value": link}


def test_seed_jackett_keeps_existing_indexer_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(stack_setup, "say", lambda text="": None)
    directory = tmp_path / "jackett" / "Jackett" / "Indexers"
    directory.mkdir(parents=True)
    saved = '[{"id": "sitelink", "value": "https://my-mirror.example/"}, {"id": "sort", "value": "seeders"}]'
    (directory / "1337x.json").write_text(saved)
    stack_setup.seed_jackett(SimpleNamespace(state=str(tmp_path)))
    assert (directory / "1337x.json").read_text() == saved
    assert (directory / "knaben.json").is_file()


@responses.activate
def test_prowlarr_seeds_the_shared_list(monkeypatch):
    monkeypatch.setenv("PROWLARR_API_KEY", "key")
    schema = [
        {"definitionName": name, "name": name, "implementation": "Cardigann"} for name in ("knaben", "uindex", "other")
    ] + [{"definitionName": "nyaapantsu", "name": "NyaaPantsu", "implementation": "Cardigann"}]
    base = "http://localhost:19696/api/v1/indexer"
    responses.add(responses.GET, f"{base}/schema", json=schema)
    responses.add(responses.GET, base, json=[])
    responses.add(responses.POST, base, json={"id": 1})
    assert ProwlarrManager().seed_default_indexers() == 2
    posted = [
        json.loads(call.request.body)["definitionName"] for call in responses.calls if call.request.method == "POST"
    ]
    assert posted == ["knaben", "uindex"]
