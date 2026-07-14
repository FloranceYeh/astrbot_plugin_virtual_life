from __future__ import annotations

import hashlib
import random
from datetime import date

import holidays

from astrbot.api import logger

from .models import DailyPlan
from .persona import PersonaContext
from .utils import extract_json_object


class DailyPlanGenerator:
    def __init__(self, context, config):
        self.context = context
        self.config = config
        self.generating: set[str] = set()

    def _settings(self) -> dict:
        return self.config.get("schedule_settings", {}) or {}

    def _pool(self) -> dict:
        return self.config.get("creative_pool", {}) or {}

    @staticmethod
    def _pick(values: object, fallback: str) -> str:
        candidates = [str(value) for value in values] if isinstance(values, list) else []
        return random.choice(candidates) if candidates else fallback

    def _holiday(self, target: date) -> str:
        try:
            return str(holidays.country_holidays("CN").get(target, "普通日期"))
        except Exception:
            return "普通日期"

    async def generate(self, target: date, persona: PersonaContext, extra: str = "") -> DailyPlan:
        key = f"{target.isoformat()}::{persona.id}"
        if key in self.generating:
            raise RuntimeError("plan generation already running")
        self.generating.add(key)
        try:
            pool = self._pool()
            theme = self._pick(pool.get("themes"), "日常日")
            mood = self._pick(pool.get("moods"), "平静")
            outfit_style = self._pick(pool.get("outfit_styles"), "日常休闲风")
            prompt_template = str(self._settings().get("prompt_template", ""))
            prompt = prompt_template.format(
                date=target.isoformat(),
                weekday="星期" + "一二三四五六日"[target.weekday()],
                holiday=self._holiday(target),
                persona=persona.prompt,
                theme=theme,
                mood=mood,
                outfit_style=outfit_style,
            )
            if extra:
                prompt += f"\n\n管理员补充要求（最高优先级）：{extra}"

            attempts = max(1, int(self._settings().get("generation_retries", 1)) + 1)
            last_error = ""
            for attempt in range(attempts):
                try:
                    raw = await self._call_llm(prompt, f"proactive_daily_{persona.id}_{target.isoformat()}")
                    payload = extract_json_object(raw)
                    payload["date"] = target.isoformat()
                    payload["persona_id"] = persona.id
                    payload["revision"] = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
                    return DailyPlan.from_dict(payload)
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning(
                        "[主动虚拟日程] 日程生成校验失败 persona=%s attempt=%s: %s",
                        persona.id,
                        attempt + 1,
                        exc,
                    )
                    prompt += (
                        "\n\n上一次输出无效："
                        + last_error
                        + "。请重新输出完整 JSON，确保时间线从 00:00 连续覆盖到 24:00、无重叠且引用 ID 有效。"
                    )
            return DailyPlan(
                date=target.isoformat(),
                persona_id=persona.id,
                theme="生成失败",
                mood="未知",
                outfit="未知",
                timeline=(),
                status="failed",
                revision=hashlib.sha1(last_error.encode("utf-8")).hexdigest()[:12],
            )
        finally:
            self.generating.discard(key)

    async def _call_llm(self, prompt: str, session_id: str) -> str:
        provider_id = str(self._settings().get("llm_provider", "") or "").strip()
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        provider = provider or self.context.get_using_provider()
        if not provider:
            raise RuntimeError("no LLM provider available")
        response = await provider.text_chat(prompt, session_id=session_id)
        for key in ("completion_text", "completion", "text", "content"):
            value = getattr(response, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError("LLM returned empty completion")
