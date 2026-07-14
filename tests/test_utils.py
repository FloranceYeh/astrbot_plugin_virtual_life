import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.models import DailyPlan
from core.utils import next_available_at, timeline_item_at


class UtilsTests(unittest.TestCase):
    def setUp(self):
        self.plan = DailyPlan.from_dict(
            {
                "date": "2026-07-14",
                "persona_id": "alice",
                "theme": "日常",
                "mood": "平静",
                "outfit": "休闲装",
                "timeline": [
                    {"id": "sleep", "start": "00:00", "end": "08:00", "activity": "睡觉", "state": "sleep", "availability": "blocked"},
                    {"id": "busy", "start": "08:00", "end": "12:00", "activity": "工作", "state": "focus", "availability": "low"},
                    {"id": "free", "start": "12:00", "end": "24:00", "activity": "休息", "state": "available", "availability": "high"},
                ],
                "proactive_windows": [],
                "budget_bonus": {"private": 0, "group": 0},
            }
        )
        self.tz = ZoneInfo("Asia/Shanghai")

    def test_current_item_and_next_available(self):
        now = datetime(2026, 7, 14, 9, 0, tzinfo=self.tz)
        self.assertEqual(timeline_item_at(self.plan, now).id, "busy")
        self.assertEqual(next_available_at(self.plan, now).hour, 12)


if __name__ == "__main__":
    unittest.main()
