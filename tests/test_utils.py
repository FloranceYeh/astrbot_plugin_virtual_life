import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.models import DailyPlan
from core.utils import format_outfit, format_timeline, next_available_at, timeline_item_at

from tests.fixtures import outfit_payload


class UtilsTests(unittest.TestCase):
    def setUp(self):
        self.plan = DailyPlan.from_dict(
            {
                "date": "2026-07-14",
                "persona_id": "alice",
                "theme": "日常",
                "mood": "平静",
                "outfit": outfit_payload(),
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

    def test_split_query_fallbacks(self):
        now = datetime(2026, 7, 14, 9, 0, tzinfo=self.tz)
        timeline = format_timeline(
            self.plan,
            now,
            {
                "stage": {
                    "name": "暑期阶段",
                    "start_date": "2026-07-01",
                    "end_date": "2026-08-31",
                },
                "active_periods": [
                    {
                        "name": "考试周",
                        "start_date": "2026-07-13",
                        "end_date": "2026-07-19",
                        "constraints": ["减少娱乐"],
                    }
                ],
                "holidays": [{"name": "纪念日"}],
            },
        )
        outfit = format_outfit(self.plan)
        self.assertIn("当前时段：08:00-12:00", timeline)
        self.assertIn("今日主题：日常", timeline)
        self.assertIn("心情状态：平静", timeline)
        self.assertIn("当前大时间段：暑期阶段", timeline)
        self.assertIn("特殊时间段：考试周", timeline)
        self.assertIn("今日节日：纪念日", timeline)
        self.assertNotIn("穿搭风格", timeline)
        self.assertIn("穿搭风格：日常休闲风", outfit)
        self.assertIn("今日主题：日常", outfit)
        self.assertIn("今日心情：平静", outfit)
        self.assertNotIn("24 小时时间轴", outfit)

    def test_timeline_fallback_without_stage_is_explicit(self):
        timeline = format_timeline(
            self.plan,
            datetime(2026, 7, 14, 9, 0, tzinfo=self.tz),
            {"stage": None, "active_periods": [], "holidays": []},
        )
        self.assertIn("当前大时间段：暂无当前阶段", timeline)


if __name__ == "__main__":
    unittest.main()
