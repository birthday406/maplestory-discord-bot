import unittest

from maple_bot import html_to_text, post_url, thumbnail_url, watched_posts


class NewsFilteringTests(unittest.TestCase):
    def test_watched_posts_includes_events_and_excludes_other_categories(self) -> None:
        posts = [
            {"id": 1, "category": "update"},
            {"id": 2, "category": "events"},
            {"id": 3, "category": "maintenance"},
            {"id": 4, "category": "community"},
        ]

        self.assertEqual([post["id"] for post in watched_posts(posts)], [1, 2, 3])

    def test_post_url_uses_category_id_and_title_slug(self) -> None:
        post = {"id": 123, "category": "sale", "name": "Summer Sale: It's Here!"}

        self.assertEqual(
            post_url(post),
            "https://www.nexon.com/maplestory/news/sale/123/summer-sale-it-s-here",
        )

    def test_thumbnail_url_uses_nexon_origin(self) -> None:
        post = {"imageThumbnail": "/media/example/thumbnail.png"}

        self.assertEqual(
            thumbnail_url(post),
            "https://www.nexon.com/media/example/thumbnail.png",
        )

    def test_html_to_text_removes_tags_and_script(self) -> None:
        source = "<h1>Patch</h1><script>ignore()</script><p>Notes</p>"
        self.assertEqual(html_to_text(source), "Patch Notes")
