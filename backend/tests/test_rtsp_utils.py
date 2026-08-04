import unittest

from app.services.rtsp_utils import (
    apply_rtsp_urls,
    build_camera_rtsp_urls,
    build_rtsp_url,
    build_rtsp_urls,
    effective_camera_rtsp_urls,
    mask_rtsp_url,
    normalize_make,
    rewrite_rtsp_credentials,
    rtsp_url_credentials_stale,
    stream_source_urls,
    sync_camera_rtsp_urls,
)


class TestBuildRtspUrls(unittest.TestCase):
    def _base(self, **kwargs):
        defaults = {
            "make": "HIKVISION",
            "ip": "192.168.1.10",
            "username": "admin",
            "password": "pass",
            "port": 554,
        }
        defaults.update(kwargs)
        return build_rtsp_urls(**defaults)

    def test_hikvision_and_prama(self):
        hik = self._base(make="HIKVISION")
        self.assertIn("/Streaming/Channels/101", hik["main_rtsp_url"])
        self.assertIn("/Streaming/Channels/102", hik["sub_rtsp_url"])
        self.assertEqual(hik["main_channel"], "101")
        self.assertEqual(hik["sub_channel"], "102")
        self.assertEqual(hik["rtsp_source"], "auto_hikvision")

        prama = self._base(make="PRAMA")
        self.assertIn("/Streaming/Channels/101", prama["main_rtsp_url"])
        self.assertEqual(prama["rtsp_source"], "auto_prama")

    def test_dahua(self):
        built = self._base(make="DAHUA")
        self.assertIn("/cam/realmonitor?channel=1&subtype=0", built["main_rtsp_url"])
        self.assertIn("/cam/realmonitor?channel=1&subtype=1", built["sub_rtsp_url"])

    def test_uniview_aliases(self):
        built = self._base(make="UNV")
        self.assertIn("/media/video1", built["main_rtsp_url"])
        self.assertIn("/media/video2", built["sub_rtsp_url"])
        self.assertEqual(built["rtsp_source"], "auto_uniview")
        paths = "".join(built["fallback_urls"])
        self.assertIn("/media/video3", paths)

    def test_prama_go2rtc_sources_transcode_h264(self):
        cam = apply_rtsp_urls(
            {
                "protocol": "PRAMA",
                "ip_address": "10.0.0.22",
                "username": "admin",
                "password": "secret",
            }
        )
        sub = stream_source_urls(cam, main=False)
        main = stream_source_urls(cam, main=True)
        self.assertTrue(sub[0].startswith("ffmpeg:"))
        self.assertIn("/Streaming/Channels/102", sub[0])
        self.assertIn("#video=h264", sub[0])
        self.assertTrue(main[0].startswith("ffmpeg:"))
        self.assertIn("/Streaming/Channels/101", main[0])

    def test_main_only_transcode_does_not_wrap_substream(self):
        cam = {
            "protocol": "SPARSH",
            "ip_address": "10.0.0.51",
            "username": "admin",
            "password": "secret",
            "main_rtsp_url": "rtsp://admin:secret@10.0.0.51:554/ch01.264?ptype=tcp&dev=1",
            "sub_rtsp_url": "rtsp://admin:secret@10.0.0.51:554/ch01_sub.264?ptype=tcp&dev=1",
            "go2rtc_transcode_main_h264": True,
        }
        sub = stream_source_urls(cam, main=False)
        main = stream_source_urls(cam, main=True)
        self.assertFalse(sub[0].startswith("ffmpeg:"))
        self.assertTrue(main[0].startswith("ffmpeg:"))

    def test_stream_source_urls_rebuilds_missing_fallbacks(self):
        # DB-inserted SPARSH without rtsp_fallback_urls still gets brand fallbacks.
        cam = {
            "protocol": "SPARSH",
            "ip_address": "10.0.0.50",
            "username": "admin",
            "password": "secret",
            "main_rtsp_url": "rtsp://admin:secret@10.0.0.50:554/ch01.264?ptype=tcp&dev=1",
            "sub_rtsp_url": "rtsp://admin:secret@10.0.0.50:554/ch01_sub.264?ptype=tcp&dev=1",
        }
        sources = stream_source_urls(cam, main=False)
        self.assertGreaterEqual(len(sources), 2)
        self.assertTrue(any(u.startswith("onvif://") for u in sources))
        self.assertTrue(any("/cam/realmonitor" in u for u in sources))

    def test_hikvision_ignores_stale_cross_brand_fallbacks(self):
        cam = {
            "protocol": "HIKVISION",
            "ip_address": "10.0.0.44",
            "username": "admin",
            "password": "secret",
            "main_rtsp_url": "rtsp://admin:secret@10.0.0.44:554/Streaming/Channels/101",
            "sub_rtsp_url": "rtsp://admin:secret@10.0.0.44:554/Streaming/Channels/102",
            "rtsp_fallback_urls": [
                "onvif://admin:secret@10.0.0.44",
                "rtsp://admin:secret@10.0.0.44:554/cam/realmonitor?channel=1&subtype=0",
            ],
        }
        sources = stream_source_urls(cam, main=False)
        self.assertEqual(len(sources), 1)
        self.assertIn("/Streaming/Channels/102", sources[0])
        self.assertFalse(any(u.startswith("onvif://") for u in sources))

    def test_uniview_go2rtc_sources_transcode_h264(self):
        from app.services.rtsp_utils import stream_source_urls

        cam = apply_rtsp_urls(
            {
                "protocol": "UNIVIEW",
                "ip_address": "10.0.0.76",
                "username": "admin",
                "password": "secret",
            }
        )
        sub = stream_source_urls(cam, main=False)
        main = stream_source_urls(cam, main=True)
        self.assertTrue(sub[0].startswith("ffmpeg:"))
        self.assertIn("/media/video2", sub[0])
        self.assertIn("#video=h264", sub[0])
        self.assertTrue(main[0].startswith("ffmpeg:"))
        self.assertIn("/media/video1", main[0])
        # H.264 tertiary fallback stays unwrapped
        self.assertTrue(any(u.endswith("/media/video3") and not u.startswith("ffmpeg:") for u in sub))

    def test_vivotek_fallbacks(self):
        built = self._base(make="VIVOTEK")
        self.assertIn("/live1s1.sdp", built["main_rtsp_url"])
        self.assertIn("/live1s2.sdp", built["sub_rtsp_url"])
        self.assertTrue(any("/live.sdp" in u for u in built["fallback_urls"]))

    def test_honeywell_fallbacks(self):
        built = self._base(make="HONEYWELL")
        self.assertIn("/rtsp/streaming?channel=01&subtype=A", built["main_rtsp_url"])
        self.assertIn("/rtsp/streaming?channel=01&subtype=B", built["sub_rtsp_url"])
        paths = "".join(built["fallback_urls"])
        self.assertIn("/h264", paths)
        self.assertIn("/cam1/h264", paths)
        self.assertIn("/PSIA/Streaming/channels/1", paths)

    def test_sparsh_native_paths_with_onvif_fallback(self):
        built = self._base(make="SPARSH")
        self.assertIn("/ch01.264?ptype=tcp&dev=1", built["main_rtsp_url"])
        self.assertIn("/ch01_sub.264?ptype=tcp&dev=1", built["sub_rtsp_url"])
        self.assertTrue(built["main_rtsp_url"].startswith("rtsp://"))
        self.assertTrue(any(u.startswith("onvif://") for u in built["fallback_urls"]))

    def test_apply_rtsp_urls_sets_ip_name(self):
        doc = apply_rtsp_urls(
            {
                "protocol": "DAHUA",
                "ip_address": "10.0.0.8",
                "username": "admin",
                "password": "secret",
                "port": 554,
            }
        )
        self.assertEqual(doc["name"], "10.0.0.8")
        self.assertEqual(doc["display_name"], "10.0.0.8")
        self.assertEqual(doc["rtsp_url_source"], "auto_dahua")

    def test_stream_source_urls_include_fallbacks(self):
        cam = apply_rtsp_urls(
            {
                "protocol": "VIVOTEK",
                "ip_address": "10.0.0.9",
                "username": "admin",
                "password": "secret",
            }
        )
        sub_sources = stream_source_urls(cam, main=False)
        self.assertGreater(len(sub_sources), 1)


