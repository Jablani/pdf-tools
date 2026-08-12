import unittest

from tools.yimei_packing import parse_item_qty_lines


class ParseItemQtyLinesTests(unittest.TestCase):
    def test_parses_numeric_items_without_changing_existing_behavior(self):
        lines = ["Item", "Qty", "200111", "2", "200301", "1"]

        self.assertEqual(
            parse_item_qty_lines(lines),
            [("200111", 2), ("200301", 1)],
        )

    def test_parses_alphanumeric_items(self):
        lines = ["Item", "Qty", "100302", "4", "400302D", "8"]

        self.assertEqual(
            parse_item_qty_lines(lines),
            [("100302", 4), ("400302D", 8)],
        )

    def test_rejects_unpaired_item_qty_data(self):
        with self.assertRaisesRegex(ValueError, "未成对"):
            parse_item_qty_lines(["Item", "Qty", "400302D"])

    def test_rejects_non_numeric_qty(self):
        with self.assertRaisesRegex(ValueError, "Qty 无效"):
            parse_item_qty_lines(["Item", "Qty", "400302D", "eight"])

    def test_rejects_unsafe_item_characters(self):
        with self.assertRaisesRegex(ValueError, "无效的 Item"):
            parse_item_qty_lines(["Item", "Qty", "400/302D", "8"])


if __name__ == "__main__":
    unittest.main()
