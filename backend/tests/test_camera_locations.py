import unittest

from app.services.camera_locations import (
    DEFAULT_SITE_NAME,
    infer_camera_group_from_name,
    location_fields_for_floor,
    location_fields_for_building_floor,
    location_fields_for_group,
    build_groups_hierarchy,
    camera_group_for_floor,
    camera_group_for_building_floor,
    camera_group_key_for_document,
    CORPORATE_OFFICE,
    CORPORATE_OFFICE_FLOORS,
)
from app.services.camera_access import (
    normalize_camera_access,
    build_access_filter,
    active_camera_filter,
)


class TestCameraLocations(unittest.TestCase):
    def test_corporate_floor_list(self):
        self.assertEqual(CORPORATE_OFFICE_FLOORS[0], "Ground Floor")
        self.assertEqual(CORPORATE_OFFICE_FLOORS[-1], "7th Floor")
        self.assertEqual(len(CORPORATE_OFFICE_FLOORS), 8)

    def test_camera_group_for_floor(self):
        self.assertEqual(
            camera_group_for_floor("Ground Floor"),
            "rml_6_corporate_office_ground_floor",
        )
        self.assertEqual(
            camera_group_for_floor("6th Floor"),
            "rml_6_corporate_office_6th_floor",
        )

    def test_camera_group_for_other_building(self):
        self.assertEqual(
            camera_group_for_building_floor("Warehouse A", "Ground Floor", site="North Plant"),
            "north_plant_warehouse_a_ground_floor",
        )

    def test_location_fields_for_floor_ground(self):
        fields = location_fields_for_floor("Ground Floor")
        self.assertEqual(fields["camera_group"], "rml_6_corporate_office_ground_floor")
        self.assertEqual(fields["location_path"], "RML - 6 / Corporate Office / Ground Floor")
        self.assertEqual(fields["site"], DEFAULT_SITE_NAME)

    def test_location_fields_for_building_floor(self):
        fields = location_fields_for_building_floor("North Plant", "Warehouse A", "1st Floor")
        self.assertEqual(fields["camera_group"], "north_plant_warehouse_a_1st_floor")
        self.assertEqual(fields["location_path"], "North Plant / Warehouse A / 1st Floor")

    def test_cam1_to_13_sixth_floor(self):
        self.assertEqual(infer_camera_group_from_name("Cam1"), "rml_6_corporate_office_6th_floor")
        self.assertEqual(infer_camera_group_from_name("Cam13"), "rml_6_corporate_office_6th_floor")

    def test_cam14_to_23_seventh_floor(self):
        self.assertEqual(infer_camera_group_from_name("Cam14"), "rml_6_corporate_office_7th_floor")
        self.assertEqual(infer_camera_group_from_name("Cam23"), "rml_6_corporate_office_7th_floor")

    def test_location_fields(self):
        fields = location_fields_for_group("rml_6_corporate_office_6th_floor")
        self.assertEqual(fields["building"], CORPORATE_OFFICE)
        self.assertEqual(fields["floor"], "6th Floor")
        self.assertEqual(fields["site"], DEFAULT_SITE_NAME)

    def test_build_hierarchy(self):
        location_config = [
            {
                "site": DEFAULT_SITE_NAME,
                "building": CORPORATE_OFFICE,
                "floors": list(CORPORATE_OFFICE_FLOORS),
            }
        ]
        cameras = [
            {
                "building": CORPORATE_OFFICE,
                "camera_group": "rml_6_corporate_office_6th_floor",
                "floor_group": "6th Floor",
                "floor": "6th Floor",
                "site": DEFAULT_SITE_NAME,
                "location_path": "RML - 6 / Corporate Office / 6th Floor",
            },
            {
                "building": CORPORATE_OFFICE,
                "camera_group": "rml_6_corporate_office_6th_floor",
                "floor_group": "6th Floor",
                "floor": "6th Floor",
                "site": DEFAULT_SITE_NAME,
                "location_path": "RML - 6 / Corporate Office / 6th Floor",
            },
        ]
        tree = build_groups_hierarchy(cameras, location_config)
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["site"], DEFAULT_SITE_NAME)
        floors = tree[0]["floorGroups"]
        self.assertEqual(len(floors), len(CORPORATE_OFFICE_FLOORS))
        self.assertEqual(floors[0]["floor_group"], "Ground Floor")
        self.assertEqual(floors[0]["cameraCount"], 0)
        sixth = next(f for f in floors if f["floor_group"] == "6th Floor")
        self.assertEqual(sixth["cameraCount"], 2)

    def test_build_hierarchy_new_building_empty_floors(self):
        location_config = [
            {
                "site": "North Plant",
                "building": "Warehouse A",
                "floors": ["Ground Floor", "1st Floor"],
            }
        ]
        tree = build_groups_hierarchy([], location_config)
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["building"], "Warehouse A")
        self.assertEqual(tree[0]["site"], "North Plant")
        self.assertEqual(len(tree[0]["floorGroups"]), 2)
        self.assertEqual(tree[0]["floorGroups"][0]["cameraCount"], 0)

    def test_build_hierarchy_cameras_only(self):
        location_config = [
            {
                "site": DEFAULT_SITE_NAME,
                "building": CORPORATE_OFFICE,
                "floors": list(CORPORATE_OFFICE_FLOORS),
            }
        ]
        cameras = [
            {"building": CORPORATE_OFFICE, "floor": "2nd Floor", "floor_group": "2nd Floor", "site": DEFAULT_SITE_NAME},
            {"building": CORPORATE_OFFICE, "floor": "5th Floor", "floor_group": "5th Floor", "site": DEFAULT_SITE_NAME},
            {"building": CORPORATE_OFFICE, "floor": "5th Floor", "floor_group": "5th Floor", "site": DEFAULT_SITE_NAME},
        ]
        tree = build_groups_hierarchy(cameras, location_config, cameras_only=True)
        self.assertEqual(len(tree), 1)
        floors = [f["floor_group"] for f in tree[0]["floorGroups"]]
        self.assertEqual(floors, ["2nd Floor", "5th Floor"])
        fifth = next(f for f in tree[0]["floorGroups"] if f["floor_group"] == "5th Floor")
        self.assertEqual(fifth["cameraCount"], 2)

    def test_camera_group_key_from_building_floor(self):
        cam = {
            "site": DEFAULT_SITE_NAME,
            "building": CORPORATE_OFFICE,
            "floor": "2nd Floor",
            "camera_group": "wrong_key",
        }
        self.assertEqual(
            camera_group_key_for_document(cam),
            "rml_6_corporate_office_2nd_floor",
        )


