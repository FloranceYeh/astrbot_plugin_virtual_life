import unittest

from core.models import DailyPlan


def valid_payload():
    return {
        "date": "2026-07-14",
        "persona_id": "alice",
        "theme": "探索日",
        "mood": "轻快",
        "outfit": "白衬衫和长裙",
        "timeline": [
            {"id": "sleep", "start": "00:00", "end": "07:00", "activity": "睡觉", "state": "sleep", "availability": "blocked"},
            {"id": "day", "start": "07:00", "end": "23:00", "activity": "生活与工作", "state": "available", "availability": "normal"},
            {"id": "night", "start": "23:00", "end": "24:00", "activity": "睡觉", "state": "sleep", "availability": "blocked"},
        ],
        "proactive_windows": [
            {"id": "hello", "at": "12:00", "intent": "分享午饭", "audience": "both", "source_item_id": "day"}
        ],
        "budget_bonus": {"private": 2, "group": 1},
    }


class ModelTests(unittest.TestCase):
    def test_empty_timeline_time_has_clear_error(self):
        payload = valid_payload()
        payload["timeline"][0]["start"] = ""
        with self.assertRaisesRegex(ValueError, "non-empty HH:MM"):
            DailyPlan.from_dict(payload)

    def test_valid_structured_plan(self):
        plan = DailyPlan.from_dict(valid_payload())
        self.assertEqual(plan.private_bonus, 2)
        self.assertEqual(plan.timeline[-1].end, "24:00")

    def test_timeline_gap_is_rejected(self):
        payload = valid_payload()
        payload["timeline"][1]["start"] = "08:00"
        with self.assertRaises(ValueError):
            DailyPlan.from_dict(payload)

    def test_unknown_window_source_is_rejected(self):
        payload = valid_payload()
        payload["proactive_windows"][0]["source_item_id"] = "missing"
        with self.assertRaises(ValueError):
            DailyPlan.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
