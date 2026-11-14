import unittest

from porndoe_api.modules.consts import extract_video


class MetadataExportTests(unittest.TestCase):
    def test_producer_and_publisher_are_distinct(self):
        content = '''<script type="application/ld+json">{"@type":"VideoObject","name":"Example",
          "producer":{"name":"Producer"},"publisher":{"name":"Publisher"}}</script>'''
        fields = extract_video(content, "https://porndoe.com/watch/example")
        self.assertEqual(fields["producers"][0]["name"], "Producer")
        self.assertEqual(fields["publishers"][0]["name"], "Publisher")
        self.assertNotEqual(fields["description"], fields["title"])
