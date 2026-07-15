import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from core.models import DailyPlan
from core.proactive import ProactivePolicy

from tests.fixtures import outfit_payload


def plan(private_bonus=2, group_bonus=1):
    return DailyPlan.from_dict(
        {
            "date": "2026-07-14",
            "persona_id": "alice",
            "theme": "日常",
            "mood": "平静",
            "outfit": outfit_payload(),
            "timeline": [
                {"id": "all", "start": "00:00", "end": "24:00", "activity": "休息", "state": "available", "availability": "normal"}
            ],
            "proactive_windows": [],
            "budget_bonus": {"private": private_bonus, "group": group_bonus},
        }
    )


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.umo = "aiocqhttp:FriendMessage:42"
        self.config = {
            "friend_settings": {
                "enable": True,
                "session_list": [self.umo],
                "daily_budget_min": 1,
                "daily_budget_max": 1,
                "llm_bonus_max": 2,
                "daily_hard_max": 2,
                "cooldown_minutes": 120,
            },
            "group_settings": {"enable": False, "session_list": []},
            "delivery_settings": {"max_unanswered": 3, "minimum_idle_for_window_minutes": 20},
        }
        self.storage = SimpleNamespace(sessions={})
        self.policy = ProactivePolicy(self.config, self.storage, ZoneInfo("Asia/Shanghai"))
        self.now = datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    def test_budget_is_clamped_by_hard_max(self):
        state = self.policy.ensure_state(self.umo, "alice", plan(private_bonus=99), self.now)
        self.assertEqual(state.daily_budget, 2)

    def test_three_unanswered_blocks_delivery(self):
        state = self.policy.ensure_state(self.umo, "alice", plan(), self.now)
        state.daily_budget = 10
        state.unanswered_count = 3
        decision = self.policy.evaluate(umo=self.umo, state=state, current_item=None, now=self.now, trigger="idle")
        self.assertFalse(decision.allowed)
        self.assertIn("unanswered", decision.reason)

    def test_incoming_resets_unanswered(self):
        state = self.policy.ensure_state(self.umo, "alice", plan(), self.now)
        state.unanswered_count = 2
        self.policy.record_incoming(self.umo, self.now + timedelta(minutes=1))
        self.assertEqual(state.unanswered_count, 0)

    def test_subscribe_enables_and_adds_private_session(self):
        self.config["friend_settings"]["enable"] = False
        self.config["friend_settings"]["session_list"] = []

        self.assertTrue(self.policy.subscribe(self.umo))
        self.assertTrue(self.config["friend_settings"]["enable"])
        self.assertEqual(self.config["friend_settings"]["session_list"], [self.umo])
        self.assertTrue(self.policy.is_enabled(self.umo))
        self.assertFalse(self.policy.subscribe(self.umo))

    def test_subscribe_uses_group_settings(self):
        umo = "aiocqhttp:GroupMessage:42"

        self.assertTrue(self.policy.subscribe(umo))
        self.assertTrue(self.config["group_settings"]["enable"])
        self.assertEqual(self.config["group_settings"]["session_list"], [umo])


if __name__ == "__main__":
    unittest.main()
