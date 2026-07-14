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
            "You are a strict long-term timeline planner. Return exactly one JSON object and no Markdown or explanation.\n"
            "The top-level object must contain a stages array with 1 to 3 stages. Never output null for required fields.\n"
            "Stage rules:\n"
            "- Every stage must contain id, name, kind, start_date, end_date, priority, summary, weekly_rules, "
            "special_dates, special_periods, milestones, and constraints.\n"
            "- id and name must be non-empty strings; ids must be unique. kind must be academic, project, or custom.\n"
            "- start_date and end_date must use YYYY-MM-DD. The first stage must start on the requested date; each later "
            "stage must start on the day after the previous stage ends. Stages must not overlap or leave gaps.\n"
            "- priority must be a JSON integer from 0 to 100. Never use text values such as high, medium, or low.\n"
            "- summary must be a string. constraints must be an array of strings; use [] when empty.\n"
            "Recurring event rules for weekly_rules:\n"
            "- Each item must contain weekdays, start, end, title, location, participants, and required.\n"
            "- weekdays must be a non-empty JSON array of unique integers from 1 to 7: 1=Monday, 2=Tuesday, "
            "3=Wednesday, 4=Thursday, 5=Friday, 6=Saturday, 7=Sunday. Never use weekday names, strings, 0, null, or [].\n"
            "- start and end must be non-empty HH:MM strings and start must be earlier than end. title must be non-empty.\n"
            "- location must be a string, participants must be an array of strings, and required must be a JSON boolean.\n"
            "Special date rules for special_dates:\n"
            "- Each item uses the same event fields as weekly_rules except weekdays, plus date in YYYY-MM-DD.\n"
            "- date must fall within its stage. start and end must be non-empty HH:MM strings with start earlier than end.\n"
            "Special period rules for special_periods:\n"
            "- Each item must contain a non-empty name, start_date, end_date, and constraints array.\n"
            "- Both dates must fall within the stage and start_date must not be later than end_date.\n"
            "Milestone rules for milestones:\n"
            "- Each item must contain date, a non-empty title, and lead_days as a non-negative JSON integer.\n"
            "- The milestone date must fall within its stage.\n"
            "Use [] instead of placeholder objects when a list has no valid items. Before responding, self-check every field, "
            "type, enum, date range, weekday, time, and stage boundary against all rules above."
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
