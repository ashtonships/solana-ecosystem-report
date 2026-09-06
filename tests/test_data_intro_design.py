"""Navigation and accessible context for the compact Data contents index."""

from html.parser import HTMLParser
import unittest

import render


class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.descriptions = {}
        self.description_id = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self.links.append(attributes)
        if tag == "small":
            self.description_id = attributes["id"]
            self.descriptions[self.description_id] = ""

    def handle_data(self, data):
        if self.description_id:
            self.descriptions[self.description_id] += data

    def handle_endtag(self, tag):
        if tag == "small":
            self.description_id = None


class TestDataIntroDesign(unittest.TestCase):
    def test_each_destination_retains_an_accessible_description_in_both_layouts(self):
        seen_ids = set()
        for context in ("mobile", "desktop"):
            with self.subTest(context=context):
                parser = IndexParser()
                parser.feed(render.render_data_domain_rail({}, context))
                self.assertEqual(
                    [link["href"] for link in parser.links],
                    ["#overview", f"#{context}-validator-evidence",
                     f"#{context}-people-markets", f"#{context}-community-news",
                     f"#{context}-data-sources"],
                )
                for link in parser.links:
                    description_id = link["aria-describedby"]
                    self.assertNotIn(description_id, seen_ids)
                    seen_ids.add(description_id)
                    self.assertTrue(parser.descriptions[description_id].strip())


if __name__ == "__main__":
    unittest.main()
