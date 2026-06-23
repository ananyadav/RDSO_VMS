import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.video_live_hls import SubscribeResult, batch_subscribe


class TestBatchSubscribe(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.video_live_hls.BATCH_SIZE", 2)
    @patch("app.services.video_live_hls.BATCH_DELAY_SEC", 0.01)
    @patch("app.services.video_live_hls.subscribe", new_callable=AsyncMock)
    async def test_batch_stagger_and_summarize(self, mock_subscribe):
        mock_subscribe.side_effect = [
            SubscribeResult(ok=True, reused=False),
            SubscribeResult(ok=True, reused=True),
            SubscribeResult(ok=False, error="offline"),
            SubscribeResult(ok=True, reused=False),
        ]

        with patch("app.services.video_live_hls.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await batch_subscribe(
                ["cam1", "cam2", "cam3", "cam4"],
                profile="grid",
            )

        self.assertEqual(result["total"], 4)
        self.assertEqual(result["started"], 2)
        self.assertEqual(result["reused"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(result["results"]), 4)
        mock_sleep.assert_awaited_once()

        self.assertEqual(result["results"][0]["status"], "started")
        self.assertEqual(result["results"][0]["playlistUrl"], "/api/live/cam1/live.m3u8")
        self.assertEqual(result["results"][1]["status"], "reused")
        self.assertEqual(result["results"][2]["status"], "failed")
        self.assertEqual(result["results"][2]["error"], "offline")

    @patch("app.services.video_live_hls.subscribe", new_callable=AsyncMock)
    async def test_skips_fullscreen_ids(self, mock_subscribe):
        mock_subscribe.return_value = SubscribeResult(ok=True, reused=False)
        result = await batch_subscribe(
            ["cam1", "cam1__fullscreen", "cam2"],
            profile="grid",
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(mock_subscribe.await_count, 2)


if __name__ == "__main__":
    unittest.main()
