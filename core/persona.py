from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from astrbot.api import logger


@dataclass(slots=True, frozen=True)
class PersonaContext:
    id: str
    prompt: str


class PersonaResolver:
    def __init__(self, context, settings_provider: Callable[[], dict] | None = None):
        self.context = context
        self._settings_provider = settings_provider

    def _override_id(self) -> str:
        """返回「日程专用人格」填的 persona_id；留空返回空串。

        留空即按各会话人格分别生成。这个 id 指向用户自己在 AstrBot 里建的一个简短人格
        （只写身份、作息、居住地、风格，不写语言规则和发言规范）。填了就统一用它生成
        日程、穿搭、大时间表，plan_key 也跟着统一，于是所有群和私聊共用同一份日程。
        """
        provider = self._settings_provider
        if provider is None:
            return ""
        try:
            settings = provider() or {}
        except Exception:
            return ""
        if not isinstance(settings, dict):
            return ""
        return str(settings.get("override_persona", "") or "").strip()

    async def _resolve_stored(self, persona_id: str) -> PersonaContext | None:
        """按 persona_id 取一个已存人格的正文；取不到返回 None。"""
        try:
            persona = await self.context.persona_manager.get_persona(persona_id)
            prompt = str(getattr(persona, "system_prompt", "") or "").strip()
            if prompt:
                return PersonaContext(str(persona_id), prompt)
        except Exception:
            pass
        try:
            persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
            if isinstance(persona, dict):
                prompt = str(persona.get("prompt", "") or "").strip()
                if prompt:
                    return PersonaContext(str(persona_id), prompt)
        except Exception:
            pass
        return None

    async def resolve(self, umo: str | None) -> PersonaContext:
        override_id = self._override_id()
        if override_id:
            override = await self._resolve_stored(override_id)
            if override is not None:
                return override
            # fail-open：ID 填错不该让当天完全没有日程，退回会话人格继续跑。
            logger.warning(
                "[虚拟人生] 日程专用人格 %s 解析失败，本次回退会话人格", override_id
            )

        conversation_persona_id = None
        if umo:
            try:
                conversation_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                if conversation_id:
                    conversation = await self.context.conversation_manager.get_conversation(umo, conversation_id)
                    conversation_persona_id = getattr(conversation, "persona_id", None)
            except Exception as exc:
                logger.warning("[虚拟人生] 读取会话人格失败 %s: %s", umo, exc)

        if conversation_persona_id and conversation_persona_id != "[%None]":
            try:
                persona = await self.context.persona_manager.get_persona(conversation_persona_id)
                prompt = str(getattr(persona, "system_prompt", "") or "").strip()
                if prompt:
                    return PersonaContext(str(conversation_persona_id), prompt)
            except Exception:
                pass

        try:
            persona = await self.context.persona_manager.get_default_persona_v3(umo=umo)
            persona_id = str(persona.get("name", "default"))
            prompt = str(persona.get("prompt", "") or "You are a helpful and friendly assistant.")
            return PersonaContext(persona_id, prompt)
        except Exception as exc:
            logger.warning("[虚拟人生] 回退默认人格: %s", exc)
            return PersonaContext("default", "You are a helpful and friendly assistant.")

    async def resolve_id(self, persona_id: str, fallback_umo: str | None = None) -> PersonaContext:
        persona = await self._resolve_stored(persona_id)
        if persona is not None:
            return persona
        resolved = await self.resolve(fallback_umo)
        if resolved.id != persona_id:
            raise RuntimeError(f"无法加载已批准人格 {persona_id}")
        return resolved
