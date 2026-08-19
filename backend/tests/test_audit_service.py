import unittest
from unittest.mock import AsyncMock, patch

from app.services.audit_service import (
    AuditWriteError,
    commit_critical_audit,
    query_audit_logs,
    redact_value,
    sanitize_changes,
    sanitize_metadata,
    write_audit,
)


class TestAuditRedaction(unittest.TestCase):
    def test_password_fields_redacted(self):
        changes = sanitize_changes(
            {
                "camera_password": {"before": "secret1", "after": "secret2"},
                "password": {"before": "hash-a", "after": "hash-b"},
            }
        )
        self.assertEqual(changes["camera_password"]["before"], "[REDACTED]")
        self.assertEqual(changes["camera_password"]["after"], "[REDACTED]")
        self.assertEqual(changes["password"]["before"], "[REDACTED]")
        self.assertNotIn("secret1", str(changes))
        self.assertNotIn("hash-a", str(changes))

    def test_password_changed_flag_preserved(self):
        changes = sanitize_changes({"password_changed": True})
        self.assertEqual(changes["password_changed"], True)

    def test_rtsp_credentials_stripped(self):
        url = "rtsp://admin:password@192.168.1.10:554/Streaming/Channels/101"
        redacted = redact_value("main_rtsp_url", url)
        self.assertNotIn("password", redacted)
        self.assertNotIn("admin:password", redacted)
        self.assertIn("192.168.1.10", redacted)

    def test_session_token_redacted(self):
        meta = sanitize_metadata({"nvr_session": "abc123token", "token": "sess-tok"})
        self.assertEqual(meta["nvr_session"], "[REDACTED]")
        self.assertEqual(meta["token"], "[REDACTED]")

    def test_mongo_uri_and_hash_redacted(self):
        self.assertEqual(redact_value("uri", "mongodb+srv://user:pwd@cluster.mongodb.net"), "[REDACTED]")
        self.assertEqual(redact_value("password_hash", "$2b$12$abcdefghijklmnopqrstuv"), "[REDACTED]")
        self.assertEqual(redact_value("blob", b"$2b$12$notahash"), "[REDACTED]")
        meta = sanitize_metadata({"internal_reason": "hidden", "mongodb_uri": "mongodb://localhost"})
        self.assertEqual(meta["internal_reason"], "hidden")
        self.assertEqual(meta["mongodb_uri"], "[REDACTED]")


class _EmptyCursor:
    def __init__(self):
        self.limit_arg = None

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.limit_arg = n
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class TestAuditWriteAndQuery(unittest.IsolatedAsyncioTestCase):
    async def test_write_audit_does_not_raise(self):
        with patch(
            "app.services.audit_service.AUDIT_COLLECTION.insert_one",
            new_callable=AsyncMock,
            side_effect=RuntimeError("mongo down"),
        ):
            ok = await write_audit(action="LOGIN_SUCCESS", actor=None, success=True)
        self.assertFalse(ok)

    async def test_required_write_raises_and_compensate_runs(self):
        with patch(
            "app.services.audit_service.AUDIT_COLLECTION.insert_one",
            new_callable=AsyncMock,
            side_effect=RuntimeError("mongo down"),
        ):
            with self.assertRaises(AuditWriteError):
                await write_audit(action="CAMERA_DELETED", required=True)
            compensate = AsyncMock()
            ok = await commit_critical_audit(
                compensate=compensate,
                action="USER_ROLE_CHANGED",
                actor={"_id": "a1", "role": "SUPER_ADMIN", "name": "root"},
                success=True,
            )
        self.assertFalse(ok)
        compensate.assert_awaited_once()

    async def test_query_limit_capped_at_200(self):
        cursor = _EmptyCursor()
        with patch("app.services.audit_service.AUDIT_COLLECTION") as col:
            col.count_documents = AsyncMock(return_value=0)
            col.find.return_value = cursor
            result = await query_audit_logs(limit=5000, offset=-3)
        self.assertEqual(result["limit"], 200)
        self.assertEqual(result["offset"], 0)
        self.assertEqual(cursor.limit_arg, 200)


if __name__ == "__main__":
    unittest.main()
