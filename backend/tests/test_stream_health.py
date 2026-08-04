import unittest

from app.services.camera_management import stream_online
from app.services.stream_health import (
    _classify_frame_response,
    finalize_probe_result,
    get_stream_health,
    record_stream_health,
    reset_stream_health_for_tests,
    stream_health_snapshot,
)
from app.services.stream_issues import stream_issue_from_row


class TestStreamHealth(unittest.TestCase):
    def tearDown(self):
        reset_stream_health_for_tests()

    def test_healthy_frame(self):
        ok, category, message = _classify_frame_response(200, b"x" * 1001)
        self.assertTrue(ok)
        self.assertEqual(category, "online")
        self.assertEqual(message, "")

    def test_auth_failure_is_classified(self):
        ok, category, message = _classify_frame_response(
            500, b"streams: wrong user/pass"
        )
        self.assertFalse(ok)
        self.assertEqual(category, "wrong_password")
        self.assertIn("wrong user", message)

    def test_missing_stream_is_classified(self):
        ok, category, message = _classify_frame_response(404, b"")
        self.assertFalse(ok)
        self.assertEqual(category, "missing_url")
        self.assertIn("not registered", message)

    def test_empty_frame_is_offline(self):
        ok, category, message = _classify_frame_response(200, b"")
        self.assertFalse(ok)
        self.assertEqual(category, "offline")
        self.assertIn("No video frame", message)

    def test_timeout_needs_three_strikes_before_alarm(self):
        first = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "timeout",
                "message": "Stream probe timed out",
                "checkedAt": "t1",
            }
        )
        self.assertFalse(first["alarm"])
        self.assertTrue(first["suspect"])
        self.assertEqual(first["strikes"], 1)

        second = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "timeout",
                "message": "Stream probe timed out",
                "checkedAt": "t2",
            },
            previous=first,
        )
        self.assertFalse(second["alarm"])
        self.assertTrue(second["suspect"])
        self.assertEqual(second["strikes"], 2)

        third = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "timeout",
                "message": "Stream probe timed out",
                "checkedAt": "t3",
            },
            previous=second,
        )
        self.assertTrue(third["alarm"])
        self.assertFalse(third["suspect"])
        self.assertEqual(third["strikes"], 3)

    def test_transient_categories_share_strike_bucket(self):
        first = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "timeout",
                "message": "timeout",
                "checkedAt": "t1",
            }
        )
        second = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "offline",
                "message": "No video frame",
                "checkedAt": "t2",
            },
            previous=first,
        )
        self.assertFalse(second["alarm"])
        self.assertEqual(second["strikes"], 2)
        third = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "timeout",
                "message": "timeout",
                "checkedAt": "t3",
            },
            previous=second,
        )
        self.assertTrue(third["alarm"])
        self.assertEqual(third["strikes"], 3)

    def test_auth_failure_alarms_immediately(self):
        result = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "wrong_password",
                "message": "wrong user/pass",
                "checkedAt": "t1",
            }
        )
        self.assertTrue(result["alarm"])
        self.assertEqual(result["strikes"], 1)

    def test_success_clears_strikes(self):
        failed = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": False,
                "category": "timeout",
                "message": "timeout",
                "checkedAt": "t1",
            }
        )
        ok = finalize_probe_result(
            {
                "cameraId": "c1",
                "ok": True,
                "category": "online",
                "message": "",
                "checkedAt": "t2",
            },
            previous=failed,
        )
        self.assertTrue(ok["ok"])
        self.assertFalse(ok["alarm"])
        self.assertEqual(ok["strikes"], 0)

    def test_manual_test_result_is_cached_by_id_and_uid(self):
        camera = {
            "_id": "camera-1",
            "camera_uid": "ip_10_0_0_1",
            "ip_address": "10.0.0.1",
        }
        result = record_stream_health(
            camera,
            ok=False,
            message="Stream test timed out",
            category="timeout",
        )
        self.assertTrue(result["alarm"])
        self.assertEqual(get_stream_health("camera-1")["category"], "timeout")
        self.assertEqual(
            get_stream_health("", "ip_10_0_0_1")["message"],
            "Stream test timed out",
        )
        self.assertEqual(stream_health_snapshot()["cachedResults"], 1)

    def test_confirmed_health_controls_management_online_state(self):
        from app.services.camera_management import (
            apply_stream_online_status,
            stream_confirmed_offline,
        )

        camera = {"is_active": True}
        self.assertFalse(stream_online(camera, {"streamRegistered": True}))
        self.assertFalse(
            stream_online(
                camera,
                {
                    "streamRegistered": True,
                    "issueCategory": "timeout",
                },
            )
        )
        self.assertTrue(
            stream_online(
                camera,
                {
                    "streamRegistered": True,
                    "issueCategory": "online",
                },
            )
        )
        self.assertTrue(
            stream_confirmed_offline(
                camera,
                {"issueCategory": "timeout", "confirmedOffline": True},
            )
        )
        self.assertFalse(
            stream_confirmed_offline(
                camera,
                {"issueCategory": "timeout", "confirmedOffline": False},
            )
        )
        items = [{"id": "a", "is_active": True}]
        apply_stream_online_status(
            items,
            {"a": {"issueCategory": "timeout", "confirmedOffline": True}},
        )
        self.assertEqual(items[0]["liveStatus"], "offline")
        self.assertTrue(items[0]["confirmedOffline"])
        self.assertTrue(items[0]["alertEligible"])
        items2 = [{"id": "b", "is_active": True}]
        apply_stream_online_status(
            items2,
            {"b": {"issueCategory": "unchecked"}},
        )
        self.assertEqual(items2[0]["liveStatus"], "online")
        self.assertFalse(items2[0]["confirmedOffline"])
        self.assertFalse(items2[0]["alertEligible"])

    def test_producer_error_wins_over_registered_state(self):
        category, message = stream_issue_from_row(
            sub_online=True,
            main_online=False,
            sub_producers=[{"error": "401 Unauthorized"}],
            main_producers=[],
            stream_registered=True,
        )
        self.assertEqual(category, "wrong_password")
        self.assertEqual(message, "401 Unauthorized")

    def test_transient_producer_timeout_stays_checking(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[{"error": "connection timed out"}],
            main_producers=[],
            stream_registered=True,
        )
        self.assertEqual(category, "unchecked")
        self.assertIn("timed out", message.lower())

    def test_health_alarm_confirms_timeout(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[{"error": "connection timed out"}],
            main_producers=[],
            stream_registered=True,
            health={
                "ok": False,
                "alarm": True,
                "category": "timeout",
                "message": "Stream probe timed out",
            },
        )
        self.assertEqual(category, "timeout")
        self.assertIn("timed out", message.lower())

    def test_zombie_producer_without_media_is_unchecked_when_no_health(self):
        category, message = stream_issue_from_row(
            sub_online=True,
            main_online=False,
            sub_producers=[{"url": "rtsp://cam/sub"}],
            main_producers=[],
            stream_registered=True,
        )
        self.assertEqual(category, "unchecked")
        self.assertEqual(message, "")

    def test_zombie_producer_with_failed_health_is_offline(self):
        category, message = stream_issue_from_row(
            sub_online=True,
            main_online=False,
            sub_producers=[{"url": "rtsp://cam/sub"}],
            main_producers=[],
            stream_registered=True,
            health={
                "ok": False,
                "alarm": True,
                "category": "timeout",
                "message": "Stream probe timed out",
            },
        )
        self.assertEqual(category, "timeout")
        self.assertIn("timed out", message.lower())

    def test_producer_with_medias_is_online(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[{"url": "rtsp://cam/sub", "medias": ["video, recvonly, H264"]}],
            main_producers=[],
            stream_registered=True,
        )
        self.assertEqual(category, "online")
        self.assertEqual(message, "")

    def test_registered_without_producer_is_unchecked(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[],
            main_producers=[],
            stream_registered=True,
        )
        self.assertEqual(category, "unchecked")
        self.assertEqual(message, "")

    def test_health_ok_is_online_without_active_producer(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[],
            main_producers=[],
            stream_registered=True,
            health={"ok": True, "category": "online", "message": ""},
        )
        self.assertEqual(category, "online")
        self.assertEqual(message, "")

    def test_unregistered_is_checking_until_health(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[],
            main_producers=[],
            stream_registered=False,
        )
        self.assertEqual(category, "unchecked")
        self.assertIn("not registered", message.lower())

    def test_worker_down_is_offline(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[],
            main_producers=[],
            stream_registered=True,
            worker_running=False,
            worker_id=2,
        )
        self.assertEqual(category, "offline")
        self.assertIn("worker 2", message.lower())

    def test_health_suspect_stays_unchecked(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[],
            main_producers=[],
            stream_registered=True,
            health={
                "ok": False,
                "alarm": False,
                "suspect": True,
                "category": "timeout",
                "message": "Stream probe timed out",
            },
        )
        self.assertEqual(category, "unchecked")
        # Message may surface for diagnostics; must not be alertable offline.
        self.assertIn("timed out", message.lower())

    def test_health_failure_is_offline_when_alarm_confirmed(self):
        category, message = stream_issue_from_row(
            sub_online=False,
            main_online=False,
            sub_producers=[],
            main_producers=[],
            stream_registered=True,
            health={
                "ok": False,
                "alarm": True,
                "suspect": False,
                "category": "timeout",
                "message": "Stream probe timed out",
            },
        )
        self.assertEqual(category, "timeout")
        self.assertIn("timed out", message.lower())


class TestStreamIssueClassify(unittest.TestCase):
    def test_truncated_wrong_is_auth(self):
        from app.services.stream_issues import classify_stream_error

        self.assertEqual(classify_stream_error("mse: streams: wrong"), "wrong_password")

    def test_eof_setup_is_path_issue(self):
        from app.services.stream_issues import classify_stream_error

        self.assertEqual(
            classify_stream_error("webrtc/offer: streams: EOF response on SETUP"),
            "missing_url",
        )

    def test_453_bandwidth_is_other_not_path(self):
        from app.services.stream_issues import classify_stream_error

        self.assertEqual(
            classify_stream_error("method SETUP failed: 453 (Not Enough Bandwidth)"),
            "other",
        )


if __name__ == "__main__":
    unittest.main()
