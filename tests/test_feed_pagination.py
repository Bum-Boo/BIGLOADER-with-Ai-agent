"""Feed pagination uses bounded fake pages; no database or HTTP is opened."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ig_post_controller.services.instagram_service import InstagramService


class FeedPaginationTests(unittest.TestCase):
    def service_with_pages(self, pages, max_calls):
        service = InstagramService.__new__(InstagramService)
        calls = []
        def fetch(username, *, next_max_id=None):
            self.assertEqual(username, "synthetic_account")
            calls.append(next_max_id)
            if len(calls) > max_calls:
                raise AssertionError("pagination repeated a consumed cursor")
            return pages[next_max_id]
        service._fetch_feed_page = fetch
        return service, calls

    def test_repeated_cursor_stops_without_refetching_page(self):
        page = [{"code": "a"}]
        service, calls = self.service_with_pages({None: (page, "A"), "A": (page, "A")}, 2)
        self.assertEqual(service._fetch_feed_items("synthetic_account", count=3), page)
        self.assertEqual(calls, [None, "A"])

    def test_cursor_cycle_stops_after_each_distinct_request(self):
        page = [{"code": "a"}]
        service, calls = self.service_with_pages(
            {None: (page, "A"), "A": (page, "B"), "B": (page, "A")}, 3
        )
        self.assertEqual(service._fetch_feed_items("synthetic_account", count=3), page)
        self.assertEqual(calls, [None, "A", "B"])

    def test_duplicate_page_with_new_cursor_can_reach_later_unique_posts(self):
        service, calls = self.service_with_pages({
            None: ([{"code": "a"}], "A"),
            "A": ([{"code": "a"}], "B"),
            "B": ([{"code": "b"}, {"code": "c"}], "C"),
        }, 3)
        self.assertEqual([p["code"] for p in service._fetch_feed_items("synthetic_account", count=3)], ["a", "b", "c"])
        self.assertEqual(calls, [None, "A", "B"])

    def test_count_limit_is_preserved_inside_a_page(self):
        service, calls = self.service_with_pages({None: ([{"code": "a"}, {"code": "b"}], "A")}, 1)
        self.assertEqual(service._fetch_feed_items("synthetic_account", count=1), [{"code": "a"}])
        self.assertEqual(calls, [None])

    def test_zero_count_and_empty_page_stop_without_extra_requests(self):
        service, calls = self.service_with_pages({None: ([], "A")}, 1)
        self.assertEqual(service._fetch_feed_items("synthetic_account", count=0), [])
        self.assertEqual(calls, [])
        self.assertEqual(service._fetch_feed_items("synthetic_account", count=3), [])
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()
