import unittest

from gamedesigner.qt_theme import palette, stylesheet


class QtThemeTests(unittest.TestCase):
    def test_dark_table_selection_uses_theme_accent(self) -> None:
        style = stylesheet("dark")
        colors = palette("dark")

        self.assertIn("QTableWidget::item:selected", style)
        self.assertIn("QHeaderView {", style)
        self.assertIn("QHeaderView::section", style)
        self.assertIn(f"background: {colors['accent']};", style)
        self.assertIn("color: #FFFFFF;", style)


if __name__ == "__main__":
    unittest.main()
