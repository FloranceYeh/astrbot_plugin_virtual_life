from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from datetime import date

import holidays

from astrbot.api import logger

from .long_term import validate_stage_bundle
from .models import DailyPlan, Outfit, ProactiveWindow, TimelineItem
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
        candidates = (
            [str(value) for value in values] if isinstance(values, list) else []
        )
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
        """Generate a complete plan from the combined schedule and outfit prompts.

        Args:
            target: Date to generate.
            persona: Persona used to guide the plan.
            extra: Optional administrator requirements.
            history_plans: Recent plans used to reduce repetition.
            long_term_context: Optional long-term stage constraints.

        Returns:
            A complete validated plan, or a failed placeholder after all retries.
        """
        pool = self._pool()
        theme = self._pick(pool.get("themes"), "日常日")
        mood = self._pick(pool.get("moods"), "平静")
        outfit_style = self._pick(pool.get("outfit_styles"), "日常休闲风")
        try:
            return await self._generate_plan(
                target,
                persona,
                mode="完整生成",
                include_schedule=True,
                include_outfit=True,
                theme=theme,
                mood=mood,
                outfit_style=outfit_style,
                history=self._format_history(history_plans or []),
                long_term_context=long_term_context,
                requirements=extra,
            )
        except RuntimeError as exc:
            return DailyPlan(
                date=target.isoformat(),
                persona_id=persona.id,
                theme="生成失败",
                mood="未知",
                outfit=Outfit(summary="未知", items=(), style="未知"),
                timeline=(),
                status="failed",
                revision=hashlib.sha1(str(exc).encode("utf-8")).hexdigest()[:12],
            )

    async def rewrite_schedule(
        self,
        plan: DailyPlan,
        persona: PersonaContext,
        *,
        extra: str = "",
        long_term_context: str = "",
    ) -> DailyPlan:
        """Rewrite timeline-related fields while preserving plan identity and outfit.

        Args:
            plan: Existing valid daily plan.
            persona: Persona used to guide the rewritten activities.
            extra: Optional administrator requirements for the timeline.
            long_term_context: Optional long-term stage constraints.

        Returns:
            A validated plan with a new timeline, windows, budget, and revision.

        Raises:
            RuntimeError: If generation is already running or every attempt fails.
        """
        outfit_context, timeline = self._format_plan_context(plan)
        return await self._generate_plan(
            date.fromisoformat(plan.date),
            persona,
            mode="重写日程",
            include_schedule=True,
            include_outfit=False,
            theme=plan.theme,
            mood=plan.mood,
            outfit_style=plan.outfit.style,
            base_plan=plan,
            history=self._format_history([plan]),
            long_term_context=long_term_context,
            outfit_context=outfit_context,
            timeline=timeline,
            requirements=extra,
        )

    async def rewrite_outfit(
        self,
        plan: DailyPlan,
        persona: PersonaContext,
        *,
        extra: str = "",
    ) -> DailyPlan:
        """Rewrite only the outfit while preserving all schedule-related fields.

        Args:
            plan: Existing valid daily plan.
            persona: Persona used to guide the rewritten outfit.
            extra: Optional administrator requirements for the outfit.

        Returns:
            A validated plan with a new outfit and revision.

        Raises:
            RuntimeError: If generation is already running or every attempt fails.
        """
        raw_styles = self._pool().get("outfit_styles")
        styles = (
            [str(value).strip() for value in raw_styles]
            if isinstance(raw_styles, list)
            else []
        )
        styles = [value for value in styles if value]
        explicitly_requested = next((style for style in styles if style in extra), None)
        alternatives = [style for style in styles if style != plan.outfit.style]
        outfit_style = explicitly_requested or self._pick(
            alternatives,
            plan.outfit.style or "日常休闲风",
        )
        outfit_context, timeline = self._format_plan_context(plan)
        return await self._generate_plan(
            date.fromisoformat(plan.date),
            persona,
            mode="重写穿搭",
            include_schedule=False,
            include_outfit=True,
            theme=plan.theme,
            mood=plan.mood,
            outfit_style=outfit_style,
            base_plan=plan,
            outfit_context=outfit_context,
            timeline=timeline,
            requirements=extra,
        )

    async def _generate_plan(
        self,
        target: date,
        persona: PersonaContext,
        *,
        mode: str,
        include_schedule: bool,
        include_outfit: bool,
        theme: str,
        mood: str,
        outfit_style: str,
        base_plan: DailyPlan | None = None,
        history: str = "",
        long_term_context: str = "",
        outfit_context: str = "",
        timeline: str = "",
        requirements: str = "",
    ) -> DailyPlan:
        """Generate and validate a full plan or selected plan components.

        Args:
            target: Date being generated.
            persona: Persona used to guide generation.
            mode: Human-readable operation name exposed to prompt templates.
            include_schedule: Whether schedule prompt and fields are included.
            include_outfit: Whether outfit prompt and field are included.
            theme: Selected or preserved theme.
            mood: Selected or preserved mood.
            outfit_style: Selected or preserved outfit style.
            base_plan: Existing plan used for a partial rewrite.
            history: Formatted recent or current plan context.
            long_term_context: Formatted long-term stage context.
            outfit_context: Formatted existing outfit context.
            timeline: Formatted existing timeline context.
            requirements: Optional administrator requirements.

        Returns:
            A validated complete or partially replaced plan.

        Raises:
            RuntimeError: If generation is concurrent, misconfigured, or invalid after retries.
        """
        key = f"{target.isoformat()}::{persona.id}"
        if key in self.generating:
            raise RuntimeError("plan generation already running")
        self.generating.add(key)
        try:
            settings = self._settings()
            variables = {
                "mode": mode,
                "date": target.isoformat(),
                "weekday": "星期" + "一二三四五六日"[target.weekday()],
                "holiday": self._holiday(target),
                "persona": persona.prompt,
                "theme": theme,
                "mood": mood,
                "outfit_style": outfit_style,
                "history": history or "无",
                "long_term_context": long_term_context or "无",
                "outfit_context": outfit_context or "无",
                "timeline": timeline or "无",
                "current_outfit": outfit_context or "无",
                "requirements": requirements or "无",
            }
            components = []
            if include_schedule:
                components.append("schedule")
            if include_outfit:
                components.append("outfit")
            system_prompts = [
                str(
                    settings.get(f"{component}_generation_system_prompt", "") or ""
                ).strip()
                for component in components
            ]
            prompt_templates = [
                str(settings.get(f"{component}_prompt_template", "") or "").strip()
                for component in components
            ]
            if any(not value for value in system_prompts + prompt_templates):
                raise RuntimeError("schedule or outfit generation prompt is empty")
            system_prompt = "\n\n".join(system_prompts)
            prompt = "\n\n".join(
                template.format(**variables) for template in prompt_templates
            )

            attempts = max(1, int(settings.get("generation_retries", 1)) + 1)
            last_error = ""
            for attempt in range(attempts):
                try:
                    raw = await self._call_llm_with_system(
                        prompt,
                        f"daily_{'_'.join(components)}_{persona.id}_{target.isoformat()}",
                        system_prompt,
                    )
                    payload = extract_json_object(raw)
                    raw_timeline: list[dict] = []
                    raw_windows: list[dict] = []
                    bonus: dict = {}
                    if include_schedule:
                        for required in (
                            "timeline",
                            "proactive_windows",
                            "budget_bonus",
                        ):
                            if required not in payload:
                                raise ValueError(f"missing schedule field: {required}")
                        raw_timeline = payload["timeline"]
                        raw_windows = payload["proactive_windows"]
                        bonus = payload["budget_bonus"]
                        if not isinstance(raw_timeline, list) or not all(
                            isinstance(item, dict) for item in raw_timeline
                        ):
                            raise ValueError("timeline must be an array of objects")
                        if not isinstance(raw_windows, list) or not all(
                            isinstance(item, dict) for item in raw_windows
                        ):
                            raise ValueError(
                                "proactive_windows must be an array of objects"
                            )
                        if not isinstance(bonus, dict):
                            raise ValueError("budget_bonus must be an object")
                    outfit: dict | None = None
                    if include_outfit:
                        outfit = payload.get("outfit")
                        if not isinstance(outfit, dict):
                            raise ValueError("missing structured outfit")
                        outfit["style"] = outfit_style
                    revision = hashlib.sha1(
                        f"{base_plan.revision if base_plan else ''}\n{mode}\n{raw}".encode()
                    ).hexdigest()[:12]
                    if base_plan is None:
                        payload["date"] = target.isoformat()
                        payload["persona_id"] = persona.id
                        payload["theme"] = theme
                        payload["mood"] = mood
                        payload["revision"] = revision
                        return DailyPlan.from_dict(payload)

                    rewritten = base_plan
                    if include_schedule:
                        rewritten = replace(
                            rewritten,
                            timeline=tuple(
                                TimelineItem.from_dict(item) for item in raw_timeline
                            ),
                            proactive_windows=tuple(
                                ProactiveWindow.from_dict(item) for item in raw_windows
                            ),
                            private_bonus=int(bonus.get("private", 0)),
                            group_bonus=int(bonus.get("group", 0)),
                        )
                    if include_outfit:
                        rewritten = replace(rewritten, outfit=Outfit.from_dict(outfit))
                    return replace(rewritten, revision=revision)
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning(
                        "[Virtual Life] Plan generation validation failed mode=%s persona=%s attempt=%s: %s",
                        mode,
                        persona.id,
                        attempt + 1,
                        exc,
                    )
                    prompt += f"\n\n上一次输出无效：{last_error}。请修正错误并重新输出完整 JSON 对象。"
            raise RuntimeError(last_error or "plan generation failed")
        finally:
            self.generating.discard(key)

    @staticmethod
    def _format_plan_context(plan: DailyPlan) -> tuple[str, str]:
        """Format outfit and timeline context for configurable prompt templates.

        Args:
            plan: Plan whose preserved context should be described.

        Returns:
            Outfit context followed by timeline context.
        """
        outfit_items = "；".join(
            f"{item.category}={item.name}"
            + (f"（{item.details}）" if item.details else "")
            for item in plan.outfit.items
        )
        outfit_context = f"{plan.outfit.style}｜{plan.outfit.summary}｜{outfit_items}"
        timeline = "；".join(
            f"{item.start}-{item.end} {item.activity}"
            + (f" @ {item.location}" if item.location else "")
            for item in plan.timeline
        )
        return outfit_context, timeline

    @staticmethod
    def _format_history(plans: list[DailyPlan]) -> str:
        if not plans:
            return "\n\n近期同人格日程：无。"
        blocks = []
        for plan in plans:
            _, activities = DailyPlanGenerator._format_plan_context(plan)
            blocks.append(
                f"- {plan.date}｜主题：{plan.theme}｜心情：{plan.mood}｜"
                f"穿搭风格：{plan.outfit.style}｜穿搭：{plan.outfit.summary}｜活动：{activities}"
            )
        return (
            "\n\n近期同人格日程（仅用于保持生活连续性并避免重复）：\n"
            + "\n".join(blocks)
            + "\n新日程可以延续尚未完成的兴趣或状态，但不要照抄相同主题、穿搭和活动组合。"
        )

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
        return validate_stage_bundle(
            extract_json_object(raw), persona.id, required_start=start_date
        )

    async def _call_llm_with_system(
        self, prompt: str, session_id: str, system_prompt: str
    ) -> str:
        provider_id = str(self._settings().get("schedule_llm_provider") or "").strip()
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        provider = provider or self.context.get_using_provider()
        if not provider:
            raise RuntimeError("no LLM provider available")
        response = await provider.text_chat(
            prompt=prompt, session_id=session_id, system_prompt=system_prompt
        )
        for key in ("completion_text", "completion", "text", "content"):
            value = getattr(response, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError("LLM returned empty completion")
