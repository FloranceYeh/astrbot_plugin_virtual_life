from __future__ import annotations

import json
import re
from datetime import datetime

from astrbot.api import logger
from astrbot.core.agent.message import (
    AssistantMessageSegment,
    TextPart,
    UserMessageSegment,
)

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
        relationship_context: str = "",
        timeline_context: str = "",
    ) -> str:
        settings = self.config.get("delivery_settings", {}) or {}
        template = str(settings.get("proactive_prompt", ""))
        unanswered_hint = self._build_unanswered_hint(settings, unanswered_count)
        task_prompt = template.format(
            current_time=current_time.strftime("%Y-%m-%d %H:%M"),
            current_state=current_state,
            intent=intent,
            unanswered_count=unanswered_count,
            unanswered_hint=unanswered_hint,
        )
        history = await self._recent_history(umo, int(settings.get("recent_chat_messages", 8)))
        prompt = (
            f"<persona>\n{persona.prompt}\n</persona>\n\n"
            f"<recent_conversation>\n{history}\n</recent_conversation>\n\n{task_prompt}"
        )
        if timeline_context:
            prompt += "\n\n" + timeline_context
        if relationship_context:
            prompt += "\n\n" + relationship_context
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

    @staticmethod
    def _build_unanswered_hint(settings: dict, count: int) -> str:
        method = str(
            settings.get("unanswered_hint_method", "segmented") or "segmented"
        ).strip().lower()
        if method == "placeholder":
            placeholder = str(
                settings.get("unanswered_hint_placeholder_template", "") or ""
            ).strip()
            if placeholder:
                try:
                    return placeholder.format(unanswered_count=max(0, int(count)))
                except (KeyError, IndexError, ValueError, TypeError):
                    return ""
            return ""
        return ProactiveMessageGenerator._parse_unanswered_hint(
            settings.get("unanswered_hint_template", ""), count
        )

    @staticmethod
    def _parse_unanswered_hint(template: object, count: int) -> str:
        """Parse a segmented unanswered hint template.

        Every line has the form ``<threshold>: <text>`` and applies when the
        unanswered count is at least ``threshold``. The segment with the largest
        threshold not exceeding the count wins; unparseable or empty templates
        produce an empty hint.
        """
        try:
            count = max(0, int(count))
        except (TypeError, ValueError):
            count = 0
        segments: list[tuple[int, str]] = []
        for line in str(template).splitlines():
            match = re.match(r"^\s*(\d+)\s*[:：]\s*(.+?)\s*$", line)
            if not match:
                continue
            text = match.group(2).strip()
            if text:
                segments.append((int(match.group(1)), text))
        if not segments:
            return ""
        segments.sort(key=lambda item: item[0])
        selected = ""
        for threshold, text in segments:
            if threshold <= count:
                selected = text
            else:
                break
        return selected

    async def record_conversation(
        self,
        *,
        umo: str,
        persona_id: str,
        intent: str,
        assistant_text: str,
    ) -> bool:
        manager = self.context.conversation_manager
        try:
            conversation_id = await manager.get_curr_conversation_id(umo)
            if not conversation_id:
                conversation_id = await manager.new_conversation(umo, persona_id=persona_id)
            if not conversation_id:
                logger.warning("[虚拟人生] 主动消息未写入上下文：无法创建对话 umo=%s", umo)
                return False
            user_message = UserMessageSegment(content=[TextPart(text=self._history_note(intent))])
            assistant_message = AssistantMessageSegment(content=[TextPart(text=assistant_text)])
            await manager.add_message_pair(
                cid=conversation_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
            logger.info("[虚拟人生] 主动消息已写入对话上下文 umo=%s conversation=%s", umo, conversation_id)
            return True
        except Exception as exc:
            logger.error("[虚拟人生] 主动消息写入对话上下文失败 umo=%s: %s", umo, exc)
            return False

    def _history_note(self, intent: str) -> str:
        settings = self.config.get("delivery_settings", {}) or {}
        template = str(settings.get("proactive_history_note_template", "")).strip()
        default = f"（你主动发起的消息）\n背景：{intent}"
        if not template:
            return default
        try:
            return template.format(intent=intent)
        except (KeyError, IndexError, ValueError):
            return default

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
            logger.debug("[虚拟人生] 读取历史失败 %s: %s", umo, exc)
            return "无"
