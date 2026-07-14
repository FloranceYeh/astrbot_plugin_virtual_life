from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import minute_of_day
from .storage import JsonRepository

STAGE_KINDS = {"academic", "project", "custom"}


def normalize_priority(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("priority must be an integer from 0 to 100, not a boolean")
    if isinstance(value, int):
        priority = value
    elif isinstance(value, str):
        try:
            priority = int(value.strip())
        except ValueError as exc:
            raise ValueError("priority must be an integer from 0 to 100") from exc
    else:
        raise ValueError("priority must be an integer from 0 to 100")
    if not 0 <= priority <= 100:
        raise ValueError("priority must be between 0 and 100")
    return priority


def validate_stage(value: dict[str, Any], persona_id: str, *, stage_id: str | None = None) -> dict[str, Any]:
    stage = dict(value)
    stage["id"] = str(stage_id or stage.get("id", "")).strip()
    stage["persona_id"] = persona_id
    stage["name"] = str(stage.get("name", "")).strip()
    stage["kind"] = str(stage.get("kind", "custom")).strip()
    stage["summary"] = str(stage.get("summary", "")).strip()
    if not stage["id"] or not stage["name"]:
        raise ValueError("长期时间表阶段必须包含 id 和 name")
    if stage["kind"] not in STAGE_KINDS:
        raise ValueError("kind 必须是 academic、project 或 custom")
    start = date.fromisoformat(str(stage.get("start_date", "")))
    end = date.fromisoformat(str(stage.get("end_date", "")))
    if start > end:
        raise ValueError("start_date 不能晚于 end_date")
    stage["start_date"] = start.isoformat()
    stage["end_date"] = end.isoformat()
    stage["priority"] = normalize_priority(stage.get("priority", 0))
    stage["constraints"] = _strings(stage.get("constraints", []))
    stage["weekly_rules"] = _validate_weekly_rules(stage.get("weekly_rules", []))
    stage["special_dates"] = _validate_special_dates(stage.get("special_dates", []), start, end)
    stage["special_periods"] = _validate_special_periods(stage.get("special_periods", []), start, end)
    stage["milestones"] = _validate_milestones(stage.get("milestones", []), start, end)
    return stage


def validate_stage_bundle(value: dict[str, Any], persona_id: str, *, required_start: date | None = None) -> list[dict[str, Any]]:
    raw_stages = value.get("stages") if isinstance(value, dict) else None
    if not isinstance(raw_stages, list) or not raw_stages:
        raise ValueError("大时间表必须包含非空 stages 列表")
    stages = sorted(
        (validate_stage(raw, persona_id) for raw in raw_stages),
        key=lambda stage: (stage["start_date"], stage["end_date"], stage["id"]),
    )
    ids = [stage["id"] for stage in stages]
    if len(ids) != len(set(ids)):
        raise ValueError("大时间表阶段 ID 必须唯一")
    if required_start and date.fromisoformat(stages[0]["start_date"]) != required_start:
        raise ValueError(f"首个阶段必须从 {required_start.isoformat()} 开始")
    for previous, current in zip(stages, stages[1:], strict=False):
        expected = date.fromisoformat(previous["end_date"]) + timedelta(days=1)
        if date.fromisoformat(current["start_date"]) != expected:
            raise ValueError("同一批生成的阶段必须首尾连续，不得重叠或留空")
    return stages


def _strings(values: object) -> list[str]:
    return [str(item).strip() for item in values if str(item).strip()] if isinstance(values, list) else []


def _validate_event(raw: dict[str, Any]) -> dict[str, Any]:
    event = dict(raw)
    event["title"] = str(event.get("title", "")).strip()
    event["start"] = str(event.get("start", "")).strip()
    event["end"] = str(event.get("end", "")).strip()
    event["location"] = str(event.get("location", "")).strip()
    event["participants"] = _strings(event.get("participants", []))
    if not event["title"]:
        raise ValueError("固定事件 title 不能为空")
    if minute_of_day(event["start"]) >= minute_of_day(event["end"]):
        raise ValueError(f"固定事件时间无效: {event['title']}")
    event["required"] = bool(event.get("required", True))
    return event


def _validate_weekly_rules(values: object) -> list[dict[str, Any]]:
    result = []
    for raw in values if isinstance(values, list) else []:
        event = _validate_event(raw)
        weekdays = sorted({int(day) for day in raw.get("weekdays", [])})
        if not weekdays or any(day < 1 or day > 7 for day in weekdays):
            raise ValueError(f"周规则 weekdays 无效: {event['title']}")
        event["weekdays"] = weekdays
        result.append(event)
    return result


def _validate_special_dates(values: object, start: date, end: date) -> list[dict[str, Any]]:
    result = []
    for raw in values if isinstance(values, list) else []:
        event = _validate_event(raw)
        target = date.fromisoformat(str(raw.get("date", "")))
        if not start <= target <= end:
            raise ValueError(f"特殊日期超出阶段范围: {target}")
        event["date"] = target.isoformat()
        result.append(event)
    return result


def _validate_special_periods(values: object, start: date, end: date) -> list[dict[str, Any]]:
    result = []
    for raw in values if isinstance(values, list) else []:
        period_start = date.fromisoformat(str(raw.get("start_date", "")))
        period_end = date.fromisoformat(str(raw.get("end_date", "")))
        if period_start > period_end or period_start < start or period_end > end:
            raise ValueError("特殊时期日期范围无效")
        result.append(
            {
                "name": str(raw.get("name", "特殊时期")).strip(),
                "start_date": period_start.isoformat(),
                "end_date": period_end.isoformat(),
                "constraints": _strings(raw.get("constraints", [])),
            }
        )
    return result


def _validate_milestones(values: object, start: date, end: date) -> list[dict[str, Any]]:
    result = []
    for raw in values if isinstance(values, list) else []:
        target = date.fromisoformat(str(raw.get("date", "")))
        if not start <= target <= end:
            raise ValueError(f"里程碑超出阶段范围: {target}")
        title = str(raw.get("title", "")).strip()
        if not title:
            raise ValueError("里程碑 title 不能为空")
        result.append({"date": target.isoformat(), "title": title, "lead_days": max(0, int(raw.get("lead_days", 0)))})
    return result


def _overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return minute_of_day(left["start"]) < minute_of_day(right["end"]) and minute_of_day(right["start"]) < minute_of_day(left["end"])


class LongTermTimelineStore:
    def __init__(self, data_dir: Path):
        self.repo = JsonRepository(data_dir / "long_term_timelines.json", {"schema_version": 1, "stages": []})
        self.stages: list[dict[str, Any]] = []
        self.drafts: dict[str, dict[str, Any]] = {}
        self.notification_targets: dict[str, str] = {}

    async def load(self) -> None:
        value = await self.repo.load()
        self.stages = [dict(item) for item in value.get("stages", []) if isinstance(item, dict)]
        self.drafts = {str(key): dict(item) for key, item in value.get("drafts", {}).items() if isinstance(item, dict)}
        self.notification_targets = {
            str(key): str(item)
            for key, item in value.get("notification_targets", {}).items()
            if str(item).strip()
        }

    async def save(self) -> None:
        await self.repo.save(
            {
                "schema_version": 1,
                "stages": self.stages,
                "drafts": self.drafts,
                "notification_targets": self.notification_targets,
            }
        )

    async def set_draft(
        self,
        persona_id: str,
        stages: list[dict[str, Any]],
        *,
        source: str,
        admin_umo: str,
        created_at: str,
        requirements: str = "",
        mode: str = "append",
    ) -> dict[str, Any]:
        if mode not in {"append", "replace_all"}:
            raise ValueError("草稿 mode 必须是 append 或 replace_all")
        draft = {
            "persona_id": persona_id,
            "stages": stages,
            "source": source,
            "admin_umo": admin_umo,
            "created_at": created_at,
            "requirements": requirements,
            "mode": mode,
        }
        self.drafts[persona_id] = draft
        await self.save()
        return dict(draft)

    def get_draft(self, persona_id: str) -> dict[str, Any] | None:
        draft = self.drafts.get(persona_id)
        return dict(draft) if draft else None

    async def reject_draft(self, persona_id: str) -> bool:
        if persona_id not in self.drafts:
            return False
        self.drafts.pop(persona_id, None)
        await self.save()
        return True

    async def approve_draft(self, persona_id: str, admin_umo: str) -> list[dict[str, Any]]:
        draft = self.drafts.get(persona_id)
        if not draft:
            raise ValueError("当前人格没有待批准草稿")
        approved = [dict(stage) for stage in draft["stages"]]
        if draft.get("mode") == "replace_all":
            self.stages = [stage for stage in self.stages if stage.get("persona_id") != persona_id]
        approved_ids = {stage["id"] for stage in approved}
        self.stages = [
            stage
            for stage in self.stages
            if not (stage.get("persona_id") == persona_id and stage.get("id") in approved_ids)
        ]
        self.stages.extend(approved)
        self.drafts.pop(persona_id, None)
        self.notification_targets[persona_id] = admin_umo
        await self.save()
        return approved

    async def add_auto_renewal(self, persona_id: str, stages: list[dict[str, Any]]) -> None:
        existing_ids = {stage["id"] for stage in self.stages if stage.get("persona_id") == persona_id}
        self.stages.extend(stage for stage in stages if stage["id"] not in existing_ids)
        await self.save()

    def notification_target(self, persona_id: str) -> str | None:
        return self.notification_targets.get(persona_id)

    def latest_stage(self, persona_id: str) -> dict[str, Any] | None:
        stages = self.list_for_persona(persona_id)
        return dict(max(stages, key=lambda stage: (stage["end_date"], stage["priority"]))) if stages else None

    def has_stage_starting_after(self, persona_id: str, target: date) -> bool:
        return any(
            stage.get("persona_id") == persona_id and date.fromisoformat(stage["start_date"]) > target
            for stage in self.stages
        )

    def list_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        return sorted(
            (dict(stage) for stage in self.stages if stage.get("persona_id") == persona_id),
            key=lambda stage: (stage["start_date"], -int(stage.get("priority", 0)), stage["id"]),
        )

    def find(self, persona_id: str, stage_id: str) -> dict[str, Any] | None:
        return next(
            (dict(stage) for stage in self.stages if stage.get("persona_id") == persona_id and stage.get("id") == stage_id),
            None,
        )

    def active_stage(self, persona_id: str, target: date) -> dict[str, Any] | None:
        matched = [
            stage
            for stage in self.stages
            if stage.get("persona_id") == persona_id
            and date.fromisoformat(stage["start_date"]) <= target <= date.fromisoformat(stage["end_date"])
        ]
        if not matched:
            return None
        return dict(
            min(
                matched,
                key=lambda stage: (
                    -int(stage.get("priority", 0)),
                    (date.fromisoformat(stage["end_date"]) - date.fromisoformat(stage["start_date"])).days,
                    self.stages.index(stage),
                ),
            )
        )

    def expand_day(self, persona_id: str, target: date) -> dict[str, Any] | None:
        stage = self.active_stage(persona_id, target)
        if not stage:
            return None
        weekly = [
            dict(item)
            for item in stage.get("weekly_rules", [])
            if target.isoweekday() in item.get("weekdays", []) and item.get("required", True)
        ]
        special = [
            dict(item)
            for item in stage.get("special_dates", [])
            if item.get("date") == target.isoformat() and item.get("required", True)
        ]
        weekly = [item for item in weekly if not any(_overlap(item, special_item) for special_item in special)]
        fixed_events = sorted(weekly + special, key=lambda item: item["start"])
        for index, event in enumerate(fixed_events):
            if any(_overlap(event, other) for other in fixed_events[index + 1 :]):
                raise ValueError(f"长期时间表固定事件冲突: {event['title']}")

        constraints = list(stage.get("constraints", []))
        active_periods = []
        for period in stage.get("special_periods", []):
            if date.fromisoformat(period["start_date"]) <= target <= date.fromisoformat(period["end_date"]):
                active_periods.append(dict(period))
                constraints.extend(period.get("constraints", []))

        milestones = []
        for milestone in stage.get("milestones", []):
            milestone_date = date.fromisoformat(milestone["date"])
            if milestone_date - timedelta(days=int(milestone.get("lead_days", 0))) <= target <= milestone_date:
                milestones.append(dict(milestone))

        return {
            "stage": stage,
            "fixed_events": fixed_events,
            "active_periods": active_periods,
            "constraints": list(dict.fromkeys(constraints)),
            "milestones": milestones,
        }

    def format_day_context(self, persona_id: str, target: date, *, fallback_to_latest: bool = False) -> str:
        expanded = self.expand_day(persona_id, target)
        if not expanded:
            latest = self.latest_stage(persona_id) if fallback_to_latest else None
            if latest:
                return (
                    "<long_term_timeline>\n"
                    f"最近阶段已于 {latest['end_date']} 结束：{latest['name']}（{latest['kind']}）。\n"
                    f"阶段说明：{latest.get('summary') or '无'}\n"
                    "后续阶段正在自动生成中；在生成成功前，可延续该阶段的人物背景和一般约束，"
                    "但不要虚构已经过期的固定课程、会议或截止日期。\n"
                    "一般约束：" + ("；".join(latest.get("constraints", [])) or "无") + "\n"
                    "</long_term_timeline>"
                )
            return "长期时间表：当前日期没有生效阶段。"
        stage = expanded["stage"]
        lines = [
            "<long_term_timeline>",
            f"阶段：{stage['name']}（{stage['kind']}，{stage['start_date']} 至 {stage['end_date']}）",
            f"说明：{stage.get('summary') or '无'}",
            "今日必须保留的固定事件：",
        ]
        lines.extend(
            f"- {item['start']}-{item['end']} {item['title']}"
            + (f" @ {item['location']}" if item.get("location") else "")
            for item in expanded["fixed_events"]
        )
        if not expanded["fixed_events"]:
            lines.append("- 无")
        lines.append("阶段约束：" + ("；".join(expanded["constraints"]) if expanded["constraints"] else "无"))
        lines.append("临近里程碑：")
        lines.extend(f"- {item['date']} {item['title']}" for item in expanded["milestones"])
        if not expanded["milestones"]:
            lines.append("- 无")
        lines.extend(["生成当天日程时必须保留固定事件，并围绕阶段约束和里程碑安排其余活动。", "</long_term_timeline>"])
        return "\n".join(lines)
