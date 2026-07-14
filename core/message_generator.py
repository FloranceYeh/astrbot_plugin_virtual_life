from __future__ import annotations

import json
from datetime import datetime

from astrbot.api import logger

from .persona import PersonaContext


class ProactiveMessageGenerator:
    def __init__(self, context, config):
        self.context = context
        self.config = config

    async def generate(
        self,
        *,
        umo: str,
        persona: PersonaContext,
        current_time: datetime,
        current_state: str,
        intent: str,
        unanswered_count: int,
    ) -> str:
        settings = self.config.get("delivery_settings", {}) or {}
        template = str(settings.get("proactive_prompt", ""))
        task_prompt = template.format(
            current_time=current_time.strftime("%Y-%m-%d %H:%M"),
            current_state=current_state,
            intent=intent,
            unanswered_count=unanswered_count,
        )
        history = await self._recent_history(umo, int(settings.get("recent_chat_messages", 8)))
        prompt = (
            f"<persona>\n{persona.prompt}\n</persona>\n\n"
            f"<recent_conversation>\n{history}\n</recent_conversation>\n\n{task_prompt}"
        )
        schedule_settings = self.config.get("schedule_settings", {}) or {}
        provider_id = str(schedule_settings.get("proactive_llm_provider") or "").strip()
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        provider = provider or self.context.get_using_provider()
        if not provider:
            raise RuntimeError("no LLM provider available")
        response = await provider.text_chat(prompt, session_id=f"proactive_message::{umo}")
        for key in ("completion_text", "completion", "text", "content"):
            value = getattr(response, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError("LLM returned empty proactive message")

    async def _recent_history(self, umo: str, count: int) -> str:
        if count <= 0:
            return "无"
        try:
            conversation_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not conversation_id:
                return "无"
            conversation = await self.context.conversation_manager.get_conversation(umo, conversation_id)
            raw = getattr(conversation, "history", "[]")
            history = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(history, list):
                return "无"
            lines = []
            for item in history[-count:]:
                if not isinstance(item, dict):
                    continue
                role = item.get("role", "unknown")
                content = item.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
                    )
                lines.append(f"{role}: {content}")
            return "\n".join(lines) or "无"
        except Exception as exc:
            logger.debug("[主动虚拟日程] 读取历史失败 %s: %s", umo, exc)
            return "无"
