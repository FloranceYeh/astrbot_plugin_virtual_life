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
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.provider.entities import ProviderRequest

from .core.context_injection import SmartContextInjector
from .core.generator import DailyPlanGenerator
from .core.image_renderer import ScheduleImageRenderer
from .core.long_term import LongTermTimelineStore, validate_stage_bundle
from .core.message_generator import ProactiveMessageGenerator
from .core.message_segmenter import ProactiveMessageSegmenter
from .core.models import DailyPlan, FollowupTask, ProactiveWindow
from .core.persona import PersonaContext, PersonaResolver
from .core.proactive import ProactivePolicy, session_kind
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
        self.renewal_attempts: dict[str, int] = {}

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
        await self._restore_followups()
        logger.info("[虚拟人生] 插件已启动，时区=%s", self.timezone.key)

    async def terminate(self) -> None:
        self.runtime.stop()
        await self.storage.save_plans()
        await self.storage.save_sessions()
        await self.storage.save_followups()

    @staticmethod
    def _umo_hash(umo: str) -> str:
        return hashlib.sha1(umo.encode("utf-8")).hexdigest()[:12]

    def _now(self) -> datetime:
        return now_in(self.timezone)

    async def _daily_refresh(self) -> None:
        await self._refresh_all(force=True)

    async def _refresh_all(self, *, force: bool) -> None:
        async with self.refresh_lock:
            now = self._now()
            await self._check_long_term_renewals(now.date())
            grouped: dict[str, tuple[PersonaContext, list[str]]] = {}
            for umo in self.policy.enabled_sessions():
                persona = await self.personas.resolve(umo)
                grouped.setdefault(persona.id, (persona, []))[1].append(umo)

            for persona, sessions in grouped.values():
                plan = await self._ensure_plan(persona, now.date(), force=force)
                for umo in sessions:
                    await self._schedule_session(umo, persona, plan)

            keep_days = int((self.config.get("schedule_settings", {}) or {}).get("history_days", 14))
            self.storage.plans = prune_date_keys(self.storage.plans, keep_days, now.date())
            await self.storage.save_plans()
            await self.storage.save_sessions()

    async def _ensure_plan_for_umo(self, umo: str, *, force: bool = False, extra: str = "") -> tuple[PersonaContext, DailyPlan]:
        persona = await self.personas.resolve(umo)
        plan = await self._ensure_plan(persona, self._now().date(), force=force, extra=extra)
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
        history_plans = self.storage.get_recent_plans(persona.id, target, reference_days)
        long_term_settings = self.config.get("long_term_settings", {}) or {}
        long_term_context = ""
        if long_term_settings.get("enable", True):
            long_term_context = self.long_term.format_day_context(
                persona.id,
                target,
                fallback_to_latest=True,
            )
        plan = await self.plan_generator.generate(
            target,
            persona,
            extra=extra,
            history_plans=history_plans,
            long_term_context=long_term_context,
        )
        self.storage.plans[self.storage.plan_key(date_str, persona.id)] = plan
        await self.storage.save_plans()
        return plan

    async def _schedule_session(self, umo: str, persona: PersonaContext, plan: DailyPlan) -> None:
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
                    minutes=proactive_window_offset_minutes(plan, window, jitter_minutes),
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
        return datetime.combine(date.fromisoformat(date_str), time(hour, minute), self.timezone)

    async def _schedule_sleep_exception(self, umo, persona, plan, state) -> None:
        if state.sleep_drawn:
            return
        state.sleep_drawn = True
        probability = float((self.config.get("delivery_settings", {}) or {}).get("sleep_exception_probability", 0.08))
        state.sleep_selected = deterministic_probability(f"sleep::{plan.date}::{umo}") < max(0.0, min(1.0, probability))
        if not state.sleep_selected:
            return
        sleep_items = [item for item in plan.timeline if item.state == "sleep"]
        if not sleep_items:
            return
        item = max(sleep_items, key=lambda value: self._duration_minutes(value.start, value.end))
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
            return datetime.combine(date.fromisoformat(date_str) + timedelta(days=1), time.min, self.timezone)
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

    async def _run_window(self, umo: str, persona_id: str, revision: str, window_id: str) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        if persona.id != persona_id or plan.revision != revision:
            await self._schedule_session(umo, persona, plan)
            return
        window = next((value for value in plan.proactive_windows if value.id == window_id), None)
        if not window:
            return
        await self._attempt_window(umo, persona, plan, window, delayed=False)

    async def _run_delayed_window(
        self, umo: str, persona_id: str, revision: str, window_id: str, scheduled_at: str
    ) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        if persona.id != persona_id or plan.revision != revision:
            return
        window = next((value for value in plan.proactive_windows if value.id == window_id), None)
        if not window:
            return
        await self._attempt_window(umo, persona, plan, window, delayed=True, attempt_key=scheduled_at)

    async def _attempt_window(
        self,
        umo: str,
        persona: PersonaContext,
        plan: DailyPlan,
        window: ProactiveWindow,
        *,
        delayed: bool,
        attempt_key: str = "",
    ) -> None:
        intent = window.intent
        if delayed:
            source = next(item for item in plan.timeline if item.id == window.source_item_id)
            intent = f"延迟的主动消息：原定日程「{source.activity}」已结束。{intent}"
        sent, reason = await self._attempt_unsolicited(
            umo,
            persona,
            plan,
            intent,
            "window",
            attempt_key=attempt_key or window.id,
        )
        if not sent and reason in {"sleeping", "availability probability rejected"}:
            self._schedule_window_retry(umo, persona, plan, window)

    def _schedule_window_retry(
        self, umo: str, persona: PersonaContext, plan: DailyPlan, window: ProactiveWindow
    ) -> None:
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
        )

    async def _run_sleep(self, umo: str, persona_id: str, revision: str) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        if persona.id != persona_id or plan.revision != revision:
            return
        await self._attempt_unsolicited(umo, persona, plan, "睡梦中醒来、起夜或失眠时忽然想起对方", "sleep")

    async def _run_idle(self, umo: str) -> None:
        persona, plan = await self._ensure_plan_for_umo(umo)
        sent, reason = await self._attempt_unsolicited(umo, persona, plan, "会话已经沉默了一段时间，想自然地重新联系", "idle")
        if sent:
            self._schedule_idle(umo)
            return
        if reason in {"sleeping", "availability probability rejected"}:
            next_time = next_available_at(plan, self._now())
            if next_time:
                self._schedule_idle(umo, run_at=next_time + timedelta(minutes=random.randint(3, 15)))
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
        text = await self.message_generator.generate(
            umo=umo,
            persona=persona,
            current_time=now,
            current_state=current_state,
            intent=intent,
            unanswered_count=unanswered_count,
        )
        await self._send_text(umo, text)

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

    async def _restore_followups(self) -> None:
        now = self._now()
        grace = max(1, int((self.config.get("followup_settings", {}) or {}).get("misfire_grace_minutes", 30)))
        changed = False
        for task in self.storage.followups.values():
            if task.status != "pending":
                continue
            scheduled = parse_datetime(task.scheduled_at, self.timezone)
            if scheduled < now - timedelta(minutes=grace):
                task.status = "missed"
                changed = True
                continue
            self._schedule_followup_job(task, max(scheduled, now + timedelta(seconds=2)))
        if changed:
            await self.storage.save_followups()

    async def _check_long_term_renewals(self, target: date) -> None:
        settings = self.config.get("long_term_settings", {}) or {}
        if not settings.get("enable", True):
            return
        persona_ids = {str(stage.get("persona_id", "")) for stage in self.long_term.stages if stage.get("persona_id")}
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
                + "、".join(f"{stage['name']}（{stage['start_date']} 至 {stage['end_date']}）" for stage in stages),
            )
        except Exception as exc:
            await self._notify_admin(target_umo, f"人格 {persona_id} 的大时间表自动续期失败（第 {attempts} 次）：{exc}")
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

    def _schedule_followup_job(self, task: FollowupTask, run_at: datetime) -> None:
        grace = max(1, int((self.config.get("followup_settings", {}) or {}).get("misfire_grace_minutes", 30)))
        self.runtime.add_date_job(
            f"pvd:followup:{task.id}",
            run_at,
            self._run_followup,
            task.id,
            misfire_grace_time=grace * 60,
        )

    async def _create_followup(self, umo: str, scheduled_at: str, intent: str) -> FollowupTask:
        settings = self.config.get("followup_settings", {}) or {}
        if not settings.get("enable", True):
            raise ValueError("回访功能未启用")
        run_at = parse_datetime(scheduled_at, self.timezone)
        if run_at <= self._now():
            raise ValueError("回访时间必须晚于当前时间")
        pending = [task for task in self.storage.followups.values() if task.umo == umo and task.status == "pending"]
        if len(pending) >= max(1, int(settings.get("max_pending_per_session", 10))):
            raise ValueError("当前会话待执行回访数量已达上限")
        persona = await self.personas.resolve(umo)
        task = FollowupTask(
            id=uuid.uuid4().hex[:12],
            umo=umo,
            persona_id=persona.id,
            scheduled_at=run_at.isoformat(),
            intent=intent.strip(),
            created_at=self._now().isoformat(),
        )
        if not task.intent:
            raise ValueError("回访意图不能为空")
        self.storage.followups[task.id] = task
        await self.storage.save_followups()
        self._schedule_followup_job(task, run_at)
        return task

    async def _run_followup(self, task_id: str) -> None:
        task = self.storage.followups.get(task_id)
        if not task or task.status != "pending":
            return
        for attempt in range(3):
            try:
                persona, plan = await self._ensure_plan_for_umo(task.umo)
                await self._deliver(task.umo, persona, plan, "用户明确委托的回访：" + task.intent, 0)
                task.status = "completed"
                break
            except Exception as exc:
                task.last_error = str(exc)
                if attempt < 2:
                    await asyncio.sleep(2 + attempt * 3)
                    continue
                task.status = "failed"
                logger.error("[虚拟人生] 回访 %s 发送失败: %s", task.id, exc)
        await self.storage.save_followups()

    async def _cancel_followup(self, umo: str, task_id: str) -> bool:
        task = self.storage.followups.get(task_id)
        if not task or task.umo != umo or task.status != "pending":
            return False
        task.status = "cancelled"
        self.runtime.remove(f"pvd:followup:{task.id}")
        await self.storage.save_followups()
        return True

    def _pending_followups(self, umo: str) -> list[FollowupTask]:
        return sorted(
            (task for task in self.storage.followups.values() if task.umo == umo and task.status == "pending"),
            key=lambda task: task.scheduled_at,
        )

    def _scheduled_proactive_entries(self, umo: str, plan: DailyPlan) -> list[tuple[datetime, str]]:
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
            suffix = job_id[len(plan_prefix):]
            if suffix == "sleep":
                entries.append((run_at, "睡眠异常主动"))
            elif suffix.startswith("window-retry:"):
                window_id = suffix.removeprefix("window-retry:")
                window = windows.get(window_id)
                label = f"延迟窗口 [{window_id}]"
                entries.append((run_at, label + (f" · {window.intent}" if window else "")))
            elif suffix.startswith("window:"):
                window_id = suffix.removeprefix("window:")
                window = windows.get(window_id)
                label = f"日程窗口 [{window_id}]"
                entries.append((run_at, label + (f" · {window.intent}" if window else "")))

        for task in self._pending_followups(umo):
            run_at = job_times.get(
                f"pvd:followup:{task.id}",
                parse_datetime(task.scheduled_at, self.timezone),
            )
            entries.append((run_at, f"用户回访 [{task.id}] · {task.intent}"))

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

    async def _image_view_results(self, event: AstrMessageEvent, title: str, fallback: str, jobs) -> list:
        if not self.image_renderer.enabled:
            return [event.plain_result(fallback)]
        try:
            paths = [await job() for job in jobs]
        except Exception:
            logger.exception("[虚拟人生] 图片渲染失败，回退文字输出")
            return [event.plain_result("图片渲染失败，已切换为文字模式。\n" + fallback)]
        return [event.plain_result(title), *(event.image_result(path) for path in paths)]

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

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=999)
    async def on_friend_message(self, event: AstrMessageEvent) -> None:
        await self._handle_incoming(event.unified_msg_origin)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=998)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        await self._handle_incoming(event.unified_msg_origin)

    async def _handle_incoming(self, umo: str) -> None:
        if not self.policy.is_enabled(umo):
            return
        self.policy.record_incoming(umo, self._now())
        await self.storage.save_sessions()
        self._schedule_idle(umo)

    @filter.on_llm_request()
    async def inject_virtual_state(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not self.smart_context_injector.enabled:
            return
        try:
            _, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        except RuntimeError:
            return
        if plan.status != "ok":
            return
        injection, modules, limit = self.smart_context_injector.build_details(
            plan,
            self._now(),
            self.long_term,
            event.get_message_str(),
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

    @filter.llm_tool(name="get_virtual_daily_schedule")
    async def get_virtual_daily_schedule(self, event: AstrMessageEvent) -> str:
        """查询机器人当前人格的完整虚拟日程、穿搭与当前活动。"""
        _, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        return "今日暂无可用日程。" if plan.status != "ok" else format_plan(plan, self._now())

    @filter.llm_tool(name="get_long_term_timeline")
    async def get_long_term_timeline(self, event: AstrMessageEvent) -> str:
        """查询机器人当前人格全部已批准的大时间表阶段。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        stages = self.long_term.list_for_persona(persona.id)
        if not stages:
            return "当前人格没有已批准的大时间表。"
        enriched = [self.long_term.with_holidays(stage) for stage in stages]
        return json.dumps({"persona_id": persona.id, "stages": enriched}, ensure_ascii=False, indent=2)

    @filter.llm_tool(name="schedule_proactive_followup")
    async def schedule_proactive_followup(self, event: AstrMessageEvent, scheduled_at: str, intent: str) -> str:
        """仅在用户明确要求稍后联系、提醒或询问结果时创建一次回访。scheduled_at 必须是明确的 ISO 8601 时间；时间不明确时先询问用户。"""
        try:
            task = await self._create_followup(event.unified_msg_origin, scheduled_at, intent)
            return f"已安排回访，任务 ID={task.id}，时间={task.scheduled_at}。"
        except ValueError as exc:
            return f"无法安排回访：{exc}"

    @filter.llm_tool(name="list_proactive_followups")
    async def list_proactive_followups(self, event: AstrMessageEvent) -> str:
        """列出当前会话所有待执行的主动回访任务。"""
        tasks = self._pending_followups(event.unified_msg_origin)
        return "当前没有待执行回访。" if not tasks else "\n".join(f"{task.id} | {task.scheduled_at} | {task.intent}" for task in tasks)

    @filter.llm_tool(name="cancel_proactive_followup")
    async def cancel_proactive_followup(self, event: AstrMessageEvent, task_id: str) -> str:
        """当用户取消请求或已经提前汇报结果时，取消当前会话指定的回访任务。"""
        return "已取消回访。" if await self._cancel_followup(event.unified_msg_origin, task_id) else "未找到可取消的回访任务。"

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
            yield event.plain_result(f"{action}当前{kind}会话，但今日日程调度失败，请检查日志或稍后重试。")
            return
        yield event.plain_result(f"{action}当前{kind}会话，主动消息将在符合日程与发送规则时触发。")

    @filter.command_group("虚拟日程")
    def virtual_daily_group(self):
        """虚拟日程命令组；不提供子命令时由 AstrBot 输出帮助。"""
        pass

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
        """重新生成今日虚拟日程。"""
        persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin, force=True, extra=extra or "")
        for umo in self.policy.enabled_sessions():
            resolved = await self.personas.resolve(umo)
            if resolved.id == persona.id:
                await self._schedule_session(umo, resolved, plan)
        yield event.plain_result("重写失败，请检查 LLM 输出。" if plan.status != "ok" else format_plan(plan, self._now()))

    @filter.command_group("大时间表")
    def long_term_group(self):
        """大时间表命令组；不提供子命令时由 AstrBot 输出帮助。"""
        pass

    @long_term_group.command("生成")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_generate(self, event: AstrMessageEvent, requirements: str | None = None):
        """根据自然语言要求生成追加草稿。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        latest = self.long_term.latest_stage(persona.id)
        start = date.fromisoformat(latest["end_date"]) + timedelta(days=1) if latest else self._now().date()
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
        yield event.plain_result("已生成大时间表草稿，使用 /大时间表 草稿 查看，确认后执行 /大时间表 批准。")

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
            approved = await self.long_term.approve_draft(persona.id, event.unified_msg_origin)
            self.image_renderer.invalidate_persona(persona.id)
            await self._refresh_persona_daily_plan(persona)
        except Exception as exc:
            yield event.plain_result(f"批准失败：{exc}")
            return
        yield event.plain_result("大时间表已生效并重生成今日日程：" + "、".join(stage["name"] for stage in approved))

    @long_term_group.command("拒绝")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def long_term_reject(self, event: AstrMessageEvent, feedback: str | None = None):
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
        previous = self.long_term.latest_stage(persona.id) if draft.get("mode") == "append" else None
        requirements = "；".join(part for part in (draft.get("requirements", ""), f"修改意见：{feedback}") if part)
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
        fallback = "\n".join(f"{stage['id']} | {stage['name']} | {stage['kind']} | {stage['start_date']} 至 {stage['end_date']} | 优先级 {stage['priority']}" for stage in stages)
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
    async def long_term_view(self, event: AstrMessageEvent, stage_id: str | None = None):
        """查看阶段；可省略参数或输入阶段 ID、名称及唯一片段。"""
        persona = await self.personas.resolve(event.unified_msg_origin)
        stage, candidates = self.long_term.resolve_stage(persona.id, self._now().date(), stage_id or "")
        if not stage:
            if candidates:
                lines = ["找到多个匹配阶段，请使用完整 ID 或名称："]
                lines.extend(f"- {item['id']} | {item['name']} | {item['start_date']} 至 {item['end_date']}" for item in candidates)
                yield event.plain_result("\n".join(lines))
            elif stage_id:
                yield event.plain_result("未找到匹配阶段，请先使用 /大时间表 列表 查看可用 ID。")
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
    async def long_term_regenerate(self, event: AstrMessageEvent, requirements: str | None = None):
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
        yield event.plain_result("已生成替换全部阶段的草稿，使用 /大时间表 草稿 查看，批准后才会生效。")

    @filter.command_group("主动消息")
    def proactive_group(self):
        """主动消息命令组；不提供子命令时由 AstrBot 输出帮助。"""
        pass

    @proactive_group.command("状态")
    async def proactive_status(self, event: AstrMessageEvent):
        persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        state = self.policy.ensure_state(event.unified_msg_origin, persona.id, plan, self._now())
        pending = len(self._pending_followups(event.unified_msg_origin))
        yield event.plain_result(
            f"人格：{persona.id}\n今日预算：{state.sent_count}/{state.daily_budget}\n"
            f"连续未回复：{state.unanswered_count}\n待执行回访：{pending}"
        )

    @proactive_group.command("立即")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def trigger_now(self, event: AstrMessageEvent):
        persona, plan = await self._ensure_plan_for_umo(event.unified_msg_origin)
        await self._deliver(event.unified_msg_origin, persona, plan, "管理员要求立即测试主动消息", 0)
        yield event.plain_result("测试主动消息已发送。")

    @proactive_group.command("回访列表")
    async def followup_list_command(self, event: AstrMessageEvent):
        yield event.plain_result(await self.list_proactive_followups(event))

    @proactive_group.command("取消回访")
    async def followup_cancel_command(self, event: AstrMessageEvent, task_id: str):
        yield event.plain_result(await self.cancel_proactive_followup(event, task_id))

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
