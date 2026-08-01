import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from core.models import DailyPlan
from core.proactive import ProactivePolicy

from tests.fixtures import outfit_payload

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from astrbot_plugin_virtual_life.main import ProactiveVirtualDailyPlugin


def plan():
    return DailyPlan.from_dict(
        {
            "date": "2026-07-14",
            "persona_id": "alice",
            "theme": "日常",
            "mood": "平静",
            "outfit": outfit_payload(),
            "timeline": [
                {"id": "morning", "start": "00:00", "end": "12:00", "activity": "上午日程", "state": "available", "availability": "normal"},
                {"id": "rest", "start": "12:00", "end": "14:00", "activity": "午休", "state": "sleep", "availability": "blocked"},
                {"id": "afternoon", "start": "14:00", "end": "24:00", "activity": "下午日程", "state": "available", "availability": "high"},
            ],
            "proactive_windows": [
                {"id": "w1", "at": "10:00", "intent": "上午打个招呼", "audience": "both", "source_item_id": "morning"}
            ],
            "budget_bonus": {"private": 1, "group": 0},
        }
    )


def make_plugin(config, now):
    plugin = object.__new__(ProactiveVirtualDailyPlugin)
    plugin.config = config
    plugin.timezone = ZoneInfo("Asia/Shanghai")
    plugin.storage = SimpleNamespace(sessions={})
    plugin.policy = ProactivePolicy(config, plugin.storage, plugin.timezone)
    plugin._now = lambda: now
    return plugin


class WindowRetryTests(unittest.TestCase):
    def setUp(self):
        self.umo = "aiocqhttp:FriendMessage:42"
        self.now = datetime(2026, 7, 14, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.plan = plan()
        self.window = self.plan.proactive_windows[0]
        self.base_config = {
            "friend_settings": {"enable": True, "session_list": [self.umo], "cooldown_minutes": 120},
            "group_settings": {"enable": False, "session_list": []},
            "delivery_settings": {"minimum_idle_for_window_minutes": 20},
        }

    def test_delayed_intent_uses_natural_context(self):
        plugin = make_plugin(self.base_config, self.now)
        intent = plugin._delayed_window_intent(self.window, self.plan, "sleeping")

        self.assertNotIn("延迟的主动消息", intent)
        self.assertIn("上午打个招呼", intent)
        self.assertIn("上午日程", intent)
        self.assertIn("休息", intent)

    def test_delayed_intent_fallback_without_reason(self):
        plugin = make_plugin(self.base_config, self.now)
        intent = plugin._delayed_window_intent(self.window, self.plan, "")

        self.assertIn("时机不太合适", intent)

    def test_sleep_and_probability_always_retry(self):
        plugin = make_plugin(self.base_config, self.now)
        self.assertTrue(plugin._should_retry_window(self.umo, "sleeping"))
        self.assertTrue(plugin._should_retry_window(self.umo, "availability probability rejected"))

    def test_idle_and_cooldown_retry_are_opt_in(self):
        plugin = make_plugin(self.base_config, self.now)
        self.assertFalse(plugin._should_retry_window(self.umo, "conversation is not idle enough"))
        self.assertFalse(plugin._should_retry_window(self.umo, "cooldown active"))

        enabled = make_plugin(
            {
                **self.base_config,
                "delivery_settings": {
                    **self.base_config["delivery_settings"],
                    "window_retry_when_not_idle": True,
                    "window_retry_when_cooldown": True,
                },
            },
            self.now,
        )
        self.assertTrue(enabled._should_retry_window(self.umo, "conversation is not idle enough"))
        self.assertTrue(enabled._should_retry_window(self.umo, "cooldown active"))

    def test_retry_run_at_for_sleep_uses_next_available_slot(self):
        plugin = make_plugin(self.base_config, self.now)
        run_at = plugin._window_retry_run_at(self.umo, self.plan, "sleeping")

        self.assertIsNotNone(run_at)
        self.assertEqual(run_at.hour, 14)
        self.assertGreater(run_at, self.now)

    def test_retry_run_at_for_idle_clears_idle_gate(self):
        plugin = make_plugin(self.base_config, self.now)
        plugin.storage.sessions[self.umo] = SimpleNamespace(
            last_user_message_at=(self.now - timedelta(minutes=10)).isoformat()
        )
        run_at = plugin._window_retry_run_at(self.umo, self.plan, "conversation is not idle enough")

        self.assertIsNotNone(run_at)
        self.assertGreaterEqual(run_at, self.now + timedelta(minutes=10))

    def test_retry_run_at_for_cooldown_clears_cooldown(self):
        plugin = make_plugin(self.base_config, self.now)
        plugin.storage.sessions[self.umo] = SimpleNamespace(
            last_proactive_at=(self.now - timedelta(minutes=30)).isoformat()
        )
        run_at = plugin._window_retry_run_at(self.umo, self.plan, "cooldown active")

        self.assertIsNotNone(run_at)
        self.assertGreaterEqual(run_at, self.now + timedelta(minutes=90))

    def test_retry_run_at_skips_unknown_reason(self):
        plugin = make_plugin(self.base_config, self.now)
        self.assertIsNone(plugin._window_retry_run_at(self.umo, self.plan, "budget exhausted"))

    def test_retry_run_at_skips_when_blocker_crosses_midnight(self):
        plugin = make_plugin(self.base_config, self.now)
        plugin.storage.sessions[self.umo] = SimpleNamespace(
            last_proactive_at=(self.now - timedelta(minutes=10)).isoformat()
        )
        plugin.config["friend_settings"]["cooldown_minutes"] = 2000
        self.assertIsNone(plugin._window_retry_run_at(self.umo, self.plan, "cooldown active"))


if __name__ == "__main__":
    unittest.main()
