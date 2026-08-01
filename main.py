from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Record, Reply
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.filter.command import CommandFilter, GreedyStr
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.regex import RegexFilter

from .core.context_injection import SmartContextInjector
from .core.generator import DailyPlanGenerator
from .core.image_renderer import ScheduleImageRenderer
from .core.long_term import LongTermTimelineStore, validate_stage_bundle
from .core.message_generator import ProactiveMessageGenerator
from .core.message_segmenter import ProactiveMessageSegmenter
from .core.models import (
    DailyPlan,
    ProactiveWindow,
    ScheduleRequirement,
)
from .core.persona import PersonaContext, PersonaResolver
from .core.proactive import ProactivePolicy, session_kind
from .core.reply_delay import (
    QueuedMessage,
    ReplyDelayCoordinator,
    ReplyDelayPolicy,
    format_delay_context,
    format_queued_messages,
)
from .core.runtime import SchedulerRuntime
from .core.storage import PluginStorage
from .core.utils import (
    deterministic_int,
    deterministic_probability,
    format_outfit,
    format_plan,
    format_timeline,
    next_available_at,
    now_in,
    parse_datetime,
    parse_schedule_date_range,
    prune_date_keys,
    timeline_item_at,
)
from .core.window_schedule import proactive_window_offset_minutes


class ProactiveVirtualDailyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_virtual_life")
        self.storage = PluginStorage(self.data_dir)
        self.personas = PersonaResolver(context)
        self.plan_generator = DailyPlanGenerator(context, config)
        self.message_generator = ProactiveMessageGenerator(context, config)
        delivery_settings = self.config.get("delivery_settings", {}) or {}
        self.message_segmenter = ProactiveMessageSegmenter(
            delivery_settings.get("segmented_reply_settings", {}) or {},
        )
        self.long_term = LongTermTimelineStore(self.data_dir)
        self.smart_context_injector = SmartContextInjector(
            self.config.get("smart_context_injection", {}) or {},
        )
        self.reply_delay_policy = ReplyDelayPolicy(
            self.config.get("reply_delay_settings", {}) or {},
        )
        self.reply_delay_coordinator = ReplyDelayCoordinator()
        self.image_renderer = ScheduleImageRenderer(
            self.data_dir,
            self.html_render,
            self.config.get("image_settings", {}) or {},
        )
        self.timezone = self._resolve_timezone()
        self.policy = ProactivePolicy(config, self.storage, self.timezone)
        self.runtime = SchedulerRuntime(self.timezone)
        self.refresh_lock = asyncio.Lock()
        self.delivery_locks: dict[str, asyncio.Lock] = {}
        self._window_retry_counts: dict[tuple[str, str, str], int] = {}
        self.renewal_attempts: dict[str, int] = {}
        self._personal_network_unavailable_logged = False

    def _resolve_timezone(self) -> ZoneInfo:
        try:
            name = str(self.context.get_config().get("timezone") or "Asia/Shanghai")
            return ZoneInfo(name)
        except Exception:
            return ZoneInfo("Asia/Shanghai")

    async def initialize(self) -> None:
        await self.storage.load()
        await self.long_term.load()
        await self.image_renderer.cleanup()
        schedule_settings = self.config.get("schedule_settings", {}) or {}
        generate_time = str(schedule_settings.get("generate_time", "07:00"))
        self.runtime.start(generate_time, self._daily_refresh)
        await self._refresh_all(force=False)
        logger.info("[虚拟人生] 插件已启动，时区=%s", self.timezone.key)

    async def terminate(self) -> None:
        self.runtime.stop()
        self.reply_delay_coordinator.clear()
        await self.storage.save_plans()
        await self.storage.save_sessions()

    @staticmethod
    def _umo_hash(umo: str) -> str:
        return hashlib.sha1(umo.encode("utf-8")).hexdigest()[:12]

    def _now(self) -> datetime:
        return now_in(self.timezone)

    def _personal_network_plugin(self):
        settings = self.config.get("personal_network_integration", {}) or {}
        if not settings.get("enable", False):
            return None
        getter = getattr(self.context, "get_registered_star", None)
        metadata = (
            getter("astrbot_plugin_personal_network") if callable(getter) else None
        )
        plugin = (
            metadata.star_cls
            if metadata and metadata.activated and metadata.star_cls
            else None
        )
        required_methods = {
            "get_context_for_plugin",
            "record_life_event_from_plugin",
        }
        if plugin is None or any(
            not hasattr(plugin, method) for method in required_methods
        ):
            if not self._personal_network_unavailable_logged:
                logger.warning(
                    "[Virtual Life] Personal Network integration is enabled but a compatible plugin is unavailable."
                )
                self._personal_network_unavailable_logged = True
            return None
        self._personal_network_unavailable_logged = False
        return plugin

    async def _generate_new_character(
        self, persona: PersonaContext
    ) -> str:
        """Possibly generate a new virtual character and write it to Personal Network.

        Returns a non-empty hint string to append to schedule requirements when a
        character is successfully created, or an empty string otherwise.
        """
        settings = self.config.get("personal_network_integration", {}) or {}
        try:
            probability = max(0.0, min(1.0, float(settings.get("new_character_probability", 0.0))))
        except (TypeError, ValueError):
            probability = 0.0
        if probability <= 0.0:
            return ""
        import random as _random
        if _random.random() >= probability:
            return ""
        plugin = self._personal_network_plugin()
        if plugin is None or not hasattr(plugin, "upsert_batch_for_plugin"):
            return ""
        try:
            char = await self.plan_generator.generate_new_character(persona)
        except Exception:
            logger.exception(
                "[Virtual Life] New character generation failed persona=%s", persona.id
            )
            return ""
        ref = "new_char"
        try:
            result = await plugin.upsert_batch_for_plugin(
                persona.id,
                characters=[
                    {
                        "ref": ref,
                        "name": char["name"],
                        "bio": char["bio"],
                        "personality": char["personality"],
                        "preferences": char["preferences"],
                        "facts": char["facts"],
                    }
                ],
                relationships=[
                    {
                        "source": "persona",
                        "target": ref,
                        "type": char["relationship_type"],
                        "strength": 30,
                        "status": "active",
                        "description": char["relationship_description"],
                    }
                ],
                source="astrbot_plugin_virtual_life",
            )
            if not result.get("updated"):
                logger.warning(
                    "[Virtual Life] New character write rejected persona=%s: %s",
                    persona.id,
                    result.get("reason", "unknown"),
                )
                return ""
            char_uuid = result.get("refs", {}).get(ref, "")
            logger.info(
                "[Virtual Life] New character written to Personal Network persona=%s name=%s uuid=%s",
                persona.id,
                char["name"],
                char_uuid,
            )
        except Exception:
            logger.exception(
                "[Virtual Life] Failed to write new character to Personal Network persona=%s",
                persona.id,
            )
            return ""
        return (
            f"今天日程中应安排与 {char['name']}（{char['relationship_type']}）的相遇或互动。"
            f"关系说明：{char['relationship_description']}。"
            f"人物简介：{char['bio']}。"
            "请在时间线中自然地加入这次会面，并在对应 timeline 项目的 participant_ids 中填入该人物的 ID（从关系数据中获取）。"
        )

    async def _personal_network_participants_context(
        self, persona_id: str, participant_ids: tuple[str, ...]
    ) -> str:
        """Return a brief context string for the given participant UUIDs.

        Fetches the full network snapshot and filters by the provided IDs,
        returning a formatted string suitable for injection into the LLM prompt.
        Returns an empty string on any failure or if the feature is disabled.
        """
        if not participant_ids:
            return ""
        settings = self.config.get("personal_network_integration", {}) or {}
        if not settings.get("inject_current_participants", True):
            return ""
        plugin = self._personal_network_plugin()
        if plugin is None or not hasattr(plugin, "get_network_for_plugin"):
            return ""
        try:
            data = await plugin.get_network_for_plugin(persona_id)
        except Exception:
            logger.exception(
                "[Virtual Life] Failed to fetch network for participant injection persona=%s",
                persona_id,
            )
            return ""
        id_set = set(participant_ids)
        characters = [
            c for c in (data.get("characters") or []) if c.get("id") in id_set
        ]
        if not characters:
            return ""
        lines = ["当前时段参与人物："]
        for c in characters:
            name = c.get("name", "（未知）")
            bio = c.get("bio", "")
            personality = c.get("personality", "")
            parts = [name]
            if bio:
                parts.append(bio)
            if personality:
                parts.append(f"性格：{personality}")
            lines.append("- " + "；".join(parts))
        return "\n".join(lines)

    async def _personal_network_context(self, persona_id: str) -> str:
        plugin = self._personal_network_plugin()
        if plugin is None:
            return ""
        settings = self.config.get("personal_network_integration", {}) or {}
        try:
            max_chars = max(
                500,
                min(12000, int(settings.get("max_context_chars", 4000))),
            )
        except (TypeError, ValueError):
            max_chars = 4000
        try:
            return await plugin.get_context_for_plugin(persona_id, max_chars=max_chars)
        except Exception:
            logger.exception(
                "[Virtual Life] Failed to read Personal Network context: persona=%s",
                persona_id,
            )
            return ""

    async def _record_completed_personal_network_events(
        self, persona_id: str, now: datetime
    ) -> None:
        settings = self.config.get("personal_network_integration", {}) or {}
        if not settings.get("record_completed_events", True):
            return
        plugin = self._personal_network_plugin()
        if plugin is None:
            return
        for plan in self.storage.plans.values():
            if plan.persona_id != persona_id or plan.status != "ok":
                continue
            plan_date = date.fromisoformat(plan.date)
            for item in plan.timeline:
                if not item.participant_ids:
                    continue
                if item.end == "24:00":
                    ended_at = datetime.combine(
                        plan_date + timedelta(days=1), time.min, self.timezone
                    )
                else:
                    ended_at = datetime.combine(
                        plan_date,
                        time.fromisoformat(item.end),
                        self.timezone,
                    )
                if ended_at > now:
                    continue
                summary = item.activity
                if item.location:
                    summary += f"（地点：{item.location}）"
                try:
                    await plugin.record_life_event_from_plugin(
                        persona_id,
                        participant_ids=list(item.participant_ids),
                        event_type="virtual_schedule",
                        summary=summary,
                        occurred_at=ended_at.isoformat(),
                        importance=50,
                        emotional_tone=plan.mood,
                        source="astrbot_plugin_virtual_life",
                        source_key=f"{plan.date}:{item.id}",
                    )
                except ValueError as exc:
                    logger.warning(
                        "[Virtual Life] Skipped Personal Network event: persona=%s item=%s error=%s",
                        persona_id,
                        item.id,
                        exc,
                    )
                except Exception:
                    logger.exception(
                        "[Virtual Life] Failed to record Personal Network event: persona=%s item=%s",
                        persona_id,
                        item.id,
                    )

    async def _daily_refresh(self) -> None:
        await self._refresh_all(force=True)

    async def _refresh_all(self, *, force: bool) -> None:
        async with self.refresh_lock:
            now = self._now()
            self.storage.prune_schedule_requirements(now.date())
            await self._check_long_term_renewals(now.date())
            grouped: dict[str, tuple[PersonaContext, list[str]]] = {}
            for umo in self.policy.enabled_sessions():
                persona = await self.personas.resolve(umo)
                grouped.setdefault(persona.id, (persona, []))[1].append(umo)

            for persona, sessions in grouped.values():
                await self._record_completed_personal_network_events(persona.id, now)
                plan = await self._ensure_plan(persona, now.date(), force=force)
                for umo in sessions:
                    await self._schedule_session(umo, persona, plan)

            keep_days = int(
                (self.config.get("schedule_settings", {}) or {}).get("history_days", 14)
            )
            self.storage.plans = prune_date_keys(
                self.storage.plans, keep_days, now.date()
            )
            await self.storage.save_plans()
            await self.storage.save_sessions()

    async def _ensure_plan_for_umo(
        self, umo: str, *, force: bool = False, extra: str = ""
    ) -> tuple[PersonaContext, DailyPlan]:
        persona = await self.personas.resolve(umo)
        plan = await self._ensure_plan(
            persona, self._now().date(), force=force, extra=extra
        )
        return persona, plan

    async def _ensure_plan(
        self,
        persona: PersonaContext,
        target: date,
        *,
        force: bool = False,
        extra: str = "",
    ) -> DailyPlan:
        date_str = target.isoformat()
        existing = self.storage.get_plan(date_str, persona.id)
        if existing and not force:
            return existing
        settings = self.config.get("schedule_settings", {}) or {}
        reference_days = max(0, int(settings.get("reference_history_days", 3)))
        history_plans = self.storage.get_recent_plans(
            persona.id, target, reference_days
        )
        long_term_settings = self.config.get("long_term_settings", {}) or {}
        long_term_context = ""
        if long_term_settings.get("enable", True):
            long_term_context = self.long_term.format_day_context(
                persona.id,
                target,
                fallback_to_latest=True,
            )
        schedule_requirements = self.storage.active_schedule_requirements(
            persona.id, target
        )
        relationship_context = await self._personal_network_context(persona.id)
        new_character_hint = await self._generate_new_character(persona)
        requirement_sections = []
        if extra.strip():
            requirement_sections.append("本次补充要求：\n" + extra.strip())
        if schedule_requirements:
            requirement_sections.append(
                "预设日程要求：\n"
                + "\n".join(f"- {item.requirement}" for item in schedule_requirements)
            )
        if new_character_hint:
            requirement_sections.append("新人物出现要求：\n" + new_character_hint)
        plan = await self.plan_generator.generate(
            target,
            persona,
            extra="\n\n".join(requirement_sections),
            history_plans=history_plans,
            long_term_context=long_term_context,
            relationship_context=relationship_context,
        )
        plan_key = self.storage.plan_key(date_str, persona.id)
        previous_plan = self.storage.plans.get(plan_key)
        self.storage.plans[plan_key] = plan
        if plan.status == "ok":
            self.storage.consume_schedule_requirements(
                [item.id for item in schedule_requirements], target
            )
        try:
            await self.storage.save_plans()
        except Exception:
            if previous_plan is None:
                self.storage.plans.pop(plan_key, None)
            else:
                self.storage.plans[plan_key] = previous_plan
            for item in schedule_requirements:
                self.storage.schedule_requirements[item.id] = item
            raise
        return plan

    async def _add_schedule_requirement(
        self,
        event: AstrMessageEvent,
        date_range: str,
        requirement: str,
    ) -> str:
        if not event.is_admin():
            return "无权限：仅管理员可以添加日程要求。"
        today = self._now().date()
        requirement = str(requirement).strip()
        try:
            start, end = parse_schedule_date_range(date_range, today)
            if start <= today:
                raise ValueError("只能为今天之后的日期添加日程要求")
            range_days = (end - start).days + 1
            if range_days > 366:
                raise ValueError("日期区间不能超过 366 天")
            if not requirement:
                raise ValueError("日程要求不能为空")
            if len(requirement) > 500:
                raise ValueError("每条日程要求不能超过 500 字")
            persona = await self.personas.resolve(event.unified_msg_origin)
            for offset in range(range_days):
                target = start + timedelta(days=offset)
                existing_plan = self.storage.get_plan(target.isoformat(), persona.id)
                if existing_plan and existing_plan.status == "ok":
                    raise ValueError(
                        f"{target.isoformat()} 已有有效日程，无法添加首次生成要求"
                    )
                if (
                    len(self.storage.active_schedule_requirements(persona.id, target))
                    >= 10
                ):
                    raise ValueError(
                        f"{target.isoformat()} 已有 10 条日程要求，无法继续添加"
                    )
        except ValueError as exc:
            return f"无法添加日程要求：{exc}。"

        item = ScheduleRequirement(
            id=uuid.uuid4().hex[:12],
            persona_id=persona.id,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            requirement=requirement,
            created_at=self._now().isoformat(),
        )
        self.storage.schedule_requirements[item.id] = item
        try:
            await self.storage.save_plans()
        except Exception as exc:
            self.storage.schedule_requirements.pop(item.id, None)
            logger.warning(
                "[Virtual Life] Schedule requirement persistence failed persona=%s: %s",
                persona.id,
                exc,
            )
            return "日程要求保存失败，请检查日志后重试。"
        normalized_range = (
            item.start_date
            if item.start_date == item.end_date
            else f"{item.start_date}..{item.end_date}"
        )
        return f"已为人格 {persona.id} 添加日程要求：{normalized_range}。"

    async def _reschedule_plan_sessions(
        self, persona: PersonaContext, plan: DailyPlan
    ) -> None:
        for umo in self.policy.enabled_sessions():
            resolved = await self.personas.resolve(umo)
            if resolved.id == persona.id:
                await self._schedule_session(umo, resolved, plan)
        await self.storage.save_sessions()

    async def _schedule_session(
        self, umo: str, persona: PersonaContext, plan: DailyPlan
    ) -> None:
        prefix = f"pvd:{self._umo_hash(umo)}:plan:"
        self.runtime.remove_prefix(prefix)
        now = self._now()
        state = self.policy.ensure_state(umo, persona.id, plan, now)
        if plan.status == "ok":
            delivery = self.config.get("delivery_settings", {}) or {}
            jitter_minutes = delivery.get("proactive_window_jitter_minutes", 15)
            for window in plan.proactive_windows:
                if not self._window_matches(umo, window):
                    continue
                run_at = self._at_time(plan.date, window.at) + timedelta(
                    minutes=proactive_window_offset_minutes(
                        plan, window, jitter_minutes
                    ),
                )
                if run_at <= now:
                    continue
                self.runtime.add_date_job(
                    prefix + "window:" + window.id,
                    run_at,
                    self._run_window,
                    umo,
                    persona.id,
                    plan.revision,
                    window.id,
                )
            await self._schedule_sleep_exception(umo, persona, plan, state)
        self._schedule_idle(umo)

    @staticmethod
    def _window_matches(umo: str, window: ProactiveWindow) -> bool:
        kind = "group" if session_kind(umo) == "group" else "private"
        return window.audience in {"both", kind}

    def _at_time(self, date_str: str, hhmm: str) -> datetime:
        hour, minute = map(int, hhmm.split(":"))
        return datetime.combine(
            date.fromisoformat(date_str), time(hour, minute), self.timezone
        )

    async def _schedule_sleep_exception(self, umo, persona, plan, state) -> None:
        if state.sleep_drawn:
            return
        state.sleep_drawn = True
        probability = float(
            (self.config.get("delivery_settings", {}) or {}).get(
                "sleep_exception_probability", 0.08
            )
        )
        state.sleep_selected = deterministic_probability(
            f"sleep::{plan.date}::{umo}"
        ) < max(0.0, min(1.0, probability))
        if not state.sleep_selected:
            return
        sleep_items = [item for item in plan.timeline if item.state == "sleep"]
        if not sleep_items:
            return
        item = max(
            sleep_items,
            key=lambda value: self._duration_minutes(value.start, value.end),
        )
        start = self._at_time(plan.date, item.start)
        end = self._end_time(plan.date, item.end)
        if end <= self._now() or (end - start).total_seconds() < 120:
            return
        earliest = max(start, self._now() + timedelta(seconds=10))
        span = max(1, int((end - earliest).total_seconds() // 60) - 1)
        offset = deterministic_int(f"sleep-time::{plan.date}::{umo}", 0, span)
        run_at = earliest + timedelta(minutes=offset)
        self.runtime.add_date_job(
            f"pvd:{self._umo_hash(umo)}:plan:sleep",
            run_at,
            self._run_sleep,
            umo,
            persona.id,
            plan.revision,
        )

    @staticmethod
    def _duration_minutes(start: str, end: str) -> int:
        def value(raw: str) -> int:
            if raw == "24:00":
                return 1440
            hour, minute = map(int, raw.split(":"))
            return hour * 60 + minute

        return value(end) - value(start)

    def _end_time(self, date_str: str, hhmm: str) -> datetime:
        if hhmm == "24:00":
            return datetime.combine(
                date.fromisoformat(date_str) + timedelta(days=1),
                time.min,
                self.timezone,
            )
        return self._at_time(date_str, hhmm)

    def _schedule_idle(self, umo: str, *, run_at: datetime | None = None) -> None:
        job_id = f"pvd:{self._umo_hash(umo)}:idle"
        self.runtime.remove(job_id)
        if not self.policy.is_enabled(umo):
            return
        if run_at is None:
            settings = self.policy.settings_for(umo)
            minimum = max(1, int(settings.get("idle_min_minutes", 90)))
            maximum = max(minimum, int(settings.get("idle_max_minutes", minimum)))
            run_at = self._now() + timedelta(minutes=random.randint(minimum, maximum))
        self.runtime.add_date_job(job_id, run_at, self._run_idle, umo)

    async def _run_window(
        self, umo: str, persona_id: str, revision: str, window_id: str
    ) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        if persona.id != persona_id or plan.revision != revision:
            await self._schedule_session(umo, persona, plan)
            return
        window = next(
            (value for value in plan.proactive_windows if value.id == window_id), None
        )
        if not window:
            return
        await self._attempt_window(umo, persona, plan, window, delayed=False)

    async def _run_delayed_window(
        self,
        umo: str,
        persona_id: str,
        revision: str,
        window_id: str,
        scheduled_at: str,
        retry_reason: str = "",
    ) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        if persona.id != persona_id or plan.revision != revision:
            return
        window = next(
            (value for value in plan.proactive_windows if value.id == window_id), None
        )
        if not window:
            return
        await self._attempt_window(
            umo,
            persona,
            plan,
            window,
            delayed=True,
            attempt_key=scheduled_at,
            retry_reason=retry_reason,
        )

    async def _attempt_window(
        self,
        umo: str,
        persona: PersonaContext,
        plan: DailyPlan,
        window: ProactiveWindow,
        *,
        delayed: bool,
        attempt_key: str = "",
        retry_reason: str = "",
    ) -> None:
        intent = window.intent
        if delayed:
            intent = self._delayed_window_intent(window, plan, retry_reason)
        sent, reason = await self._attempt_unsolicited(
            umo,
            persona,
            plan,
            intent,
            "window",
            attempt_key=attempt_key or window.id,
        )
        if sent:
            self._window_retry_counts.pop((umo, plan.revision, window.id), None)
            return
        if not self._should_retry_window(umo, reason):
            return
        key = (umo, plan.revision, window.id)
        count = self._window_retry_counts.get(key, 0)
        delivery = self.config.get("delivery_settings", {}) or {}
        retry_max = max(0, int(delivery.get("window_retry_max", 3)))
        if count >= retry_max:
            return
        run_at = self._window_retry_run_at(umo, plan, reason)
        if run_at is None:
            return
        self._schedule_window_retry(umo, persona, plan, window, run_at, reason)
        self._window_retry_counts[key] = count + 1

    def _delayed_window_intent(
        self,
        window: ProactiveWindow,
        plan: DailyPlan,
        retry_reason: str = "",
    ) -> str:
        source = next(
            (item for item in plan.timeline if item.id == window.source_item_id), None
        )
        base = window.intent
        if not source:
            return base
        reason_hint = {
            "conversation is not idle enough": "当时你正在忙或正在聊天，没找到合适的插话时机",
            "cooldown active": "当时你们刚刚才聊过，紧接着再开口会显得太突兀",
            "availability probability rejected": "当时对方的状态不太适合被打扰",
            "sleeping": "当时对方正在休息",
        }.get(retry_reason, "当时时机不太合适")
        return (
            f"{base}。你原本打算在「{source.activity}」时段联系对方，但{reason_hint}，"
            "错过了那个时机；现在自然地向对方提起，别让对方察觉这是程序自动安排的。"
        )

    def _should_retry_window(self, umo: str, reason: str) -> bool:
        if reason in {"sleeping", "availability probability rejected"}:
            return True
        delivery = self.config.get("delivery_settings", {}) or {}
        if reason == "conversation is not idle enough":
            return bool(delivery.get("window_retry_when_not_idle", False))
        if reason == "cooldown active":
            return bool(delivery.get("window_retry_when_cooldown", False))
        return False

    def _window_retry_run_at(
        self, umo: str, plan: DailyPlan, reason: str
    ) -> datetime | None:
        now = self._now()
        if reason in {"sleeping", "availability probability rejected"}:
            next_time = next_available_at(plan, now)
            if not next_time:
                return None
            return next_time + timedelta(minutes=random.randint(3, 15))
        state = self.storage.sessions.get(umo)
        if not state:
            return None
        delivery = self.config.get("delivery_settings", {}) or {}
        if reason == "conversation is not idle enough" and state.last_user_message_at:
            idle_required = max(
                0, int(delivery.get("minimum_idle_for_window_minutes", 20))
            )
            target = parse_datetime(
                state.last_user_message_at, self.timezone
            ) + timedelta(minutes=idle_required)
        elif reason == "cooldown active" and state.last_proactive_at:
            settings = self.policy.settings_for(umo)
            cooldown = max(0, int(settings.get("cooldown_minutes", 0)))
            target = parse_datetime(
                state.last_proactive_at, self.timezone
            ) + timedelta(minutes=cooldown)
        else:
            return None
        run_at = max(target, now) + timedelta(minutes=random.randint(1, 5))
        if run_at.date() > date.fromisoformat(plan.date):
            return None
        return run_at

    def _schedule_window_retry(
        self,
        umo: str,
        persona: PersonaContext,
        plan: DailyPlan,
        window: ProactiveWindow,
        run_at: datetime | None = None,
        retry_reason: str = "",
    ) -> None:
        if run_at is None:
            next_time = next_available_at(plan, self._now())
            if not next_time:
                return
            run_at = next_time + timedelta(minutes=random.randint(3, 15))
        self.runtime.add_date_job(
            f"pvd:{self._umo_hash(umo)}:plan:window-retry:{window.id}",
            run_at,
            self._run_delayed_window,
            umo,
            persona.id,
            plan.revision,
            window.id,
            run_at.isoformat(),
            retry_reason,
        )

    async def _run_sleep(self, umo: str, persona_id: str, revision: str) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        if persona.id != persona_id or plan.revision != revision:
            return
        await self._attempt_unsolicited(
            umo, persona, plan, "睡梦中醒来、起夜或失眠时忽然想起对方", "sleep"
        )

    async def _run_idle(self, umo: str) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        sent, reason = await self._attempt_unsolicited(
            umo, persona, plan, "会话已经沉默了一段时间，想自然地重新联系", "idle"
        )
        if sent:
            self._schedule_idle(umo)
            return
        if reason in {"sleeping", "availability probability rejected"}:
            next_time = next_available_at(plan, self._now())
            if next_time:
                self._schedule_idle(
                    umo, run_at=next_time + timedelta(minutes=random.randint(3, 15))
                )
        elif reason in {"cooldown active", "conversation is not idle enough"}:
            self._schedule_idle(umo, run_at=self._now() + timedelta(minutes=30))

    async def _attempt_unsolicited(
        self,
        umo: str,
        persona: PersonaContext,
        plan: DailyPlan,
        intent: str,
        trigger: str,
        *,
        attempt_key: str = "",
    ) -> tuple[bool, str]:
        lock = self.delivery_locks.setdefault(umo, asyncio.Lock())
        async with lock:
            now = self._now()
            state = self.policy.ensure_state(umo, persona.id, plan, now)
            current_item = timeline_item_at(plan, now)
            decision = self.policy.evaluate(
                umo=umo,
                state=state,
                current_item=current_item,
                now=now,
                trigger=trigger,
                attempt_key=attempt_key,
            )
            if not decision.allowed:
                logger.info("[虚拟人生] 跳过 %s: %s", umo, decision.reason)
                return False, decision.reason
            await self._deliver(umo, persona, plan, intent, state.unanswered_count)
            self.policy.record_delivery(state, now)
            await self.storage.save_sessions()
            return True, "sent"

    async def _deliver(
        self,
        umo: str,
        persona: PersonaContext,
        plan: DailyPlan,
        intent: str,
        unanswered_count: int,
    ) -> None:
        now = self._now()
        current_item = timeline_item_at(plan, now)
        current_state = current_item.activity if current_item else "今日状态暂未明确"
        await self._record_completed_personal_network_events(persona.id, now)
        relationship_context = await self._personal_network_context(persona.id)
        timeline_context = ""
        delivery = self.config.get("delivery_settings", {}) or {}
        if delivery.get("proactive_timeline_context", True):
            timeline_context = self.smart_context_injector.build_proactive_context(
                plan, now, self.long_term
            )
        text = await self.message_generator.generate(
            umo=umo,
            persona=persona,
            current_time=now,
            current_state=current_state,
            intent=intent,
            unanswered_count=unanswered_count,
            relationship_context=relationship_context,
            timeline_context=timeline_context,
        )
        await self._send_text(umo, text)
        await self.message_generator.record_conversation(
            umo=umo,
            persona_id=persona.id,
            intent=intent,
            assistant_text=text,
        )

    async def _send_text(self, umo: str, text: str) -> None:
        result = self.message_segmenter.split(text)
        logger.info(
            "[虚拟人生] 主动消息分段 umo=%s mode=%s chars=%s parts=%s threshold=%s reason=%s",
            umo,
            result.mode,
            result.source_length,
            len(result.segments),
            result.threshold,
            result.skipped_reason or "segmented",
        )
        for index, segment in enumerate(result.segments):
            sent = await self.context.send_message(umo, MessageChain().message(segment))
            if not sent:
                raise RuntimeError(f"platform unavailable for {umo}")
            if index + 1 < len(result.segments):
                await asyncio.sleep(self.message_segmenter.interval_for(segment))

    async def _check_long_term_renewals(self, target: date) -> None:
        settings = self.config.get("long_term_settings", {}) or {}
        if not settings.get("enable", True):
            return
        persona_ids = {
            str(stage.get("persona_id", ""))
            for stage in self.long_term.stages
            if stage.get("persona_id")
        }
        for persona_id in persona_ids:
            latest = self.long_term.latest_stage(persona_id)
            if not latest:
                continue
            end = date.fromisoformat(latest["end_date"])
            if target < end or self.long_term.has_stage_starting_after(persona_id, end):
                continue
            await self._run_long_term_renewal(persona_id)

    async def _run_long_term_renewal(self, persona_id: str) -> None:
        latest = self.long_term.latest_stage(persona_id)
        target_umo = self.long_term.notification_target(persona_id)
        if not latest or not target_umo:
            return
        attempts = self.renewal_attempts.get(persona_id, 0) + 1
        self.renewal_attempts[persona_id] = attempts
        settings = self.config.get("long_term_settings", {}) or {}
        try:
            persona = await self.personas.resolve_id(persona_id, target_umo)
            start = date.fromisoformat(latest["end_date"]) + timedelta(days=1)
            stages = await self.plan_generator.generate_long_term_timeline(
                persona,
                start_date=start,
                previous_stage=latest,
                requirements="自动延续当前人物的大时间表，保持经历连续但允许进入新的学期、假期、项目或生活阶段。",
            )
            await self.long_term.add_auto_renewal(persona_id, stages)
            self.image_renderer.invalidate_persona(persona_id)
            self.renewal_attempts.pop(persona_id, None)
            await self._notify_admin(
                target_umo,
                f"人格 {persona_id} 的大时间表已自动续期："
                + "、".join(
                    f"{stage['name']}（{stage['start_date']} 至 {stage['end_date']}）"
                    for stage in stages
                ),
            )
        except Exception as exc:
            await self._notify_admin(
                target_umo,
                f"人格 {persona_id} 的大时间表自动续期失败（第 {attempts} 次）：{exc}",
            )
            maximum = max(1, int(settings.get("renewal_max_attempts", 6)))
            if attempts >= maximum:
                return
            delay = max(1, int(settings.get("renewal_retry_minutes", 60)))
            self.runtime.add_date_job(
                f"pvd:long-term-renewal:{hashlib.sha1(persona_id.encode()).hexdigest()[:12]}",
                self._now() + timedelta(minutes=delay),
                self._run_long_term_renewal,
                persona_id,
            )

    async def _notify_admin(self, umo: str, text: str) -> None:
        try:
            await self.context.send_message(umo, MessageChain().message(text))
        except Exception as exc:
            logger.error("[虚拟人生] 管理员通知发送失败 %s: %s", umo, exc)

    def _scheduled_proactive_entries(
        self, umo: str, plan: DailyPlan
    ) -> list[tuple[datetime, str]]:
        job_times = dict(self.runtime.scheduled_jobs())
        umo_hash = self._umo_hash(umo)
        plan_prefix = f"pvd:{umo_hash}:plan:"
        windows = {window.id: window for window in plan.proactive_windows}
        entries: list[tuple[datetime, str]] = []

        idle_time = job_times.get(f"pvd:{umo_hash}:idle")
        if idle_time:
            entries.append((idle_time, "沉默主动"))

        for job_id, run_at in job_times.items():
            if not job_id.startswith(plan_prefix):
                continue
            suffix = job_id[len(plan_prefix) :]
            if suffix == "sleep":
                entries.append((run_at, "睡眠异常主动"))
            elif suffix.startswith("window-retry:"):
                window_id = suffix.removeprefix("window-retry:")
                window = windows.get(window_id)
                label = f"延迟窗口 [{window_id}]"
                entries.append(
                    (run_at, label + (f" · {window.intent}" if window else ""))
                )
            elif suffix.startswith("window:"):
                window_id = suffix.removeprefix("window:")
                window = windows.get(window_id)
                label = f"日程窗口 [{window_id}]"
                entries.append(
                    (run_at, label + (f" · {window.intent}" if window else ""))
                )

        return sorted(entries, key=lambda item: (item[0], item[1]))

    async def _refresh_persona_daily_plan(self, persona: PersonaContext) -> DailyPlan:
        plan = await self._ensure_plan(persona, self._now().date(), force=True)
        for umo in self.policy.enabled_sessions():
            resolved = await self.personas.resolve(umo)
            if resolved.id == persona.id:
                await self._schedule_session(umo, resolved, plan)
        return plan

    async def _create_long_term_draft(
        self,
        *,
        persona: PersonaContext,
        admin_umo: str,
        requirements: str,
        start_date: date,
        previous_stage: dict | None,
        source: str,
        mode: str,
    ) -> dict:
        stages = await self.plan_generator.generate_long_term_timeline(
            persona,
            start_date=start_date,
            previous_stage=previous_stage,
            requirements=requirements,
        )
        draft = await self.long_term.set_draft(
            persona.id,
            stages,
            source=source,
            admin_umo=admin_umo,
            created_at=self._now().isoformat(),
            requirements=requirements,
            mode=mode,
        )
        self.image_renderer.invalidate_persona(persona.id)
        return draft

    async def _image_view_results(
        self, event: AstrMessageEvent, title: str, fallback: str, jobs
    ) -> list:
        if not self.image_renderer.enabled:
            return [event.plain_result(fallback)]
        try:
            paths = [await job() for job in jobs]
        except Exception:
            logger.exception("[虚拟人生] 图片渲染失败，回退文字输出")
            return [event.plain_result("图片渲染失败，已切换为文字模式。\n" + fallback)]
        return [
            event.plain_result(title),
            *(event.image_result(path) for path in paths),
        ]

    @staticmethod
    def _parse_long_term_json(content: str, persona_id: str) -> list[dict]:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"结构化数据不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("结构化数据必须是 JSON 对象")
        if "stages" not in payload:
            payload = {"stages": [payload]}
        return validate_stage_bundle(payload, persona_id)

    async def _prepare_reply_delay(
        self,
        event: AstrMessageEvent,
    ) -> None:
        excluded = event.get_extra("provider_request") is not None
        event.set_extra("virtual_life_reply_delay_excluded", excluded)
        if excluded:
            return
        if not self.reply_delay_policy.enabled:
            return
        try:
            received_at = datetime.fromtimestamp(float(event.created_at), self.timezone)
        except (AttributeError, TypeError, ValueError, OSError):
            received_at = self._now()
        try:
            persona = await self.personas.resolve(event.unified_msg_origin)
        except Exception:
            logger.exception("[虚拟人生] 回复延迟跳过：人格解析失败")
            return
        plan = self.storage.get_plan(received_at.date().isoformat(), persona.id)
        if not plan or plan.status != "ok":
            logger.info(
                "[虚拟人生] 回复延迟跳过：内存中没有有效日程 persona=%s date=%s",
                persona.id,
                received_at.date().isoformat(),
            )
            return
        arrival_item = timeline_item_at(plan, received_at)
        if arrival_item is None:
            return

        active = self.reply_delay_coordinator.is_active(
            event.unified_msg_origin,
            received_at,
        )
        decision = self.reply_delay_policy.decide(
            arrival_item,
            received_at,
            len(event.get_message_str()),
            active_conversation=active,
        )
        queued = QueuedMessage(
            received_at=received_at,
            sender_id=event.get_sender_id(),
            sender_name=event.get_sender_name(),
            prompt=str(event.get_message_str() or ""),
            image_urls=self._message_media_urls(event, Image),
            audio_urls=self._message_media_urls(event, Record),
            temporary_files=tuple(
                str(path)
                for path in getattr(event, "_temporary_local_files", ())
                if path
            ),
        )
        batch, primary = await self.reply_delay_coordinator.enqueue(
            event.unified_msg_origin,
            queued,
            decision,
            arrival_item,
        )
        if batch is None:
            return
        if not primary:
            self._detach_temporary_files(event, queued.temporary_files)
            event.stop_event()
            logger.info(
                "[虚拟人生] 消息已加入回复延迟批次 umo=%s queued=%s deadline=%s",
                event.unified_msg_origin,
                len(batch.messages),
                batch.deadline.isoformat(),
            )
            return

        if decision.delay_seconds > 0 and self.reply_delay_policy.notify_user:
            notification = self.reply_delay_policy.notification(decision)
            if notification:
                try:
                    await self.context.send_message(
                        event.unified_msg_origin,
                        MessageChain().message(notification),
                    )
                except Exception:
                    logger.exception("[虚拟人生] 回复延迟提示发送失败")

        messages = await self.reply_delay_coordinator.settle(batch, self._now())
        settled_at = self._now()
        event.set_extra("virtual_life_reply_delay_batch", batch)
        event.set_extra("virtual_life_reply_delay_messages", messages)
        event.set_extra("virtual_life_reply_delay_persona_id", persona.id)
        event.set_extra("virtual_life_reply_delay_settled_at", settled_at)
        logger.info(
            "[虚拟人生] 回复延迟批次开始结算 umo=%s availability=%s planned=%ss actual=%ss queued=%s",
            event.unified_msg_origin,
            decision.availability,
            decision.delay_seconds,
            max(0, int((settled_at - batch.created_at).total_seconds())),
            len(messages),
        )

    @staticmethod
    def _message_media_urls(
        event: AstrMessageEvent, media_type: type
    ) -> tuple[str, ...]:
        components = []
        for component in event.get_messages():
            components.append(component)
            if isinstance(component, Reply) and component.chain:
                components.extend(component.chain)
        urls = []
        for component in components:
            if not isinstance(component, media_type):
                continue
            value = component.path or component.url or component.file
            if value and str(value) not in urls:
                urls.append(str(value))
        return tuple(urls)

    @staticmethod
    def _detach_temporary_files(
        event: AstrMessageEvent,
        temporary_files: tuple[str, ...],
    ) -> None:
        tracked = getattr(event, "_temporary_local_files", None)
        if not isinstance(tracked, list):
            return
        for path in temporary_files:
            while path in tracked:
                tracked.remove(path)

    def _apply_reply_delay_batch(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        batch = event.get_extra("virtual_life_reply_delay_batch")
        messages = event.get_extra("virtual_life_reply_delay_messages")
        settled_at = event.get_extra("virtual_life_reply_delay_settled_at")
        persona_id = event.get_extra("virtual_life_reply_delay_persona_id")
        if not batch or not messages or not isinstance(settled_at, datetime):
            return
        req.prompt = format_queued_messages(
            messages,
            group=session_kind(event.unified_msg_origin) == "group",
        )
        req.image_urls = [url for message in messages for url in message.image_urls]
        req.audio_urls = [url for message in messages for url in message.audio_urls]
        for message in messages:
            for path in message.temporary_files:
                event.track_temporary_local_file(path)
        current_plan = self.storage.get_plan(
            settled_at.date().isoformat(),
            str(persona_id),
        )
        current_item = (
            timeline_item_at(current_plan, settled_at)
            if current_plan and current_plan.status == "ok"
            else None
        )
        delay_context = format_delay_context(batch, settled_at, current_item)
        req.extra_user_content_parts.append(TextPart(text=delay_context).mark_as_temp())

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=999)
    async def on_friend_message(self, event: AstrMessageEvent) -> None:
        await self._handle_incoming(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=998)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        await self._handle_incoming(event)

    def _is_conversation_event(self, event: AstrMessageEvent) -> bool:
        if event.get_extra("handlers_parsed_params", None):
            return False
        handlers = event.get_extra("activated_handlers", None) or []
        for handler in handlers:
            for event_filter in getattr(handler, "event_filters", None) or []:
                if isinstance(
                    event_filter, (CommandFilter, CommandGroupFilter, RegexFilter)
                ):
                    return False
        return True

    async def _handle_incoming(self, event: AstrMessageEvent) -> None:
        umo = event.unified_msg_origin
        if not self.policy.is_enabled(umo):
            return
        if not self._is_conversation_event(event):
            logger.debug(
                "[虚拟人生] 命令消息不计入空闲计时 umo=%s", umo
            )
            return
        self.policy.record_incoming(umo, self._now())
        await self.storage.save_sessions()
        self._schedule_idle(umo)

    @filter.on_waiting_llm_request()
    async def queue_reply_delay(self, event: AstrMessageEvent) -> None:
        await self._prepare_reply_delay(event)

    @filter.on_llm_request()
    async def inject_virtual_state(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        self._apply_reply_delay_batch(event, req)
        if self.reply_delay_policy.enabled and not event.get_extra(
            "virtual_life_reply_delay_excluded", False
        ):
            event.set_extra("virtual_life_ordinary_llm_request", True)
        if not self.smart_context_injector.enabled:
            return
        try:
            persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        except RuntimeError:
            return
        if plan.status != "ok":
            return
        now = self._now()
        current_item = next(
            (
                item
                for item in plan.timeline
                if (item.start <= now.strftime("%H:%M") < item.end or item.end == "24:00")
                and item.participant_ids
            ),
            None,
        )
        participant_context = ""
        if current_item and current_item.participant_ids:
            participant_context = await self._personal_network_participants_context(
                persona.id, current_item.participant_ids
            )
        injection, modules, limit = self.smart_context_injector.build_details(
            plan,
            now,
            self.long_term,
            event.get_message_str(),
            participant_context=participant_context,
        )
        if injection:
            req.extra_user_content_parts.append(TextPart(text=injection).mark_as_temp())
            logger.info(
                "[虚拟人生] 智能状态注入 persona=%s modules=%s chars=%s limit=%s\n%s",
                plan.persona_id,
                ",".join(modules),
                len(injection),
                limit,
                injection,
            )

    @filter.on_llm_response()
    async def track_delayed_llm_response(
        self,
        event: AstrMessageEvent,
        response: LLMResponse | None,
    ) -> None:
        if not event.get_extra("virtual_life_ordinary_llm_request", False):
            return
        if response is not None and str(response.completion_text or "").strip():
            event.set_extra("virtual_life_ordinary_llm_succeeded", True)

    @filter.after_message_sent()
    async def mark_active_conversation(self, event: AstrMessageEvent) -> None:
        if not event.get_extra("virtual_life_ordinary_llm_succeeded", False):
            return
        self.reply_delay_coordinator.mark_replied(
            event.unified_msg_origin,
            self._now(),
            self.reply_delay_policy.active_conversation_seconds,
        )

    @filter.llm_tool(name="get_virtual_daily_schedule")
    async def get_virtual_daily_schedule(self, event: AstrMessageEvent) -> str:
        """查询机器人当前人格的完整虚拟日程、穿搭与当前活动。"""
        _, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        return (
            "今日暂无可用日程。"
            if plan.status != "ok"
            else format_plan(plan, self._now())
        )

    @filter.llm_tool(name="get_long_term_timeline")
    async def get_long_term_timeline(self, event: AstrMessageEvent) -> str:
        """查询机器人当前人格全部已批准的大时间表阶段。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        stages = self.long_term.list_for_persona(persona.id)
        if not stages:
            return "当前人格没有已批准的大时间表。"
        enriched = [self.long_term.with_holidays(stage) for stage in stages]
        return json.dumps(
            {"persona_id": persona.id, "stages": enriched}, ensure_ascii=False, indent=2
        )

    @filter.llm_tool(name="add_schedule_requirement")
    async def add_schedule_requirement_tool(
        self,
        event: AstrMessageEvent,
        start_date: str,
        end_date: str,
        requirement: str,
    ) -> str:
        """仅当当前管理员明确要求时，为当前人格未来日期的首次日程生成添加要求。

        Args:
            start_date(string): 开始日期，支持 YYYY-MM-DD、MM-DD 或 DD。
            end_date(string): 结束日期，支持 YYYY-MM-DD、MM-DD 或 DD；省略部分相对开始日期向后推导。
            requirement(string): 要应用于区间内每个日期首次日程生成的要求，最多 500 字。
        """
        return await self._add_schedule_requirement(
            event,
            f"{str(start_date).strip()}..{str(end_date).strip()}",
            requirement,
        )

    @filter.command_group("虚拟人生")
    def virtual_life_group(self):
        """虚拟人生命令组；不提供子命令时由 AstrBot 输出帮助。"""
        pass

    @virtual_life_group.command("订阅会话")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def subscribe_session(self, event: AstrMessageEvent):
        """订阅当前会话的主动消息。"""
        umo = event.unified_msg_origin
        added = self.policy.subscribe(umo)
        self.config.save_config()
        kind = "群聊" if session_kind(umo) == "group" else "私聊"
        action = "已订阅" if added else "已刷新订阅"
        logger.info("[虚拟人生] %s主动消息会话 umo=%s", action, umo)
        try:
            persona, plan = await self._ensure_plan_for_umo(umo)
            await self._schedule_session(umo, persona, plan)
            await self.storage.save_sessions()
        except Exception as exc:
            logger.warning("[虚拟人生] 订阅后即时调度失败 umo=%s: %s", umo, exc)
            yield event.plain_result(
                f"{action}当前{kind}会话，但今日日程调度失败，请检查日志或稍后重试。"
            )
            return
        yield event.plain_result(
            f"{action}当前{kind}会话，主动消息将在符合日程与发送规则时触发。"
        )

    @filter.command("回复延迟")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reply_delay_command(self, event: AstrMessageEvent, action: str):
        action = str(action).strip()
        if action == "结算":
            batch = await self.reply_delay_coordinator.settle_now(
                event.unified_msg_origin
            )
            if batch is None:
                yield event.plain_result("当前会话没有等待结算的回复批次。")
                return
            logger.info(
                "[虚拟人生] 管理员立即结算回复延迟批次 会话=%s 消息数=%s",
                event.unified_msg_origin,
                len(batch.messages),
            )
            yield event.plain_result(
                f"已立即结算当前会话的回复批次，共 {len(batch.messages)} 条消息。"
            )
            return
        if action not in {"开启", "关闭"}:
            yield event.plain_result("用法：/回复延迟 开启|关闭|结算")
            return

        enabled = action == "开启"
        settings = self.config.get("reply_delay_settings", {}) or {}
        changed = bool(settings.get("enable")) != enabled
        settings["enable"] = enabled
        self.config["reply_delay_settings"] = settings
        self.reply_delay_policy.settings = settings
        if changed:
            self.config.save_config()

        settled_count = 0
        if not enabled:
            batches = await self.reply_delay_coordinator.settle_all_now()
            settled_count = len(batches)
        logger.info(
            "[虚拟人生] 管理员%s回复延迟 配置变更=%s 结算批次=%s",
            action,
            changed,
            settled_count,
        )
        state = "已开启" if enabled else "已关闭"
        suffix = f"，并立即结算 {settled_count} 个等待批次" if settled_count else ""
        yield event.plain_result(f"回复延迟{state}{suffix}。")

    @filter.command_group("虚拟日程")
    def virtual_daily_group(self):
        """虚拟日程命令组；不提供子命令时由 AstrBot 输出帮助。"""
        pass

    @virtual_daily_group.command("要求")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def add_schedule_requirement_command(
        self,
        event: AstrMessageEvent,
        date_range: str,
        requirement=GreedyStr,
    ):
        """为当前人格未来日期的首次日程生成添加要求。"""
        yield event.plain_result(
            await self._add_schedule_requirement(event, date_range, requirement)
        )

    @virtual_daily_group.command("查看")
    async def show_schedule(self, event: AstrMessageEvent):
        """查看今日虚拟日程时间轴图片。"""
        persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        if plan.status != "ok":
            yield event.plain_result("今日暂无可用日程。")
            return
        now = self._now()
        target = date.fromisoformat(plan.date)
        long_term_day = self.long_term.expand_day(persona.id, target) or {
            "stage": None,
            "active_periods": [],
            "holidays": self.long_term.holidays.on(target),
        }
        fallback = format_timeline(plan, now, long_term_day)
        results = await self._image_view_results(
            event,
            f"{persona.id} · {plan.date} 虚拟日程",
            fallback,
            [lambda: self.image_renderer.render_timeline(plan, now, long_term_day)],
        )
        for result in results:
            yield result

    @virtual_daily_group.command("穿搭")
    async def show_outfit(self, event: AstrMessageEvent):
        """查看今日穿搭图片。"""
        persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        if plan.status != "ok":
            yield event.plain_result("今日暂无可用穿搭。")
            return
        now = self._now()
        results = await self._image_view_results(
            event,
            f"{persona.id} · {plan.date} 今日穿搭",
            format_outfit(plan),
            [lambda: self.image_renderer.render_outfit(plan, now)],
        )
        for result in results:
            yield result

    @virtual_daily_group.command("重写")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def rewrite_schedule(self, event: AstrMessageEvent, extra: str | None = None):
        """重新生成今日完整虚拟日程。"""
        persona, plan = await self._ensure_plan_for_umo(
            event.unified_msg_origin, force=True, extra=extra or ""
        )
        await self._reschedule_plan_sessions(persona, plan)
        yield event.plain_result(
            "重写失败，请检查 LLM 输出。"
            if plan.status != "ok"
            else format_plan(plan, self._now())
        )

    @virtual_daily_group.command("重写日程")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def rewrite_daily_timeline(
        self, event: AstrMessageEvent, extra: str | None = None
    ):
        """保留主题、心情与穿搭，只重写今日时间日程。"""
        persona, current = await self._ensure_plan_for_umo(event.unified_msg_origin)
        if current.status != "ok":
            yield event.plain_result(
                "今日没有可局部重写的有效日程，请先使用“虚拟日程 重写”。"
            )
            return
        long_term_context = ""
        long_term_settings = self.config.get("long_term_settings", {}) or {}
        if long_term_settings.get("enable", True):
            long_term_context = self.long_term.format_day_context(
                persona.id,
                date.fromisoformat(current.date),
                fallback_to_latest=True,
            )
        try:
            relationship_context = await self._personal_network_context(persona.id)
            plan = await self.plan_generator.rewrite_schedule(
                current,
                persona,
                extra=extra or "",
                long_term_context=long_term_context,
                relationship_context=relationship_context,
            )
        except Exception as exc:
            logger.warning(
                "[Virtual Life] Schedule rewrite failed persona=%s: %s", persona.id, exc
            )
            yield event.plain_result(
                "日程重写失败，已保留原日程，请检查 LLM 输出或日志。"
            )
            return
        try:
            self.storage.plans[self.storage.plan_key(plan.date, persona.id)] = plan
            await self.storage.save_plans()
            await self._reschedule_plan_sessions(persona, plan)
        except Exception as exc:
            logger.warning(
                "[Virtual Life] Schedule rewrite persistence failed persona=%s: %s",
                persona.id,
                exc,
            )
            yield event.plain_result("日程已生成，但保存或调度失败，请检查日志。")
            return
        yield event.plain_result(format_plan(plan, self._now()))

    @virtual_daily_group.command("重写穿搭")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def rewrite_daily_outfit(
        self, event: AstrMessageEvent, extra: str | None = None
    ):
        """保留主题、心情与时间日程，只重写今日穿搭。"""
        persona, current = await self._ensure_plan_for_umo(event.unified_msg_origin)
        if current.status != "ok":
            yield event.plain_result(
                "今日没有可局部重写的有效穿搭，请先使用“虚拟日程 重写”。"
            )
            return
        try:
            plan = await self.plan_generator.rewrite_outfit(
                current, persona, extra=extra or ""
            )
        except Exception as exc:
            logger.warning(
                "[Virtual Life] Outfit rewrite failed persona=%s: %s", persona.id, exc
            )
            yield event.plain_result(
                "穿搭重写失败，已保留原穿搭，请检查 LLM 输出或日志。"
            )
            return
        try:
            self.storage.plans[self.storage.plan_key(plan.date, persona.id)] = plan
            await self.storage.save_plans()
            await self._reschedule_plan_sessions(persona, plan)
        except Exception as exc:
            logger.warning(
                "[Virtual Life] Outfit rewrite persistence failed persona=%s: %s",
                persona.id,
                exc,
            )
            yield event.plain_result("穿搭已生成，但保存或调度失败，请检查日志。")
            return
        yield event.plain_result(format_outfit(plan))

    @filter.command_group("大时间表")
    def long_term_group(self):
        """大时间表命令组；不提供子命令时由 AstrBot 输出帮助。"""
        pass

    @long_term_group.command("生成")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_generate(
        self, event: AstrMessageEvent, requirements: str | None = None
    ):
        """根据自然语言要求生成追加草稿。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        latest = self.long_term.latest_stage(persona.id)
        start = (
            date.fromisoformat(latest["end_date"]) + timedelta(days=1)
            if latest
            else self._now().date()
        )
        try:
            await self._create_long_term_draft(
                persona=persona,
                admin_umo=event.unified_msg_origin,
                requirements=requirements or "",
                start_date=start,
                previous_stage=latest,
                source="natural",
                mode="append",
            )
        except Exception as exc:
            yield event.plain_result(f"生成草稿失败：{exc}")
            return
        yield event.plain_result(
            "已生成大时间表草稿，使用 /大时间表 草稿 查看，确认后执行 /大时间表 批准。"
        )

    @long_term_group.command("导入")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_import(self, event: AstrMessageEvent, content: str):
        """导入结构化 JSON 为替换全部阶段的草稿。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        try:
            stages = self._parse_long_term_json(content, persona.id)
            await self.long_term.set_draft(
                persona.id,
                stages,
                source="json",
                admin_umo=event.unified_msg_origin,
                created_at=self._now().isoformat(),
                requirements="结构化数据导入",
                mode="replace_all",
            )
            self.image_renderer.invalidate_persona(persona.id)
        except Exception as exc:
            yield event.plain_result(f"导入草稿失败：{exc}")
            return
        yield event.plain_result("结构化数据已保存为草稿，确认后执行 /大时间表 批准。")

    @long_term_group.command("草稿")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_draft(self, event: AstrMessageEvent):
        """查看当前人格待批准草稿。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        draft = self.long_term.get_draft(persona.id)
        if not draft:
            yield event.plain_result("当前人格没有待批准草稿。")
            return
        stages = [self.long_term.with_holidays(stage) for stage in draft["stages"]]
        fallback = json.dumps({**draft, "stages": stages}, ensure_ascii=False, indent=2)
        jobs = [
            lambda stage=stage: self.image_renderer.render_stage(
                stage,
                persona.id,
                status="draft",
                draft_metadata=draft,
            )
            for stage in stages
        ]
        results = await self._image_view_results(
            event,
            f"{persona.id} · 大时间表草稿 · 待批准 · 共 {len(jobs)} 个阶段",
            fallback,
            jobs,
        )
        for result in results:
            yield result

    @long_term_group.command("批准")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_approve(self, event: AstrMessageEvent):
        """批准草稿并重新生成今日日程。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        try:
            approved = await self.long_term.approve_draft(
                persona.id, event.unified_msg_origin
            )
            self.image_renderer.invalidate_persona(persona.id)
            await self._refresh_persona_daily_plan(persona)
        except Exception as exc:
            yield event.plain_result(f"批准失败：{exc}")
            return
        yield event.plain_result(
            "大时间表已生效并重生成今日日程："
            + "、".join(stage["name"] for stage in approved)
        )

    @long_term_group.command("拒绝")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_reject(
        self, event: AstrMessageEvent, feedback: str | None = None
    ):
        """拒绝草稿；提供修改意见时重新生成。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        draft = self.long_term.get_draft(persona.id)
        if not draft:
            yield event.plain_result("当前人格没有待拒绝草稿。")
            return
        await self.long_term.reject_draft(persona.id)
        self.image_renderer.invalidate_persona(persona.id)
        if not feedback:
            yield event.plain_result("草稿已拒绝并删除。")
            return
        start = date.fromisoformat(draft["stages"][0]["start_date"])
        previous = (
            self.long_term.latest_stage(persona.id)
            if draft.get("mode") == "append"
            else None
        )
        requirements = "；".join(
            part
            for part in (draft.get("requirements", ""), f"修改意见：{feedback}")
            if part
        )
        try:
            await self._create_long_term_draft(
                persona=persona,
                admin_umo=event.unified_msg_origin,
                requirements=requirements,
                start_date=start,
                previous_stage=previous,
                source="revision",
                mode=str(draft.get("mode", "append")),
            )
        except Exception as exc:
            yield event.plain_result(f"草稿已拒绝，但重新生成失败：{exc}")
            return
        yield event.plain_result("已按修改意见重新生成草稿，使用 /大时间表 草稿 查看。")

    @long_term_group.command("列表")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_list(self, event: AstrMessageEvent):
        """查看当前人格全部已批准阶段。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        stages = self.long_term.list_for_persona(persona.id)
        if not stages:
            yield event.plain_result("当前人格没有已批准的大时间表。")
            return
        fallback = "\n".join(
            f"{stage['id']} | {stage['name']} | {stage['kind']} | {stage['start_date']} 至 {stage['end_date']} | 优先级 {stage['priority']}"
            for stage in stages
        )
        results = await self._image_view_results(
            event,
            f"{persona.id} · 已批准阶段，共 {len(stages)} 个",
            fallback,
            [lambda: self.image_renderer.render_stage_list(stages, persona.id)],
        )
        for result in results:
            yield result

    @long_term_group.command("查看")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_view(
        self, event: AstrMessageEvent, stage_id: str | None = None
    ):
        """查看阶段；可省略参数或输入阶段 ID、名称及唯一片段。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        stage, candidates = self.long_term.resolve_stage(
            persona.id, self._now().date(), stage_id or ""
        )
        if not stage:
            if candidates:
                lines = ["找到多个匹配阶段，请使用完整 ID 或名称："]
                lines.extend(
                    f"- {item['id']} | {item['name']} | {item['start_date']} 至 {item['end_date']}"
                    for item in candidates
                )
                yield event.plain_result("\n".join(lines))
            elif stage_id:
                yield event.plain_result(
                    "未找到匹配阶段，请先使用 /大时间表 列表 查看可用 ID。"
                )
            else:
                yield event.plain_result("当前人格没有已批准的大时间表。")
            return
        stage = self.long_term.with_holidays(stage)
        fallback = json.dumps(stage, ensure_ascii=False, indent=2)
        results = await self._image_view_results(
            event,
            f"{persona.id} · {stage['name']}",
            fallback,
            [lambda: self.image_renderer.render_stage(stage, persona.id)],
        )
        for result in results:
            yield result

    @long_term_group.command("重生成")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_regenerate(
        self, event: AstrMessageEvent, requirements: str | None = None
    ):
        """重新生成替换全部阶段的草稿。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        previous = self.long_term.latest_stage(persona.id)
        try:
            await self._create_long_term_draft(
                persona=persona,
                admin_umo=event.unified_msg_origin,
                requirements=requirements or "重新规划完整大时间表",
                start_date=self._now().date(),
                previous_stage=previous,
                source="regenerate",
                mode="replace_all",
            )
        except Exception as exc:
            yield event.plain_result(f"重生成草稿失败：{exc}")
            return
        yield event.plain_result(
            "已生成替换全部阶段的草稿，使用 /大时间表 草稿 查看，批准后才会生效。"
        )

    @filter.command_group("主动消息")
    def proactive_group(self):
        """主动消息命令组；不提供子命令时由 AstrBot 输出帮助。"""
        pass

    @proactive_group.command("状态")
    async def proactive_status(self, event: AstrMessageEvent):
        persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        state = self.policy.ensure_state(
            event.unified_msg_origin, persona.id, plan, self._now()
        )
        yield event.plain_result(
            f"人格：{persona.id}\n今日预算：{state.sent_count}/{state.daily_budget}\n"
            f"连续未回复：{state.unanswered_count}"
        )

    @proactive_group.command("立即")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def trigger_now(self, event: AstrMessageEvent):
        persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        await self._deliver(
            event.unified_msg_origin, persona, plan, "管理员要求立即测试主动消息", 0
        )
        yield event.plain_result("测试主动消息已发送。")

    @proactive_group.command("执行时间")
    async def proactive_execution_times(self, event: AstrMessageEvent):
        _, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        entries = self._scheduled_proactive_entries(event.unified_msg_origin, plan)
        if not entries:
            yield event.plain_result("当前会话没有已安排的主动消息。")
            return
        lines = ["主动消息具体执行时间："]
        lines.extend(
            f"- {run_at.astimezone(self.timezone).strftime('%Y-%m-%d %H:%M:%S %z')}｜{label}"
            for run_at, label in entries
        )
        yield event.plain_result("\n".join(lines))
