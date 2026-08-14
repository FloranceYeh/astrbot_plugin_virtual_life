import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.models import DailyPlan

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from astrbot_plugin_virtual_life.main import ProactiveVirtualDailyPlugin


class DailyGenerationSwitchTests(unittest.IsolatedAsyncioTestCase):
    def plugin(self, *, schedule, outfit):
        plugin = object.__new__(ProactiveVirtualDailyPlugin)
        plugin.config = {
            "schedule_settings": {
                "daily_schedule_generation_enable": schedule,
                "daily_outfit_generation_enable": outfit,
            },
            "long_term_settings": {"enable": True},
        }
        plugin.storage = SimpleNamespace(
            plans={},
            schedule_requirements={},
            get_plan=Mock(return_value=None),
            get_recent_plans=Mock(return_value=[]),
            active_schedule_requirements=Mock(return_value=[]),
            consume_schedule_requirements=Mock(),
            plan_key=Mock(return_value="2026-08-14::alice"),
            save_plans=AsyncMock(),
        )
        plugin.long_term = SimpleNamespace(format_day_context=Mock(return_value="阶段"))
        plugin._personal_network_context = AsyncMock(return_value="关系")
        plugin._generate_new_character = AsyncMock(return_value="新人")
        plugin.plan_generator = SimpleNamespace(generate=AsyncMock())
        return plugin

    def test_flags_default_to_enabled(self):
        plugin = object.__new__(ProactiveVirtualDailyPlugin)
        plugin.config = {}

        self.assertEqual(plugin._daily_generation_flags(), (True, True))

    async def test_outfit_only_skips_all_schedule_inputs(self):
        plugin = self.plugin(schedule=False, outfit=True)
        generated = DailyPlan(
            date="2026-08-14",
            persona_id="alice",
            theme="",
            mood="",
            outfit=None,
            timeline=(),
        )
        plugin.plan_generator.generate.return_value = generated

        await plugin._ensure_plan(
            SimpleNamespace(id="alice", prompt="persona"),
            date(2026, 8, 14),
        )

        kwargs = plugin.plan_generator.generate.await_args.kwargs
        self.assertFalse(kwargs["include_schedule"])
        self.assertTrue(kwargs["include_outfit"])
        self.assertEqual(kwargs["history_plans"], [])
        self.assertEqual(kwargs["long_term_context"], "")
        self.assertEqual(kwargs["relationship_context"], "")
        plugin.storage.active_schedule_requirements.assert_not_called()
        plugin._personal_network_context.assert_not_awaited()
        plugin._generate_new_character.assert_not_awaited()
        plugin.storage.consume_schedule_requirements.assert_not_called()

    async def test_schedule_inputs_are_used_when_schedule_is_enabled(self):
        plugin = self.plugin(schedule=True, outfit=False)
        generated = DailyPlan(
            date="2026-08-14",
            persona_id="alice",
            theme="日常",
            mood="平静",
            outfit=None,
            timeline=(),
        )
        plugin.plan_generator.generate.return_value = generated

        await plugin._ensure_plan(
            SimpleNamespace(id="alice", prompt="persona"),
            date(2026, 8, 14),
        )

        kwargs = plugin.plan_generator.generate.await_args.kwargs
        self.assertTrue(kwargs["include_schedule"])
        self.assertFalse(kwargs["include_outfit"])
        plugin.storage.get_recent_plans.assert_called_once()
        plugin.storage.active_schedule_requirements.assert_called_once()
        plugin._personal_network_context.assert_awaited_once()
        plugin._generate_new_character.assert_awaited_once()

    async def test_session_without_schedule_removes_all_proactive_jobs(self):
        plugin = object.__new__(ProactiveVirtualDailyPlugin)
        plugin.runtime = SimpleNamespace(remove_prefix=Mock(), remove=Mock())
        plugin.policy = SimpleNamespace(ensure_state=Mock())
        plugin._schedule_available_umos = {"umo"}
        plan = DailyPlan(
            date="2026-08-14",
            persona_id="alice",
            theme="",
            mood="",
            outfit=None,
            timeline=(),
        )

        await plugin._schedule_session("umo", SimpleNamespace(id="alice"), plan)

        self.assertNotIn("umo", plugin._schedule_available_umos)
        plugin.runtime.remove_prefix.assert_called_once()
        plugin.runtime.remove.assert_called_once()
        plugin.policy.ensure_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
