import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from astrbot_plugin_virtual_life.main import ProactiveVirtualDailyPlugin
from tests.test_window_retry import plan

from astrbot.core.utils.session_lock import session_lock_manager


class FakeEvent:
    def __init__(self, umo):
        self.unified_msg_origin = umo


def plugin_instance(now):
    plugin = object.__new__(ProactiveVirtualDailyPlugin)
    plugin.config = {
        "delivery_settings": {
            "minimum_idle_for_window_minutes": 20,
            "proactive_timeline_context": False,
        }
    }
    plugin._now = lambda: now
    plugin.delivery_locks = {}
    plugin._conversation_generations = {}
    plugin._conversation_activity_at = {}
    plugin._active_agent_counts = {}
    plugin._agent_release_tasks = set()
    plugin._schedule_idle = Mock()
    return plugin


class ProactiveConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.umo = "aiocqhttp:FriendMessage:42"
        self.now = datetime(2026, 8, 3, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.persona = SimpleNamespace(id="alice", prompt="persona")
        self.plan = plan()

    async def test_active_agent_skips_before_policy_and_generation(self):
        plugin = plugin_instance(self.now)
        plugin._active_agent_counts[self.umo] = 1
        plugin.policy = SimpleNamespace(ensure_state=Mock(), evaluate=Mock())
        plugin._deliver = AsyncMock()

        sent, reason = await plugin._attempt_unsolicited(
            self.umo, self.persona, self.plan, "intent", "window"
        )

        self.assertFalse(sent)
        self.assertEqual(reason, "agent task active")
        plugin.policy.evaluate.assert_not_called()
        plugin._deliver.assert_not_awaited()

    async def test_activity_during_generation_discards_proactive_message(self):
        plugin = plugin_instance(self.now)
        plugin._record_completed_personal_network_events = AsyncMock()
        plugin._personal_network_context = AsyncMock(return_value="")
        plugin.smart_context_injector = Mock()
        plugin.long_term = Mock()

        async def generate(**_kwargs):
            plugin._mark_conversation_activity(self.umo, self.now)
            return "stale"

        plugin.message_generator = SimpleNamespace(
            generate=generate,
            record_conversation=AsyncMock(),
        )
        plugin._send_text = AsyncMock()

        delivered = await plugin._deliver(
            self.umo, self.persona, self.plan, "intent", 0, generation=0
        )

        self.assertFalse(delivered)
        plugin._send_text.assert_not_awaited()
        plugin.message_generator.record_conversation.assert_not_awaited()

    async def test_stable_generation_sends_and_records_under_session_lock(self):
        plugin = plugin_instance(self.now)
        plugin._record_completed_personal_network_events = AsyncMock()
        plugin._personal_network_context = AsyncMock(return_value="")
        plugin.smart_context_injector = Mock()
        plugin.long_term = Mock()
        plugin.message_generator = SimpleNamespace(
            generate=AsyncMock(return_value="current"),
            record_conversation=AsyncMock(return_value=True),
        )
        plugin._send_text = AsyncMock()

        delivered = await plugin._deliver(
            self.umo, self.persona, self.plan, "intent", 0, generation=0
        )

        self.assertTrue(delivered)
        plugin._send_text.assert_awaited_once_with(self.umo, "current")
        plugin.message_generator.record_conversation.assert_awaited_once()

    async def test_activity_while_waiting_for_session_lock_discards_message(self):
        plugin = plugin_instance(self.now)
        generated = asyncio.Event()
        plugin._record_completed_personal_network_events = AsyncMock()
        plugin._personal_network_context = AsyncMock(return_value="")
        plugin.smart_context_injector = Mock()
        plugin.long_term = Mock()

        async def generate(**_kwargs):
            generated.set()
            return "stale before commit"

        plugin.message_generator = SimpleNamespace(
            generate=generate,
            record_conversation=AsyncMock(),
        )
        plugin._send_text = AsyncMock()

        async with session_lock_manager.acquire_lock(self.umo):
            delivery = asyncio.create_task(
                plugin._deliver(
                    self.umo,
                    self.persona,
                    self.plan,
                    "intent",
                    0,
                    generation=0,
                )
            )
            await generated.wait()
            await asyncio.sleep(0)
            plugin._mark_conversation_activity(self.umo, self.now)

        self.assertFalse(await delivery)
        plugin._send_text.assert_not_awaited()
        plugin.message_generator.record_conversation.assert_not_awaited()

    async def test_agent_release_waits_for_framework_history_save(self):
        plugin = plugin_instance(self.now)
        event = FakeEvent(self.umo)

        async with session_lock_manager.acquire_lock(self.umo):
            await plugin.mark_agent_active(event, None)
            await plugin.defer_agent_release(event, None, None)
            await asyncio.sleep(0)
            self.assertTrue(plugin._agent_is_active(self.umo))

        await asyncio.gather(*tuple(plugin._agent_release_tasks))

        self.assertFalse(plugin._agent_is_active(self.umo))
        self.assertEqual(plugin._conversation_generation(self.umo), 2)
        plugin._schedule_idle.assert_called_once_with(self.umo)

    async def test_agent_completion_restarts_window_idle_period(self):
        plugin = plugin_instance(self.now)
        plugin._mark_conversation_activity(self.umo, self.now - timedelta(minutes=5))
        plugin.policy = SimpleNamespace(ensure_state=Mock(), evaluate=Mock())
        plugin._deliver = AsyncMock()

        sent, reason = await plugin._attempt_unsolicited(
            self.umo, self.persona, self.plan, "intent", "window"
        )

        self.assertFalse(sent)
        self.assertEqual(reason, "conversation is not idle enough")
        plugin.policy.evaluate.assert_not_called()
        plugin._deliver.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
