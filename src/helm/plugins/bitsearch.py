# VERSION: 1.0
# AUTHORS: Helm

import urllib.parse

from bs4 import BeautifulSoup
from helpers import retrieve_url, validate_html
from novaprinter import prettyPrinter, report_error


class bitsearch(object):
    url = "https://bitsearch.eu"
    name = "BitSearch"
    supported_categories = {"all": "all"}

    def search(self, what, cat="all"):
        query = urllib.parse.quote(what)
        html = retrieve_url(self.url + f"/search?q={query}")
        validate_html(html, ("bg-white",))
        soup = BeautifulSoup(html, "html.parser")

        for item in soup.select("div.bg-white.rounded-lg.shadow-sm"):
            try:
                title_elem = item.select_one("h3 a")
                if not title_elem:
                    report_error(ValueError("Result row has no title"))
                    continue
                res = {"name": title_elem.text.strip()}

                magnet_a = item.select_one('a[href^="magnet:"]')
                if not magnet_a:
                    report_error(ValueError("Result row has no magnet"))
                    continue
                res["link"] = magnet_a["href"]
                res["desc_link"] = self.url + title_elem["href"]

                # Extract file size
                size_icon = item.select_one("i.fa-hdd")
                res["size"] = size_icon.find_next_sibling("span").text.strip() if size_icon else "-1"

                # Extract seeds
                seed_icon = item.select_one("i.fa-arrow-up")
                res["seeds"] = seed_icon.find_next_sibling("span").text.strip() if seed_icon else "-1"

                # Extract leechers
                leech_icon = item.select_one("i.fa-arrow-down")
                res["leech"] = leech_icon.find_next_sibling("span").text.strip() if leech_icon else "-1"

                res["engine_url"] = self.url
                prettyPrinter(res)
            except Exception as error:
                report_error(error)
                continue
