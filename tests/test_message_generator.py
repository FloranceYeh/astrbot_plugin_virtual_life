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
    def __init__(self, conversation_id=None):
        self.conversation_id = conversation_id
        self.created = []
        self.pairs = []

    async def get_curr_conversation_id(self, umo):
        return self.conversation_id

    async def new_conversation(self, umo, persona_id=None):
        self.created.append((umo, persona_id))
        self.conversation_id = "created-conversation"
        return self.conversation_id

    async def add_message_pair(self, **kwargs):
        self.pairs.append(kwargs)


class Context:
    def __init__(self, default_provider, selected_provider, conversation_manager=None):
        self.default_provider = default_provider
        self.selected_provider = selected_provider
        self.requested_provider_ids = []
        self.conversation_manager = conversation_manager or ConversationManager()

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

    async def test_record_conversation_appends_proactive_pair(self):
        manager = ConversationManager("conversation-1")
        generator = ProactiveMessageGenerator(Context(Provider(), None, manager), {})
        recorded = await generator.record_conversation(
            umo="aiocqhttp:FriendMessage:42",
            persona_id="alice",
            intent="分享午饭",
            assistant_text="今天的午饭很好吃。",
        )
        self.assertTrue(recorded)
        self.assertEqual(manager.created, [])
        self.assertEqual(manager.pairs[0]["cid"], "conversation-1")
        user_message = manager.pairs[0]["user_message"].model_dump()
        assistant_message = manager.pairs[0]["assistant_message"].model_dump()
        self.assertIn("主动消息触发", user_message["content"][0]["text"])
        self.assertIn("分享午饭", user_message["content"][0]["text"])
        self.assertEqual(assistant_message["content"][0]["text"], "今天的午饭很好吃。")

    async def test_record_conversation_creates_missing_conversation(self):
        manager = ConversationManager()
        generator = ProactiveMessageGenerator(Context(Provider(), None, manager), {})
        recorded = await generator.record_conversation(
            umo="aiocqhttp:FriendMessage:42",
            persona_id="alice",
            intent="分享街景",
            assistant_text="给你看看今天拍到的街景。",
        )
        self.assertTrue(recorded)
        self.assertEqual(manager.created, [("aiocqhttp:FriendMessage:42", "alice")])
        self.assertEqual(manager.pairs[0]["cid"], "created-conversation")

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
