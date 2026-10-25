"""数据层测试：保护号码校验和原子写入这两条最基础的安全边界。"""

import json
import tempfile
import unittest
from pathlib import Path

from dlt.data import DataValidationError, load_draws, validate_draw, write_json_atomic


VALID_DRAW = {
    "issue": "26121",
    "date": "2026-10-24",
    "front": ["06", "08", "19", "24", "25"],
    "back": ["01", "08"],
}


class DataTests(unittest.TestCase):
    def test_validate_draw_accepts_valid_record(self):
        self.assertEqual(validate_draw(VALID_DRAW), VALID_DRAW)

    def test_validate_draw_rejects_duplicate_or_out_of_range_numbers(self):
        bad = dict(VALID_DRAW, front=["00", "06", "06", "24", "36"])
        with self.assertRaises(DataValidationError):
            validate_draw(bad)

    def test_atomic_write_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dlt.json"
            write_json_atomic(path, [VALID_DRAW])
            self.assertEqual(load_draws(path), [VALID_DRAW])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[0]["issue"], "26121")


if __name__ == "__main__":
    unittest.main()

