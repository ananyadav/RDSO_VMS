import unittest

from app.services.hikvision_ptz import (
    _direction_to_velocity,
    _parse_presets_xml,
    _ptz_xml,
)


class TestHikvisionPtz(unittest.TestCase):
    def test_direction_velocities(self):
        self.assertEqual(_direction_to_velocity("right", 2), (60, 0, 0))
        self.assertEqual(_direction_to_velocity("up", 3), (0, 90, 0))
        self.assertEqual(_direction_to_velocity("zoom_in", 1), (0, 0, 35))

    def test_ptz_xml(self):
        xml = _ptz_xml(10, -20, 0).decode()
        self.assertIn("<pan>10</pan>", xml)
        self.assertIn("<tilt>-20</tilt>", xml)

    def test_parse_presets(self):
        sample = """<?xml version="1.0" encoding="UTF-8"?>
        <PTZPresetList>
          <PTZPreset>
            <id>2</id>
            <presetName>Entrance</presetName>
            <enabled>true</enabled>
          </PTZPreset>
        </PTZPresetList>"""
        presets = _parse_presets_xml(sample)
        self.assertEqual(len(presets), 1)
        self.assertEqual(presets[0]["id"], 2)
        self.assertEqual(presets[0]["name"], "Entrance")


if __name__ == "__main__":
    unittest.main()
