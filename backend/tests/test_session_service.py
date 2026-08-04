import unittest
from datetime import datetime, timedelta, timezone

from app.services.session_service import _as_utc


class TestSessionDatetime(unittest.TestCase):
    def test_naive_datetime_becomes_utc(self):
        naive = datetime(2026, 1, 1, 12, 0, 0)
        aware = _as_utc(naive)
        self.assertIsNotNone(aware)
        self.assertEqual(aware.tzinfo, timezone.utc)

    def test_aware_datetime_compares_with_utcnow(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertTrue(_as_utc(future) > datetime.now(timezone.utc))
        self.assertTrue(_as_utc(past) < datetime.now(timezone.utc))

    def test_iso_string_parsed(self):
        parsed = _as_utc("2026-07-22T09:11:58.171652+00:00")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)


class TestCameraBulkImportHelpers(unittest.TestCase):
    def test_find_existing_prefers_uid(self):
        from app.services.camera_bulk_import import _find_existing

        by_uid = {"uid-1": {"_id": "a", "camera_uid": "uid-1"}}
        by_ip = {"10.0.0.1": {"_id": "b", "ip_address": "10.0.0.1"}}
        found = _find_existing(by_uid, by_ip, "uid-1", "10.0.0.2")
        self.assertEqual(found["_id"], "a")

    def test_find_existing_falls_back_to_ip(self):
        from app.services.camera_bulk_import import _find_existing

        by_uid: dict = {}
        by_ip = {"10.0.0.1": {"_id": "b", "ip_address": "10.0.0.1"}}
        found = _find_existing(by_uid, by_ip, "uid-missing", "10.0.0.1")
        self.assertEqual(found["_id"], "b")


if __name__ == "__main__":
    unittest.main()
