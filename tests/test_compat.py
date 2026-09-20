import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steamswap import compat


def _resp(appid, success=True, categories=()):
    return {str(appid): {"success": success,
                          "data": {"categories": [{"id": c, "description": "x"} for c in categories]}}}


class FetchTests(unittest.TestCase):
    def test_full_controller_and_remote_play(self):
        with patch.object(compat, "_fetch_raw", return_value=_resp(1, categories=[2, 28, 44])):
            c = compat.fetch_categories(1)
        self.assertFalse(c.error)
        self.assertTrue(c.full_controller)
        self.assertTrue(c.remote_play_together)

    def test_only_controller(self):
        with patch.object(compat, "_fetch_raw", return_value=_resp(1, categories=[28])):
            c = compat.fetch_categories(1)
        self.assertTrue(c.full_controller)
        self.assertFalse(c.remote_play_together)

    def test_unsuccessful_response_is_not_error(self):
        with patch.object(compat, "_fetch_raw", return_value=_resp(1, success=False)):
            c = compat.fetch_categories(1)
        self.assertFalse(c.error)
        self.assertFalse(c.full_controller)
        self.assertFalse(c.remote_play_together)

    def test_retries_on_429_then_succeeds(self):
        err = urllib.error.HTTPError("url", 429, "rate limited", {}, None)
        with patch.object(compat, "_fetch_raw", side_effect=[err, _resp(1, categories=[28, 44])]), \
             patch("steamswap.compat.time.sleep"):
            c = compat.fetch_categories(1, retries=2)
        self.assertFalse(c.error)
        self.assertTrue(c.remote_play_together)

    def test_gives_up_after_retries_exhausted(self):
        err = urllib.error.HTTPError("url", 429, "rate limited", {}, None)
        with patch.object(compat, "_fetch_raw", side_effect=[err, err, err]), \
             patch("steamswap.compat.time.sleep"):
            c = compat.fetch_categories(1, retries=2)
        self.assertTrue(c.error)

    def test_non_429_http_error_does_not_retry(self):
        calls = []

        def raise_404(appid, **_):
            calls.append(appid)
            raise urllib.error.HTTPError("url", 404, "not found", {}, None)

        with patch.object(compat, "_fetch_raw", side_effect=raise_404):
            c = compat.fetch_categories(1, retries=2)
        self.assertTrue(c.error)
        self.assertEqual(len(calls), 1)

    def test_network_error(self):
        with patch.object(compat, "_fetch_raw", side_effect=urllib.error.URLError("sem rede")):
            c = compat.fetch_categories(1)
        self.assertTrue(c.error)

    def test_malformed_response(self):
        with patch.object(compat, "_fetch_raw", return_value={"999": {"success": True}}):
            c = compat.fetch_categories(1)  # chave "1" ausente
        self.assertTrue(c.error)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["STEAMSWAP_HOME"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("STEAMSWAP_HOME", None)
        self._tmp.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(compat.load_cache(), {})

    def test_roundtrip(self):
        cache = {1091500: compat.Compat(frozenset({28, 44}), time.time())}
        compat.save_cache(cache)
        loaded = compat.load_cache()
        self.assertEqual(loaded[1091500].categories, frozenset({28, 44}))
        self.assertFalse(loaded[1091500].error)

    def test_corrupted_file_returns_empty(self):
        compat.cache_file().parent.mkdir(parents=True, exist_ok=True)
        compat.cache_file().write_text("{not json", encoding="utf-8")
        self.assertEqual(compat.load_cache(), {})

    def test_stale_detection(self):
        old = compat.Compat(frozenset(), time.time() - (compat.CACHE_TTL_DAYS + 1) * 86400)
        fresh = compat.Compat(frozenset(), time.time())
        self.assertTrue(old.stale)
        self.assertFalse(fresh.stale)


class RefreshManyTests(unittest.TestCase):
    def test_skips_fresh_cached_entries(self):
        cache = {1: compat.Compat(frozenset({28, 44}), time.time())}
        calls = []
        with patch.object(compat, "fetch_categories",
                          side_effect=lambda a, **_: calls.append(a) or compat.Compat(frozenset(), time.time())):
            compat.refresh_many([1, 2], cache, pace=0)
        self.assertEqual(calls, [2])
        self.assertIn(2, cache)

    def test_force_refetches_everything(self):
        cache = {1: compat.Compat(frozenset({28, 44}), time.time())}
        calls = []
        with patch.object(compat, "fetch_categories",
                          side_effect=lambda a, **_: calls.append(a) or compat.Compat(frozenset(), time.time())):
            compat.refresh_many([1, 2], cache, force=True, pace=0)
        self.assertEqual(sorted(calls), [1, 2])

    def test_on_result_and_should_stop(self):
        progress = []
        with patch.object(compat, "fetch_categories", side_effect=lambda a, **_: compat.Compat(frozenset(), time.time())):
            compat.refresh_many([1, 2, 3], {}, pace=0,
                                on_result=lambda appid, c, i, total: progress.append((appid, i, total)),
                                should_stop=lambda: len(progress) >= 2)
        self.assertEqual(progress, [(1, 1, 3), (2, 2, 3)])


if __name__ == "__main__":
    unittest.main()
