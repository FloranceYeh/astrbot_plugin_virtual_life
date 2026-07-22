from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from .models import OUTFIT_CATEGORY_LABELS, DailyPlan, TimelineItem, minute_of_day


class SmartContextInjector:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enable", False))

    def build(
        self,
        plan: DailyPlan,
        now: datetime,
        long_term,
        user_text: str,
    ) -> str:
        return self.build_details(plan, now, long_term, user_text)[0]

    def build_details(
        self,
        plan: DailyPlan,
        now: datetime,
        long_term,
        user_text: str,
    ) -> tuple[str, tuple[str, ...], int]:
        limit = self._max_chars()
        current = self._current_item(plan, now)
        if not current:
            return "", (), limit
        normalized_text = self._normalize(user_text)
        outfit_matched = self._matches(normalized_text, self.settings.get("outfit_keywords"))
        underwear_matched = self._matches(normalized_text, self.settings.get("underwear_keywords"))
        schedule_matched = self._matches(normalized_text, self.settings.get("schedule_keywords"))
        long_term_matched = self._matches(normalized_text, self.settings.get("long_term_keywords"))
        full_schedule_matched = self._matches(normalized_text, self.settings.get("full_schedule_keywords"))
        full_long_term_matched = self._matches(normalized_text, self.settings.get("full_long_term_keywords"))
        sections = []
        modules = []

        base_module_enabled = bool(self.settings.get("base_module_enable", False))
        if base_module_enabled:
            modules.append("base")
            if not schedule_matched:
                sections.append(self._base_section(plan, now, current))

        if full_schedule_matched:
            modules.append("full_schedule_query")
            sections.append(
                "用户明确请求完整信息：必须优先调用 "
                "get_virtual_daily_schedule，不要凭当前摘要补全未查询的数据。"
            )
        if full_long_term_matched:
            modules.append("full_long_term_query")
            sections.append(
                "用户明确请求完整信息：必须优先调用 "
                "get_long_term_timeline，不要凭当前摘要补全未查询的数据。"
            )
        if schedule_matched:
            modules.append("schedule")
            sections.append(self._schedule_section(plan, now, current))
        if long_term_matched:
            modules.append("long_term")
            sections.extend(self._long_term_sections(long_term, plan.persona_id, now.date()))
        if outfit_matched:
            modules.append("outfit")
            if underwear_matched:
                modules.append("underwear")
            sections.append(self._outfit_section(plan, include_underwear=underwear_matched))
        elif underwear_matched:
            modules.append("underwear")
            sections.append(self._outfit_section(plan, include_underwear=True, underwear_only=True))

        injection = self._join_with_limit(sections, limit)
        return injection, tuple(modules), limit

    def _max_chars(self) -> int:
        try:
            return max(400, min(8000, int(self.settings.get("max_chars", 1600))))
        except (TypeError, ValueError):
            return 1600

    def _milestone_days(self) -> int:
        try:
            return max(0, min(90, int(self.settings.get("long_term_milestone_days", 7))))
        except (TypeError, ValueError):
            return 7

    def _matches(self, normalized_text: str, values: Any) -> bool:
        if not isinstance(values, list):
            return False
        return any(
            normalized_keyword and normalized_keyword in normalized_text
            for value in values
            if (normalized_keyword := self._normalize(str(value)))
        )

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(char for char in normalized if not char.isspace() and not unicodedata.category(char).startswith("P"))

    @staticmethod
    def _current_item(plan: DailyPlan, now: datetime) -> TimelineItem | None:
        minute = now.hour * 60 + now.minute
        return next(
            (
                item
                for item in plan.timeline
                if minute_of_day(item.start) <= minute < minute_of_day(item.end)
            ),
            None,
        )

    def _base_section(self, plan: DailyPlan, now: datetime, current: TimelineItem) -> str:
        return (
            f"时间：{now.strftime('%Y-%m-%d %H:%M')}\n"
            f"当前活动：{current.activity}\n"
            f"地点：{current.location or '未说明'}\n"
            f"状态：{current.state}，可打扰度：{current.availability}\n"
            "普通回复必须与当前状态保持一致。只有用户明确要求稍后提醒、到点联系或询问后续时，"
            "才可调用 schedule_proactive_followup；用户提前汇报结果时，应先查询并取消对应回访。"
        )

    def _schedule_section(self, plan: DailyPlan, now: datetime, current: TimelineItem) -> str:
        next_item = self._next_item(plan, now)
        lines = [
            self._base_section(plan, now, current),
            f"今日主题：{plan.theme}；心情：{plan.mood}。",
            f"当前时段：{current.start}-{current.end}。",
        ]
        if next_item:
            lines.append(f"下一项：{next_item.start}-{next_item.end} {next_item.activity}。")
        return "\n".join(lines)

    @staticmethod
    def _next_item(plan: DailyPlan, now: datetime) -> TimelineItem | None:
        minute = now.hour * 60 + now.minute
        return next((item for item in plan.timeline if minute_of_day(item.start) > minute), None)

    def _outfit_section(self, plan: DailyPlan, *, include_underwear: bool, underwear_only: bool = False) -> str:
        items = []
        for item in plan.outfit.items:
            if underwear_only and item.category not in {"underwear", "underpants"}:
                continue
            if not include_underwear and item.category in {"underwear", "underpants"}:
                continue
            detail = f"（{item.details}）" if item.details else ""
            items.append(f"{OUTFIT_CATEGORY_LABELS[item.category]}：{item.name}{detail}")
        if not items:
            return ""
        prefix = "贴身穿搭信息：" if underwear_only else f"今日穿搭：{plan.outfit.summary}。"
        return prefix + "；".join(items)

    def _long_term_sections(self, long_term, persona_id: str, target: date) -> list[str]:
        expanded = long_term.expand_day(persona_id, target)
        if not expanded:
            latest = long_term.latest_stage(persona_id)
            if not latest:
                return ["大时间表：当前没有已批准阶段。"]
            return [
                f"最近阶段已于 {latest['end_date']} 结束：{latest['name']}。后续阶段正在生成中，"
                "不要虚构已过期的固定课程、会议或截止日期。"
            ]
        stage = expanded["stage"]
        sections = [
            f"当前大时间表阶段：{stage['name']}（{stage['kind']}，{stage['start_date']} 至 {stage['end_date']}）。"
            + (f"摘要：{stage['summary']}。" if stage.get("summary") else "")
            + (f"约束：{'；'.join(expanded['constraints'])}。" if expanded["constraints"] else "")
        ]
        if expanded["fixed_events"]:
            sections.append(
                "今日固定事件："
                + "；".join(f"{item['start']}-{item['end']} {item['title']}" for item in expanded["fixed_events"])
                + "。"
            )
        if expanded["active_periods"]:
            sections.append("当前特殊时期：" + "；".join(item["name"] for item in expanded["active_periods"]) + "。")
        deadline = target + timedelta(days=self._milestone_days())
        milestones = [
            item
            for item in stage.get("milestones", [])
            if target <= date.fromisoformat(item["date"]) <= deadline
        ]
        if milestones:
            sections.append("近期里程碑：" + "；".join(f"{item['date']} {item['title']}" for item in milestones) + "。")
        return sections

    @staticmethod
    def _join_with_limit(sections: list[str], limit: int) -> str:
        parts = [section for section in sections if section]
        if not parts:
            return ""
        content = "<character_state>\n" + "\n".join(parts)
        closing = "\n</character_state>"
        if len(content) + len(closing) > limit:
            content = content[: max(0, limit - len(closing) - 1)] + "…"
        return content + closing
