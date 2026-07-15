from __future__ import annotations

from dataclasses import dataclass

from astrbot.api import logger


@dataclass(slots=True, frozen=True)
class PersonaContext:
    id: str
    prompt: str


class PersonaResolver:
    def __init__(self, context):
        self.context = context

    async def resolve(self, umo: str | None) -> PersonaContext:
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
        try:
            persona = await self.context.persona_manager.get_persona(persona_id)
            prompt = str(getattr(persona, "system_prompt", "") or "").strip()
            if prompt:
                return PersonaContext(persona_id, prompt)
        except Exception:
            pass
        try:
            persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)
            if persona:
                return PersonaContext(persona_id, str(persona.get("prompt", "") or ""))
        except Exception:
            pass
        resolved = await self.resolve(fallback_umo)
        if resolved.id != persona_id:
            raise RuntimeError(f"无法加载已批准人格 {persona_id}")
        return resolved
