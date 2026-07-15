import unittest
from datetime import date, datetime
from pathlib import Path

from core.context_injection import DEFAULT_KEYWORDS, SmartContextInjector
from core.long_term import LongTermTimelineStore, validate_stage
from core.models import DailyPlan
from tests.fixtures import outfit_payload


def plan():
    return DailyPlan.from_dict(
        {
            "date": "2026-07-15",
            "persona_id": "alice",
            "theme": "学习日",
            "mood": "专注",
            "outfit": outfit_payload("清爽学院风"),
            "timeline": [
                {"id": "sleep", "start": "00:00", "end": "08:00", "activity": "睡觉", "state": "sleep", "availability": "blocked"},
                {"id": "study", "start": "08:00", "end": "12:00", "activity": "自习", "location": "图书馆", "state": "focus", "availability": "low"},
                {"id": "free", "start": "12:00", "end": "24:00", "activity": "午后安排", "state": "available", "availability": "normal"},
            ],
            "proactive_windows": [],
            "budget_bonus": {},
        }
    )


def long_term() -> LongTermTimelineStore:
    store = LongTermTimelineStore(Path("unused"))
    store.stages = [
        validate_stage(
            {
                "id": "semester",
                "name": "秋季学期",
                "kind": "academic",
                "start_date": "2026-07-01",
                "end_date": "2026-12-31",
                "priority": 50,
                "summary": "正常上课",
                "weekly_rules": [],
                "special_dates": [],
                "special_periods": [],
                "milestones": [{"date": "2026-07-18", "title": "提交作业", "lead_days": 1}],
                "constraints": ["工作日优先学习"],
            },
            "alice",
        )
    ]
    return store


class SmartContextInjectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 15, 9, 30)
        self.plan = plan()
        self.long_term = long_term()

    def test_base_state_is_disabled_by_default(self):
        content = SmartContextInjector().build(self.plan, self.now, self.long_term, "你好！")

        self.assertEqual(content, "")

    def test_base_state_can_be_enabled(self):
        content = SmartContextInjector({"base_module_enable": True}).build(
            self.plan,
            self.now,
            self.long_term,
            "你好！",
        )

        self.assertIn("当前活动：自习", content)
        self.assertNotIn("今日穿搭", content)
        self.assertNotIn("当前大时间表阶段", content)

    def test_outfit_hides_underwear_without_explicit_keyword(self):
        content = SmartContextInjector().build(self.plan, self.now, self.long_term, "今天穿什么？")
        self.assertIn("今日穿搭", content)
        self.assertIn("白色短袖衬衫", content)
        self.assertNotIn("浅色无痕内衣", content)

    def test_underwear_keyword_includes_underwear(self):
        content = SmartContextInjector().build(self.plan, self.now, self.long_term, "内衣怎么搭？")
        self.assertIn("浅色无痕内衣", content)

    def test_schedule_and_long_term_modules_are_combined(self):
        content = SmartContextInjector().build(self.plan, self.now, self.long_term, "考试前我几点有空？")
        self.assertIn("下一项：12:00-24:00 午后安排", content)
        self.assertIn("当前大时间表阶段：秋季学期", content)
        self.assertIn("近期里程碑：2026-07-18 提交作业", content)

    def test_full_query_requests_tool(self):
        content = SmartContextInjector().build(self.plan, self.now, self.long_term, "给我完整大时间表")
        self.assertIn("get_long_term_timeline", content)

    def test_details_report_only_matched_modules(self):
        injection, modules, limit = SmartContextInjector().build_details(
            self.plan,
            self.now,
            self.long_term,
            DEFAULT_KEYWORDS["schedule_keywords"][0] + DEFAULT_KEYWORDS["long_term_keywords"][0],
        )
        self.assertTrue(injection)
        self.assertEqual(modules, ("schedule", "long_term"))
        self.assertEqual(limit, 1600)

    def test_base_module_is_reported_with_matched_modules(self):
        _, modules, _ = SmartContextInjector({"base_module_enable": True}).build_details(
            self.plan,
            self.now,
            self.long_term,
            DEFAULT_KEYWORDS["schedule_keywords"][0],
        )

        self.assertEqual(modules, ("base", "schedule"))

    def test_length_limit_truncates_content(self):
        injector = SmartContextInjector({"max_chars": 400, "outfit_keywords": ["穿搭"], "underwear_keywords": [], "schedule_keywords": [], "long_term_keywords": [], "full_query_keywords": []})
        content = injector.build(self.plan, self.now, self.long_term, "穿搭")
        self.assertLessEqual(len(content), 400)
        self.assertTrue(content.endswith("</character_state>"))


if __name__ == "__main__":
    unittest.main()
