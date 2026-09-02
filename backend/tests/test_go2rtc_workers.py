"""Tests for go2rtc worker registry helpers and provisioning."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.go2rtc_workers import (
    MAX_CAMERAS_PER_WORKER,
    assign_worker_for_new_camera,
    ensure_workers_for_assigned_cameras,
    needed_workers_for_camera_count,
    normalize_worker_id,
    startup_workers,
    worker_base_url,
    worker_config_path,
    worker_ids_required_for_fleet,
    worker_pm2_name,
    worker_ports,
)


class TestGo2RtcWorkers(unittest.TestCase):
    def test_normalize_worker_id(self):
        self.assertEqual(normalize_worker_id(2), 2)
        self.assertEqual(normalize_worker_id("2"), 2)
        self.assertEqual(normalize_worker_id("worker-1"), 1)
        self.assertEqual(normalize_worker_id("go2rtc-worker-3"), 3)
        self.assertIsNone(normalize_worker_id(""))
        self.assertIsNone(normalize_worker_id(None))

    def test_worker_ports_increment(self):
        self.assertEqual(worker_ports(1), (1984, 8554, 8555))
        self.assertEqual(worker_ports(2), (1985, 8556, 8557))
        self.assertEqual(worker_ports(3), (1986, 8558, 8559))
        self.assertEqual(worker_ports(4), (1987, 8560, 8561))
        self.assertEqual(worker_ports(5), (1988, 8562, 8563))

    def test_worker_paths_and_names(self):
        self.assertEqual(worker_pm2_name(2), "go2rtc-worker-2")
        self.assertEqual(worker_pm2_name(4), "go2rtc-worker-4")
        self.assertTrue(
            str(worker_config_path(2)).replace("\\", "/").endswith("go2rtc/workers/2/go2rtc.yaml")
        )
        self.assertEqual(worker_base_url(2), "http://127.0.0.1:1985")
        self.assertEqual(worker_base_url(4), "http://127.0.0.1:1987")
        self.assertEqual(worker_base_url(5), "http://127.0.0.1:1988")

    def test_max_cameras_default(self):
        self.assertEqual(MAX_CAMERAS_PER_WORKER, 300)

    def test_camera_group_key_prefers_stored(self):
        from app.services.camera_locations import camera_group_key_for_document

        cam = {
            "site": "RML - 6",
            "building": "KIPL",
            "floor": "KIPL All",
            "camera_group": "rml_3_kipl_kipl_all",
        }
        self.assertEqual(camera_group_key_for_document(cam), "rml_3_kipl_kipl_all")

    def test_needed_workers_for_camera_count(self):
        self.assertEqual(needed_workers_for_camera_count(0), 0)
        self.assertEqual(needed_workers_for_camera_count(1), 1)
        self.assertEqual(needed_workers_for_camera_count(300), 1)
        self.assertEqual(needed_workers_for_camera_count(301), 2)
        self.assertEqual(needed_workers_for_camera_count(653), 3)
        self.assertEqual(needed_workers_for_camera_count(827), 3)
        self.assertEqual(needed_workers_for_camera_count(900), 3)
        self.assertEqual(needed_workers_for_camera_count(901), 4)
        self.assertEqual(needed_workers_for_camera_count(1200), 4)
        self.assertEqual(needed_workers_for_camera_count(1201), 5)
        self.assertEqual(needed_workers_for_camera_count(1500), 5)

    def test_worker_ids_required_for_fleet(self):
        self.assertEqual(worker_ids_required_for_fleet(827, []), [1, 2, 3])
        self.assertEqual(worker_ids_required_for_fleet(901, []), [1, 2, 3, 4])
        self.assertEqual(worker_ids_required_for_fleet(1201, []), [1, 2, 3, 4, 5])
        self.assertEqual(worker_ids_required_for_fleet(1500, []), [1, 2, 3, 4, 5])
        self.assertEqual(worker_ids_required_for_fleet(901, [1, 2, 3]), [1, 2, 3, 4])
        self.assertEqual(worker_ids_required_for_fleet(650, [4]), [1, 2, 3, 4])

    def test_assign_worker_uses_needed_slot_not_max_plus_one(self):
        """At 901 cameras worker 4 should be used, not worker 5 when worker 4 exists inactive."""
        needed = needed_workers_for_camera_count(901)
        self.assertEqual(needed, 4)
        self.assertEqual((900 // MAX_CAMERAS_PER_WORKER) + 1, 4)

    def test_orphan_worker_outside_needed_range(self):
        needed = needed_workers_for_camera_count(650)
        self.assertEqual(needed, 3)
        valid = set(range(1, needed + 1))
        self.assertNotIn(4, valid)
        # Assigned worker 4 is still provisioned until rebalance clears it.
        self.assertEqual(worker_ids_required_for_fleet(650, [4]), [1, 2, 3, 4])


class TestGo2RtcWorkerProvisioning(unittest.IsolatedAsyncioTestCase):
    async def test_assign_worker_opens_worker_four_at_901_cameras(self):
        workers = [{"worker_id": 1}, {"worker_id": 2}, {"worker_id": 3}]

        async def count_cameras(worker_id: int) -> int:
            return MAX_CAMERAS_PER_WORKER if worker_id <= 3 else 0

        with patch(
            "app.services.go2rtc_workers.list_active_workers",
            new_callable=AsyncMock,
            return_value=workers,
        ), patch(
            "app.services.go2rtc_workers._count_cameras",
            side_effect=count_cameras,
        ), patch(
            "app.services.go2rtc_workers.camera_collection.count_documents",
            new_callable=AsyncMock,
            return_value=900,
        ), patch(
            "app.services.go2rtc_workers.workers_collection.update_one",
            new_callable=AsyncMock,
        ), patch(
            "app.services.go2rtc_workers.get_worker",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.go2rtc_workers.create_worker_record",
            new_callable=AsyncMock,
            return_value={"worker_id": 4},
        ) as create_record, patch(
            "app.services.go2rtc_workers._ensure_worker_records",
            new_callable=AsyncMock,
            return_value=[4],
        ) as ensure_records:
            wid = await assign_worker_for_new_camera()

        self.assertEqual(wid, 4)
        create_record.assert_awaited_once_with(4)
        ensure_records.assert_awaited_once_with([4])

    async def test_assign_worker_opens_worker_five_at_1201_cameras(self):
        workers = [{"worker_id": i} for i in range(1, 5)]

        async def count_cameras(worker_id: int) -> int:
            return MAX_CAMERAS_PER_WORKER if worker_id <= 4 else 0

        with patch(
            "app.services.go2rtc_workers.list_active_workers",
            new_callable=AsyncMock,
            return_value=workers,
        ), patch(
            "app.services.go2rtc_workers._count_cameras",
            side_effect=count_cameras,
        ), patch(
            "app.services.go2rtc_workers.camera_collection.count_documents",
            new_callable=AsyncMock,
            return_value=1200,
        ), patch(
            "app.services.go2rtc_workers.workers_collection.update_one",
            new_callable=AsyncMock,
        ), patch(
            "app.services.go2rtc_workers.get_worker",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.go2rtc_workers.create_worker_record",
            new_callable=AsyncMock,
            return_value={"worker_id": 5},
        ) as create_record, patch(
            "app.services.go2rtc_workers._ensure_worker_records",
            new_callable=AsyncMock,
            return_value=[5],
        ):
            wid = await assign_worker_for_new_camera()

        self.assertEqual(wid, 5)
        create_record.assert_awaited_once_with(5)

    async def test_ensure_workers_provisions_required_slots_from_fleet_size(self):
        async def fake_find(_query, _projection):
            for wid in (1, 2, 3):
                yield {"worker_id": wid}

        cursor = MagicMock()
        cursor.__aiter__ = lambda self: fake_find({}, {})

        with patch(
            "app.services.go2rtc_workers.camera_collection.find",
            return_value=cursor,
        ), patch(
            "app.services.go2rtc_workers.camera_collection.count_documents",
            new_callable=AsyncMock,
            return_value=901,
        ), patch(
            "app.services.go2rtc_workers._ensure_worker_records",
            new_callable=AsyncMock,
            return_value=[4],
        ) as ensure_records:
            result = await ensure_workers_for_assigned_cameras()

        self.assertEqual(result, [1, 2, 3, 4])
        ensure_records.assert_awaited_once_with([1, 2, 3, 4])

    async def test_ensure_workers_restart_recovery_includes_worker_five(self):
        async def fake_find(_query, _projection):
            for wid in (1, 2, 3, 4, 5):
                yield {"worker_id": wid}

        cursor = MagicMock()
        cursor.__aiter__ = lambda self: fake_find({}, {})

        with patch(
            "app.services.go2rtc_workers.camera_collection.find",
            return_value=cursor,
        ), patch(
            "app.services.go2rtc_workers.camera_collection.count_documents",
            new_callable=AsyncMock,
            return_value=1201,
        ), patch(
            "app.services.go2rtc_workers._ensure_worker_records",
            new_callable=AsyncMock,
            return_value=[],
        ) as ensure_records:
            result = await ensure_workers_for_assigned_cameras()

        self.assertEqual(result, [1, 2, 3, 4, 5])
        ensure_records.assert_awaited_once_with([1, 2, 3, 4, 5])

    async def test_startup_workers_calls_fleet_recovery_before_sync(self):
        with patch(
            "app.services.go2rtc_workers.WORKERS_ENABLED",
            True,
        ), patch(
            "app.services.go2rtc_workers.ensure_workers_indexes",
            new_callable=AsyncMock,
        ), patch(
            "app.services.go2rtc_workers.migrate_cameras_without_worker",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.services.go2rtc_workers.rebalance_if_needed",
            new_callable=AsyncMock,
            return_value={"ok": True, "rebalanced": False},
        ), patch(
            "app.services.go2rtc_workers.ensure_workers_for_assigned_cameras",
            new_callable=AsyncMock,
            return_value=[1, 2, 3, 4],
        ) as ensure_assigned, patch(
            "app.services.go2rtc_workers.stop_legacy_monolithic_go2rtc",
            new_callable=AsyncMock,
        ), patch(
            "app.services.go2rtc_workers.sync_all_workers",
            new_callable=AsyncMock,
            return_value={"ok": True, "workers": [], "workerCount": 4},
        ) as sync_all, patch(
            "app.services.go2rtc_workers.list_active_workers",
            new_callable=AsyncMock,
            return_value=[{"worker_id": 1}],
        ), patch(
            "app.services.go2rtc_workers.start_worker_watchdog",
        ):
            result = await startup_workers()

        ensure_assigned.assert_awaited_once()
        sync_all.assert_awaited_once()
        self.assertTrue(result.get("ok"))


if __name__ == "__main__":
    unittest.main()
