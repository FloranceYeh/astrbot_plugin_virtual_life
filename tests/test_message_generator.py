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
        self.prompts = []

    async def text_chat(self, prompt, session_id):
        self.calls += 1
        self.prompts.append(prompt)
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
        self.assertIn("你主动发起的消息", user_message["content"][0]["text"])
        self.assertIn("分享午饭", user_message["content"][0]["text"])
        self.assertNotIn("系统事件", user_message["content"][0]["text"])
        self.assertEqual(assistant_message["content"][0]["text"], "今天的午饭很好吃。")

    async def test_record_conversation_honors_custom_note_template(self):
        manager = ConversationManager("conversation-1")
        generator = ProactiveMessageGenerator(
            Context(Provider(), None, manager),
            {
                "delivery_settings": {
                    "proactive_history_note_template": "（你想起关于{intent}的事）"
                }
            },
        )
        await generator.record_conversation(
            umo="aiocqhttp:FriendMessage:42",
            persona_id="alice",
            intent="分享午饭",
            assistant_text="今天的午饭很好吃。",
        )
        user_message = manager.pairs[0]["user_message"].model_dump()
        self.assertIn("你想起关于分享午饭的事", user_message["content"][0]["text"])

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

    async def test_generate_reads_unanswered_hint_template(self):
        provider = Provider()
        generator = ProactiveMessageGenerator(
            Context(provider, None),
            {
                "delivery_settings": {
                    "recent_chat_messages": 0,
                    "proactive_prompt": "hint={unanswered_hint}",
                    "unanswered_hint_template": "0: 正常热络\n2: 多次未回应，请收敛",
                },
            },
        )
        await generator.generate(
            umo="aiocqhttp:FriendMessage:42",
            persona=PersonaContext("alice", "persona"),
            current_time=datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            current_state="吃午饭",
            intent="分享午饭",
            unanswered_count=3,
        )
        self.assertIn("多次未回应，请收敛", provider.prompts[0])

    async def test_generate_uses_empty_hint_without_template(self):
        provider = Provider()
        generator = ProactiveMessageGenerator(
            Context(provider, None),
            {
                "delivery_settings": {
                    "recent_chat_messages": 0,
                    "proactive_prompt": "hint=[{unanswered_hint}]",
                },
            },
        )
        await generator.generate(
            umo="aiocqhttp:FriendMessage:42",
            persona=PersonaContext("alice", "persona"),
            current_time=datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            current_state="吃午饭",
            intent="分享午饭",
            unanswered_count=3,
        )
        self.assertIn("hint=[]", provider.prompts[0])

    def test_parse_unanswered_hint_picks_highest_matching_threshold(self):
        generator = ProactiveMessageGenerator(Context(Provider(), None), {})
        template = "0: 正常\n2: 收敛\n3: 明显收敛"
        self.assertEqual(generator._parse_unanswered_hint(template, 0), "正常")
        self.assertEqual(generator._parse_unanswered_hint(template, 1), "正常")
        self.assertEqual(generator._parse_unanswered_hint(template, 2), "收敛")
        self.assertEqual(generator._parse_unanswered_hint(template, 5), "明显收敛")
        self.assertEqual(generator._parse_unanswered_hint("", 2), "")
        self.assertEqual(generator._parse_unanswered_hint("2: 收敛", 0), "")
        self.assertEqual(generator._parse_unanswered_hint("不加数字的文本", 3), "")

    def test_build_unanswered_hint_uses_placeholder_method(self):
        generator = ProactiveMessageGenerator(Context(Provider(), None), {})
        settings = {
            "unanswered_hint_method": "placeholder",
            "unanswered_hint_placeholder_template": "已连续 {unanswered_count} 次未回应",
            "unanswered_hint_template": "0: 正常\n3: 收敛",
        }
        self.assertEqual(
            generator._build_unanswered_hint(settings, 2), "已连续 2 次未回应"
        )

    def test_build_unanswered_hint_uses_segmented_method(self):
        generator = ProactiveMessageGenerator(Context(Provider(), None), {})
        settings = {
            "unanswered_hint_method": "segmented",
            "unanswered_hint_placeholder_template": "占位 {unanswered_count}",
            "unanswered_hint_template": "0: 正常\n3: 收敛",
        }
        self.assertEqual(generator._build_unanswered_hint(settings, 5), "收敛")

    def test_build_unanswered_hint_defaults_to_segmented(self):
        generator = ProactiveMessageGenerator(Context(Provider(), None), {})
        settings = {"unanswered_hint_template": "0: 正常\n3: 收敛"}
        self.assertEqual(generator._build_unanswered_hint(settings, 0), "正常")

    def test_build_unanswered_hint_returns_empty_when_unset(self):
        generator = ProactiveMessageGenerator(Context(Provider(), None), {})
        self.assertEqual(generator._build_unanswered_hint({}, 3), "")

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