class TestCameraAccess(unittest.TestCase):
    def test_admin_all_access(self):
        access = normalize_camera_access({"role": "Admin"})
        self.assertTrue(access.get("all"))

    def test_group_access_filter(self):
        user = {
            "role": "Viewer",
            "cameraAccess": {
                "allowedCameraGroups": ["rml_6_corporate_office_7th_floor"],
                "allowedCameraUids": [],
            },
        }
        filt = build_access_filter(user)
        self.assertIn("rml_6_corporate_office_7th_floor", filt["camera_group"]["$in"])

    def test_uid_access_filter(self):
        user = {
            "role": "Viewer",
            "cameraAccess": {
                "allowedCameraGroups": [],
                "allowedCameraUids": ["ip_192_168_1_10"],
            },
        }
        filt = build_access_filter(user)
        self.assertEqual(filt["camera_uid"]["$in"], ["ip_192_168_1_10"])

    def test_combined_access_filter(self):
        user = {
            "role": "Viewer",
            "cameraAccess": {
                "allowedCameraGroups": ["rml_6_gym_gym_floors"],
                "allowedCameraUids": ["ip_192_168_1_10"],
            },
        }
        filt = build_access_filter(user)
        self.assertIn("$or", filt)

    def test_active_filter_excludes_disabled(self):
        filt = active_camera_filter(False)
        self.assertIn("$or", filt)


if __name__ == "__main__":
    unittest.main()
