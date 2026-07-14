from __future__ import annotations

import hashlib
import random
from datetime import date

import holidays

from astrbot.api import logger

from .long_term import validate_stage_bundle
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

    async def generate(
        self,
        target: date,
        persona: PersonaContext,
        extra: str = "",
        history_plans: list[DailyPlan] | None = None,
        long_term_context: str = "",
    ) -> DailyPlan:
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
            prompt += self._format_history(history_plans or [])
            if long_term_context:
                prompt += "\n\n" + long_term_context
            if extra:
                prompt += f"\n\n管理员补充要求（最高优先级）：{extra}"

            attempts = max(1, int(self._settings().get("generation_retries", 1)) + 1)
            last_error = ""
            for attempt in range(attempts):
                try:
                    raw = await self._call_llm(
                        prompt,
                        f"proactive_daily_{persona.id}_{target.isoformat()}",
                    )
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

    @staticmethod
    def _format_history(plans: list[DailyPlan]) -> str:
        if not plans:
            return "\n\n近期同人格日程：无。"
        blocks = []
        for plan in plans:
            activities = "；".join(
                f"{item.start}-{item.end} {item.activity}"
                for item in plan.timeline
            )
            blocks.append(
                f"- {plan.date}｜主题：{plan.theme}｜心情：{plan.mood}｜"
                f"穿搭：{plan.outfit}｜活动：{activities}"
            )
        return (
            "\n\n近期同人格日程（仅用于保持生活连续性并避免重复）：\n"
            + "\n".join(blocks)
            + "\n新日程可以延续尚未完成的兴趣或状态，但不要照抄相同主题、穿搭和活动组合。"
        )

    async def _call_llm(self, prompt: str, session_id: str) -> str:
        provider_id = str(self._settings().get("schedule_llm_provider") or "").strip()
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        provider = provider or self.context.get_using_provider()
        if not provider:
            raise RuntimeError("no LLM provider available")
        system_prompt = str(self._settings().get("generation_system_prompt", "") or "").strip()
        response = await provider.text_chat(
            prompt=prompt,
            session_id=session_id,
            system_prompt=system_prompt or None,
        )
        for key in ("completion_text", "completion", "text", "content"):
            value = getattr(response, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError("LLM returned empty completion")

    async def generate_long_term_timeline(
        self,
        persona: PersonaContext,
        *,
        start_date: date,
        previous_stage: dict | None = None,
        requirements: str = "",
    ) -> list[dict]:
        previous_text = "无"
        if previous_stage:
            previous_text = (
                f"{previous_stage['name']}（{previous_stage['kind']}，"
                f"{previous_stage['start_date']} 至 {previous_stage['end_date']}）："
                f"{previous_stage.get('summary') or '无说明'}"
            )
        system_prompt = (
            "priority \u5fc5\u987b\u662f 0 \u81f3 100 \u7684 JSON \u6574\u6570\uff0c\u7981\u6b62\u4f7f\u7528 high, medium, low \u7b49\u6587\u672c\u503c\u3002"
            "你是严格的大时间表规划器。只输出一个 JSON 对象，不要 Markdown、代码块或解释。"
            "JSON 顶层必须是 stages 数组，包含 1 至 3 个首尾连续的阶段。"
            "每个阶段必须包含 id、name、kind、start_date、end_date、priority、summary、"
            "weekly_rules、special_dates、special_periods、milestones、constraints。"
            "kind 只能是 academic、project、custom。日期使用 YYYY-MM-DD，时间使用 HH:MM。"
            "weekly_rules 包含 weekdays、start、end、title、location、participants、required；"
            "special_dates 额外包含 date；special_periods 包含 name、start_date、end_date、constraints；"
            "milestones 包含 date、title、lead_days。首阶段必须从指定日期开始，后续阶段必须从前一阶段结束次日开始。"
        )
        prompt = (
            f"起始日期：{start_date.isoformat()}\n"
            f"人格设定：\n{persona.prompt}\n\n"
            f"前一阶段：{previous_text}\n"
            f"管理员要求：{requirements or '无'}\n\n"
            "请根据人格自行判断适合的阶段类型和合理持续时间。学生角色可生成学期、假期、考试周等校历；"
            "上班族角色可生成项目周期、冲刺期、发布期等工期；其他角色使用 custom。"
        )
        raw = await self._call_llm_with_system(
            prompt,
            f"long_term_{persona.id}_{start_date.isoformat()}",
            system_prompt,
        )
        return validate_stage_bundle(extract_json_object(raw), persona.id, required_start=start_date)

    async def _call_llm_with_system(self, prompt: str, session_id: str, system_prompt: str) -> str:
        provider_id = str(self._settings().get("schedule_llm_provider") or "").strip()
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        provider = provider or self.context.get_using_provider()
        if not provider:
            raise RuntimeError("no LLM provider available")
        response = await provider.text_chat(prompt=prompt, session_id=session_id, system_prompt=system_prompt)
        for key in ("completion_text", "completion", "text", "content"):
            value = getattr(response, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError("LLM returned empty completion")
