import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.message_generator import ProactiveMessageGenerator
from core.persona import PersonaContext


class Response:
    completion_text = "主动消息"


class Provider:
    def __init__(self):
        self.calls = 0

    async def text_chat(self, prompt, session_id):
        self.calls += 1
        return Response()


class ConversationManager:
    async def get_curr_conversation_id(self, umo):
        return None


class Context:
    def __init__(self, default_provider, selected_provider):
        self.default_provider = default_provider
        self.selected_provider = selected_provider
        self.requested_provider_ids = []
        self.conversation_manager = ConversationManager()

    def get_provider_by_id(self, provider_id):
        self.requested_provider_ids.append(provider_id)
        return self.selected_provider

    def get_using_provider(self):
        return self.default_provider


class MessageGeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_proactive_provider_is_independent_from_schedule_provider(self):
        default_provider = Provider()
        selected_provider = Provider()
        context = Context(default_provider, selected_provider)
        generator = ProactiveMessageGenerator(
            context,
            {
                "schedule_settings": {
                    "schedule_llm_provider": "schedule-provider",
                    "proactive_llm_provider": "proactive-provider",
                },
                "delivery_settings": {
                    "recent_chat_messages": 0,
                    "proactive_prompt": "{current_time} {current_state} {intent} {unanswered_count}",
                },
            },
        )
        text = await generator.generate(
            umo="aiocqhttp:FriendMessage:42",
            persona=PersonaContext("alice", "persona"),
            current_time=datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            current_state="吃午饭",
            intent="分享午饭",
            unanswered_count=0,
        )
        self.assertEqual(text, "主动消息")
        self.assertEqual(context.requested_provider_ids, ["proactive-provider"])
        self.assertEqual(selected_provider.calls, 1)
        self.assertEqual(default_provider.calls, 0)

    async def test_legacy_provider_setting_is_not_used(self):
        default_provider = Provider()
        selected_provider = Provider()
        context = Context(default_provider, selected_provider)
        generator = ProactiveMessageGenerator(
            context,
            {
                "schedule_settings": {"llm_provider": "legacy-provider"},
                "delivery_settings": {
                    "recent_chat_messages": 0,
                    "proactive_prompt": "{current_time} {current_state} {intent} {unanswered_count}",
                },
            },
        )
        await generator.generate(
            umo="aiocqhttp:FriendMessage:42",
            persona=PersonaContext("alice", "persona"),
            current_time=datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            current_state="吃午饭",
            intent="分享午饭",
            unanswered_count=0,
        )
        self.assertEqual(context.requested_provider_ids, [])
        self.assertEqual(default_provider.calls, 1)
        self.assertEqual(selected_provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
