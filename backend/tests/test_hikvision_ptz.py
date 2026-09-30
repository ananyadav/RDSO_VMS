import unittest

from app.services.hikvision_ptz import (
    _direction_to_velocity,
    _parse_patrols_xml,
    _parse_patterns_xml,
    _parse_presets_xml,
    _patrol_set_xml,
    _pattern_set_xml,
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

    def test_parse_patrols(self):
        sample = """<?xml version="1.0" encoding="UTF-8"?>
        <PTZPatrolList>
          <PTZPatrol>
            <enabled>true</enabled>
            <id>1</id>
            <patrolName>Lobby</patrolName>
            <PatrolSequenceList>
              <PatrolSequence>
                <presetID>2</presetID>
                <seq>1</seq>
                <delay>8</delay>
                <speed>40</speed>
              </PatrolSequence>
              <PatrolSequence>
                <presetID>3</presetID>
                <seq>2</seq>
                <delay>5</delay>
                <speed>30</speed>
              </PatrolSequence>
            </PatrolSequenceList>
          </PTZPatrol>
        </PTZPatrolList>"""
        tours = _parse_patrols_xml(sample)
        self.assertEqual(len(tours), 1)
        self.assertEqual(tours[0]["id"], 1)
        self.assertEqual(tours[0]["name"], "Lobby")
        self.assertEqual(len(tours[0]["steps"]), 2)
        self.assertEqual(tours[0]["steps"][0]["presetId"], 2)
        self.assertEqual(tours[0]["steps"][0]["delay"], 8)

    def test_patrol_set_xml(self):
        xml = _patrol_set_xml(
            1,
            "Tour A",
            [{"presetId": 2, "delay": 5, "speed": 30}, {"presetId": 4, "delay": 7}],
        ).decode()
        self.assertIn("<id>1</id>", xml)
        self.assertIn("<patrolName>Tour A</patrolName>", xml)
        self.assertIn("<presetID>2</presetID>", xml)
        self.assertIn("<presetID>4</presetID>", xml)
        self.assertIn("<seqSpeed>", xml)
        # Hikvision rejects short dwell; delay is clamped to >= 15
        self.assertIn("<delay>15</delay>", xml)
        # Empty slots padded
        self.assertGreaterEqual(xml.count("<PatrolSequence>"), 8)

    def test_parse_patterns_distinct_from_patrols(self):
        sample = """<?xml version="1.0" encoding="UTF-8"?>
        <PTZPatternList>
          <PTZPattern>
            <id>2</id>
            <patternName>Sweep</patternName>
            <enabled>true</enabled>
          </PTZPattern>
        </PTZPatternList>"""
        patterns = _parse_patterns_xml(sample)
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["id"], 2)
        self.assertEqual(patterns[0]["name"], "Sweep")
        # Must not be confused with patrol XML
        self.assertEqual(_parse_patrols_xml(sample), [])

    def test_pattern_set_xml(self):
        xml = _pattern_set_xml(3, "Gate Path").decode()
        self.assertIn("<id>3</id>", xml)
        self.assertIn("<patternName>Gate Path</patternName>", xml)
        self.assertIn("PTZPattern", xml)


if __name__ == "__main__":
    unittest.main()