class TestRtspUtils(unittest.TestCase):
    def test_hikvision_sub_and_main(self):
        sub = build_rtsp_url(
            ip_address="192.168.1.10",
            port=554,
            username="admin",
            password="pass",
            model="Hikvision",
            channel="102",
            main=False,
        )
        main = build_rtsp_url(
            ip_address="192.168.1.10",
            port=554,
            username="admin",
            password="pass",
            model="Hikvision",
            channel="101",
            main=True,
        )
        self.assertIn("/Streaming/Channels/102", sub)
        self.assertIn("/Streaming/Channels/101", main)

    def test_build_camera_rtsp_urls(self):
        urls = build_camera_rtsp_urls({
            "ip_address": "10.0.0.5",
            "port": 554,
            "username": "admin",
            "password": "secret",
            "protocol": "HIKVISION",
        })
        self.assertIn("main_rtsp_url", urls)
        self.assertIn("sub_rtsp_url", urls)

    def test_effective_custom_rtsp_urls(self):
        urls = effective_camera_rtsp_urls({
            "protocol": "CUSTOM",
            "username": "admin",
            "password": "newpass",
            "sub_rtsp_url": "rtsp://admin:oldpass@10.0.0.5:554/ch01/sub/av_stream",
            "main_rtsp_url": "rtsp://admin:oldpass@10.0.0.5:554/11",
        })
        self.assertIn("newpass", urls["sub_rtsp_url"])
        self.assertIn("newpass", urls["main_rtsp_url"])

    def test_rtsp_url_credentials_stale(self):
        cam = {
            "username": "admin",
            "password": "Rashmi@432",
            "sub_rtsp_url": "rtsp://admin:Corp%232024@192.168.46.12:554/Streaming/Channels/102",
        }
        self.assertTrue(rtsp_url_credentials_stale(cam))

    def test_sync_camera_rtsp_urls_hikvision(self):
        synced = sync_camera_rtsp_urls({
            "protocol": "HIKVISION",
            "ip_address": "192.168.46.12",
            "username": "admin",
            "password": "Rashmi@432",
            "sub_rtsp_url": "rtsp://admin:Corp%232024@192.168.46.12:554/Streaming/Channels/102",
        })
        self.assertIn("Rashmi%40432", synced["sub_rtsp_url"])
        self.assertNotIn("Corp%232024", synced["sub_rtsp_url"])

    def test_rewrite_rtsp_credentials(self):
        out = rewrite_rtsp_credentials(
            "rtsp://admin:old@10.0.0.5:554/path",
            "admin",
            "new@pass",
        )
        self.assertIn("new%40pass", out)
        self.assertIn("10.0.0.5:554/path", out)

    def test_mask_rtsp_url(self):
        masked = mask_rtsp_url("rtsp://admin:secret@10.0.0.5:554/path")
        self.assertNotIn("secret", masked)
        self.assertIn("10.0.0.5", masked)

    def test_normalize_make_aliases(self):
        self.assertEqual(normalize_make("hik"), "HIKVISION")
        self.assertEqual(normalize_make("unv"), "UNIVIEW")


if __name__ == "__main__":
    unittest.main()
