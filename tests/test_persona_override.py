import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.persona import PersonaContext, PersonaResolver


def make_context(*, conversation_persona="alice", personas=None, default=None):
    personas = personas if personas is not None else {"alice": "alice prompt", "daily": "daily prompt"}
    default = default if default is not None else {"name": "fallback", "prompt": "fallback prompt"}

    async def get_persona(persona_id):
        if persona_id in personas:
            return SimpleNamespace(system_prompt=personas[persona_id])
        return None

    async def get_curr_conversation_id(umo):
        return "conv-1"

    async def get_conversation(umo, conversation_id):
        return SimpleNamespace(persona_id=conversation_persona)

    async def get_default_persona_v3(umo=None):
        return default

    context = SimpleNamespace(
        persona_manager=SimpleNamespace(
            get_persona=AsyncMock(side_effect=get_persona),
            get_persona_v3_by_id=Mock(return_value=None),
            get_default_persona_v3=AsyncMock(side_effect=get_default_persona_v3),
        ),
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(side_effect=get_curr_conversation_id),
            get_conversation=AsyncMock(side_effect=get_conversation),
        ),
    )
    return context


class PersonaOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_setting_keeps_per_session_persona(self):
        resolver = PersonaResolver(
            make_context(), settings_provider=lambda: {"override_persona": ""}
        )

        resolved = await resolver.resolve("umo-1")

        self.assertEqual(resolved, PersonaContext("alice", "alice prompt"))

    async def test_missing_setting_keeps_per_session_persona(self):
        resolver = PersonaResolver(make_context(), settings_provider=lambda: {})

        resolved = await resolver.resolve("umo-1")

        self.assertEqual(resolved, PersonaContext("alice", "alice prompt"))

    async def test_blank_setting_keeps_per_session_persona(self):
        resolver = PersonaResolver(
            make_context(), settings_provider=lambda: {"override_persona": "   "}
        )

        resolved = await resolver.resolve("umo-1")

        self.assertEqual(resolved, PersonaContext("alice", "alice prompt"))

    async def test_setting_uses_that_persona_across_sessions(self):
        resolver = PersonaResolver(
            make_context(), settings_provider=lambda: {"override_persona": "daily"}
        )

        first = await resolver.resolve("umo-1")
        second = await resolver.resolve("umo-2")

        self.assertEqual(first, PersonaContext("daily", "daily prompt"))
        self.assertEqual(second, first)

    async def test_setting_is_stripped(self):
        resolver = PersonaResolver(
            make_context(), settings_provider=lambda: {"override_persona": "  daily  "}
        )

        resolved = await resolver.resolve("umo-1")

        self.assertEqual(resolved, PersonaContext("daily", "daily prompt"))

    async def test_unknown_persona_falls_back_to_session_persona(self):
        resolver = PersonaResolver(
            make_context(), settings_provider=lambda: {"override_persona": "typo"}
        )

        with self.assertLogs("astrbot", level="WARNING") as captured:
            resolved = await resolver.resolve("umo-1")

        self.assertEqual(resolved, PersonaContext("alice", "alice prompt"))
        self.assertIn("typo", "\n".join(captured.output))

    async def test_no_settings_provider_keeps_per_session_persona(self):
        resolver = PersonaResolver(make_context())

        resolved = await resolver.resolve("umo-1")

        self.assertEqual(resolved, PersonaContext("alice", "alice prompt"))

    async def test_settings_provider_error_keeps_per_session_persona(self):
        def boom():
            raise RuntimeError("config unavailable")

        resolver = PersonaResolver(make_context(), settings_provider=boom)

        resolved = await resolver.resolve("umo-1")

        self.assertEqual(resolved, PersonaContext("alice", "alice prompt"))


if __name__ == "__main__":
    unittest.main()
