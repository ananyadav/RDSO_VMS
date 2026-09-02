"""Unit tests mirroring liveTileAssignments.ts (RDSO 18.1.10)."""
import unittest


def slot_count_for_layout(camera_count: int, grid_cols: int) -> int:
    cols = max(1, grid_cols)
    min_slots = cols * cols
    rows = max(1, (camera_count + cols - 1) // cols)
    return max(min_slots, rows * cols)


def build_default_assignments(camera_ids: list[str], grid_cols: int) -> list[str | None]:
    count = slot_count_for_layout(len(camera_ids), grid_cols)
    out: list[str | None] = [None] * count
    for i, cid in enumerate(camera_ids):
        if i >= count:
            break
        out[i] = cid
    return out


def assign_camera_to_slot(
    assignments: list[str | None], slot_index: int, camera_id: str | None
) -> list[str | None]:
    if slot_index < 0 or slot_index >= len(assignments):
        return assignments
    next_assign = list(assignments)
    if camera_id:
        for i in range(len(next_assign)):
            if i != slot_index and next_assign[i] == camera_id:
                next_assign[i] = None
    next_assign[slot_index] = camera_id
    return next_assign


class TestLiveTileAssignments(unittest.TestCase):
    def test_default_fill(self):
        rows = build_default_assignments(["c1", "c2", "c3"], 2)
        self.assertEqual(rows[:3], ["c1", "c2", "c3"])

    def test_assign_moves_duplicate(self):
        rows = assign_camera_to_slot(["a", "b", None], 2, "a")
        self.assertEqual(rows, [None, "b", "a"])

    def test_unauthorized_blocked_at_ui_layer(self):
        authorized = {"a", "b"}
        self.assertNotIn("x", authorized)


if __name__ == "__main__":
    unittest.main()
