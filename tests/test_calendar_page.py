import unittest
from pathlib import Path


class CalendarPageSyntaxTests(unittest.TestCase):
    def test_calendar_page_compiles(self):
        page = Path(__file__).resolve().parents[1] / "pages" / "3_Content_Calendar.py"
        compile(page.read_text(encoding="utf-8"), str(page), "exec")


if __name__ == "__main__":
    unittest.main()
