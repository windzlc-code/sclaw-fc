import json
import unittest
from unittest.mock import patch

import app as app_module


class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None


class PortalCaseSearchExactCountTests(unittest.TestCase):
    @patch.object(app_module, "load_crawl_settings", return_value={"portal_query_max_records": 100000})
    @patch.object(app_module, "_portal_case_search_exact_count_get_with_status", return_value=(None, "miss"))
    @patch.object(app_module, "_portal_case_search_exact_count_start", return_value="pending")
    @patch.object(app_module, "_portal_case_search_cache_get_with_status", return_value=(None, "miss"))
    @patch.object(app_module, "_portal_case_search_cache_set")
    @patch.object(app_module, "_portal_api_backfill_empty_thumbs", side_effect=lambda items, allow_live_fetch=False: (items, {}))
    @patch.object(app_module, "_portal_case_prioritize_displayable_media", side_effect=lambda items: items)
    @patch.object(app_module, "_portal_case_result_prefetch_images")
    @patch.object(
        app_module,
        "search_portal_cases",
        return_value={
            "count": 3853,
            "items": [{"source_item_id": 1}, {"source_item_id": 2}],
            "limit": 100000,
            "page_size": 6,
        },
    )
    @patch.object(app_module.threading, "Thread", _NoopThread)
    def test_paged_search_marks_count_as_pending_until_exact_total_is_ready(
        self,
        _search,
        _prefetch,
        _prioritize,
        _backfill,
        _cache_set,
        _cache_get,
        _count_start,
        _count_get,
        _settings,
    ):
        resp = app_module.api_portal_case_search(
            app_module.PortalCaseSearchRequest(
                transaction="buy",
                portals=["suumo", "homes"],
                region_hint="沖繩",
                limit=100000,
                page_size=6,
            )
        )

        data = json.loads(resp.body)
        self.assertEqual(data["count"], 3853)
        self.assertEqual(data["count_status"], "pending")
        self.assertTrue(data["count_provisional"])
        self.assertEqual(len(data["items"]), 2)

    @patch.object(app_module, "load_crawl_settings", return_value={"portal_query_max_records": 100000})
    @patch.object(
        app_module,
        "_portal_case_search_exact_count_get_with_status",
        return_value=({"count": 3718, "computed_at": 1718572800}, "ready"),
    )
    @patch.object(
        app_module,
        "_portal_case_search_cache_get_with_status",
        return_value=(
            json.dumps(
                {
                    "count": 3853,
                    "items": [{"source_item_id": 1}],
                    "limit": 100000,
                    "page_size": 6,
                },
                ensure_ascii=False,
            ),
            "hit",
        ),
    )
    def test_cached_paged_response_upgrades_to_exact_count_when_cache_is_ready(
        self,
        _cache_get,
        _count_get,
        _settings,
    ):
        resp = app_module.api_portal_case_search(
            app_module.PortalCaseSearchRequest(
                transaction="buy",
                portals=["suumo", "homes"],
                region_hint="東海",
                limit=100000,
                page_size=6,
            )
        )

        data = json.loads(resp.body)
        self.assertEqual(data["count"], 3718)
        self.assertEqual(data["count_status"], "ready")
        self.assertFalse(data["count_provisional"])
        self.assertEqual(len(data["items"]), 1)

    @patch.object(app_module, "load_crawl_settings", return_value={"portal_query_max_records": 100000})
    @patch.object(
        app_module,
        "_portal_case_search_exact_count_get_with_status",
        return_value=({"count": 219, "computed_at": 1718572800}, "ready"),
    )
    def test_exact_count_endpoint_returns_ready_count_payload(self, _count_get, _settings):
        resp = app_module.api_portal_case_search_count(
            app_module.PortalCaseSearchRequest(
                transaction="buy",
                portals=["suumo", "homes"],
                region_hint="沖繩",
                limit=100000,
                page_size=6,
            )
        )

        data = json.loads(resp.body)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["count"], 219)
        self.assertEqual(data["limit"], 100000)


if __name__ == "__main__":
    unittest.main()
