import unittest
from datetime import date

from core.holiday_calendar import ChinaHolidayCalendar


class ChinaHolidayCalendarTests(unittest.TestCase):
    def setUp(self):
        self.calendar = ChinaHolidayCalendar()

    def test_common_traditional_festivals_are_available(self):
        expected = {
            date(2026, 2, 16): "除夕",
            date(2026, 2, 17): "春节",
            date(2026, 3, 3): "元宵节",
            date(2026, 6, 19): "端午节",
            date(2026, 8, 19): "七夕节",
            date(2026, 9, 25): "中秋节",
            date(2026, 10, 18): "重阳节",
            date(2027, 1, 15): "腊八节",
        }
        for target, name in expected.items():
            with self.subTest(target=target):
                self.assertIn(name, {item["name"] for item in self.calendar.on(target)})

    def test_public_holiday_and_lunar_name_are_deduplicated(self):
        names = [item["name"] for item in self.calendar.on(date(2026, 2, 17))]
        self.assertEqual(names.count("春节"), 1)

    def test_upcoming_excludes_current_day(self):
        values = self.calendar.upcoming(date(2026, 9, 24), 2)
        self.assertIn("中秋节", {item["name"] for item in values})
        self.assertTrue(all(item["date"] != "2026-09-24" for item in values))


if __name__ == "__main__":
    unittest.main()
