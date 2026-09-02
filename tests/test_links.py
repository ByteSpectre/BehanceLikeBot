import unittest

from behancer_bot.links import (
    expand_redirect_url,
    extract_urls,
    is_behance_project,
    is_facebook_url,
)


class LinkTests(unittest.TestCase):
    def test_behance_projects_are_prioritized_and_deduplicated(self):
        text = "Сначала https://x.com/a/status/1 затем https://www.behance.net/gallery/123/Test."
        urls = extract_urls(text, ["https://www.behance.net/gallery/123/Test"])
        self.assertEqual(
            urls,
            [
                "https://www.behance.net/gallery/123/Test",
                "https://x.com/a/status/1",
            ],
        )

    def test_profile_is_not_a_project(self):
        self.assertFalse(is_behance_project("https://www.behance.net/designer"))
        self.assertTrue(
            is_behance_project("https://www.behance.net/gallery/123456/Project")
        )

    def test_unwraps_facebook_link_shim_url(self):
        facebook_url = (
            "https://l.facebook.com/l.php?"
            "u=https%3A%2F%2Fwww.behance.net%2Fgallery%2F987654%2FHealth-Event"
            "&h=tracking-token"
        )
        expanded = expand_redirect_url(facebook_url)
        self.assertIn(
            "https://www.behance.net/gallery/987654/Health-Event",
            expanded,
        )

    def test_recognizes_facebook_hosts_for_skipping(self):
        self.assertTrue(is_facebook_url("https://www.facebook.com/share/p/example"))
        self.assertTrue(is_facebook_url("https://l.facebook.com/l.php?u=test"))
        self.assertTrue(is_facebook_url("https://m.facebook.com/story.php?id=1"))
        self.assertFalse(
            is_facebook_url("https://www.behance.net/gallery/123/Facebook-design")
        )


if __name__ == "__main__":
    unittest.main()
