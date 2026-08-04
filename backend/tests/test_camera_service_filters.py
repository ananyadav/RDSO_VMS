import unittest

from app.services.camera_locations import (
    DEFAULT_SITE_NAME,
    location_fields_for_building_floor,
)
from app.services.camera_service import _location_filters, _site_scope_or_clauses


class TestCameraServiceSiteFilters(unittest.TestCase):
    def test_site_scope_matches_camera_group_prefix_when_site_field_empty(self):
        meta = location_fields_for_building_floor("North Plant", "Warehouse A", "1st Floor")
        clauses = _site_scope_or_clauses("North Plant")
        self.assertTrue(
            any(
                c.get("camera_group", {}).get("$regex", "").startswith("^north_plant_")
                for c in clauses
                if isinstance(c.get("camera_group"), dict)
            )
        )
        query = _location_filters({"site": "North Plant"}, floor_meta={meta["camera_group"]: meta})
        self.assertIn("$and", query)
        or_block = query["$and"][0]["$or"]
        groups = [c.get("camera_group") for c in or_block if "camera_group" in c]
        self.assertIn("north_plant_warehouse_a_1st_floor", groups)

    def test_site_scope_case_insensitive_on_site_field(self):
        clauses = _site_scope_or_clauses("bl3")
        site_clause = next(c for c in clauses if "site" in c)
        self.assertEqual(site_clause["site"]["$options"], "i")

    def test_site_only_filter_does_not_use_exact_site_match(self):
        query = _location_filters({"site": "ISP"})
        self.assertNotEqual(query.get("site"), "ISP")
        self.assertIn("$and", query)

    def test_building_plus_site_keeps_regex_site_not_overwritten(self):
        query = _location_filters({"site": DEFAULT_SITE_NAME, "building": "Corporate Office"})
        self.assertIn("site", query)
        self.assertIsInstance(query["site"], dict)
        self.assertIn("$regex", query["site"])


if __name__ == "__main__":
    unittest.main()
