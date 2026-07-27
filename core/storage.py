from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import DailyPlan, FollowupTask, ScheduleRequirement, SessionState


class JsonRepository:
    def __init__(self, path: Path, default: dict[str, Any]):
        self.path = path
        self.default = default
        self.lock = asyncio.Lock()

    async def load(self) -> dict[str, Any]:
        async with self.lock:
            if not self.path.exists():
                return json.loads(json.dumps(self.default))
            try:
                content = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
                value = json.loads(content)
                return (
                    value
                    if isinstance(value, dict)
                    else json.loads(json.dumps(self.default))
                )
            except (OSError, json.JSONDecodeError):
                return json.loads(json.dumps(self.default))

    async def save(self, value: dict[str, Any]) -> None:
        async with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            content = json.dumps(value, ensure_ascii=False, indent=2)
            await asyncio.to_thread(temporary.write_text, content, encoding="utf-8")
            await asyncio.to_thread(temporary.replace, self.path)


class PluginStorage:
    def __init__(self, data_dir: Path):
        self.plans_repo = JsonRepository(
            data_dir / "plans.json",
            {"schema_version": 3, "plans": {}, "schedule_requirements": {}},
        )
        self.sessions_repo = JsonRepository(
            data_dir / "sessions.json", {"schema_version": 1, "sessions": {}}
        )
        self.followups_repo = JsonRepository(
            data_dir / "followups.json", {"schema_version": 1, "tasks": {}}
        )
        self.plans: dict[str, DailyPlan] = {}
        self.schedule_requirements: dict[str, ScheduleRequirement] = {}
        self.sessions: dict[str, SessionState] = {}
        self.followups: dict[str, FollowupTask] = {}

    async def load(self) -> None:
        plans = await self.plans_repo.load()
        sessions = await self.sessions_repo.load()
        followups = await self.followups_repo.load()
        self.plans = {}
        for key, value in plans.get("plans", {}).items():
            try:
                self.plans[key] = DailyPlan.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
        self.schedule_requirements = {}
        for value in plans.get("schedule_requirements", {}).values():
            try:
                requirement = ScheduleRequirement.from_dict(value)
                self.schedule_requirements[requirement.id] = requirement
            except (TypeError, ValueError):
                continue
        self.sessions = {
            key: SessionState.from_dict(value)
            for key, value in sessions.get("sessions", {}).items()
        }
        self.followups = {
            key: FollowupTask.from_dict(value)
            for key, value in followups.get("tasks", {}).items()
        }

    @staticmethod
    def plan_key(date_str: str, persona_id: str) -> str:
        return f"{date_str}::{persona_id}"

    def get_plan(self, date_str: str, persona_id: str) -> DailyPlan | None:
        return self.plans.get(self.plan_key(date_str, persona_id))

    def get_recent_plans(
        self, persona_id: str, before: date, days: int
    ) -> list[DailyPlan]:
        result: list[DailyPlan] = []
        for offset in range(1, max(0, days) + 1):
            plan = self.get_plan(
                (before - timedelta(days=offset)).isoformat(), persona_id
            )
            if plan and plan.status == "ok":
                result.append(plan)
        result.reverse()
        return result

    def active_schedule_requirements(
        self, persona_id: str, target: date
    ) -> list[ScheduleRequirement]:
        target_str = target.isoformat()
        result = [
            item
            for item in self.schedule_requirements.values()
            if item.persona_id == persona_id
            and item.start_date <= target_str <= item.end_date
            and target_str not in item.consumed_dates
        ]
        return sorted(result, key=lambda item: (item.created_at, item.id))

    def consume_schedule_requirements(
        self, requirement_ids: list[str], target: date
    ) -> int:
        target_str = target.isoformat()
        changed = 0
        for requirement_id in requirement_ids:
            item = self.schedule_requirements.get(requirement_id)
            if (
                item is None
                or not item.start_date <= target_str <= item.end_date
                or target_str in item.consumed_dates
            ):
                continue
            consumed_dates = tuple(sorted((*item.consumed_dates, target_str)))
            expected_days = (
                date.fromisoformat(item.end_date) - date.fromisoformat(item.start_date)
            ).days + 1
            if len(consumed_dates) >= expected_days:
                del self.schedule_requirements[requirement_id]
            else:
                self.schedule_requirements[requirement_id] = replace(
                    item, consumed_dates=consumed_dates
                )
            changed += 1
        return changed

    def prune_schedule_requirements(self, today: date) -> int:
        expired = [
            key
            for key, item in self.schedule_requirements.items()
            if date.fromisoformat(item.end_date) < today
        ]
        for key in expired:
            del self.schedule_requirements[key]
        return len(expired)

    async def save_plans(self) -> None:
        await self.plans_repo.save(
            {
                "schema_version": 3,
                "plans": {key: value.to_dict() for key, value in self.plans.items()},
                "schedule_requirements": {
                    key: value.to_dict()
                    for key, value in self.schedule_requirements.items()
                },
            }
        )

    async def save_sessions(self) -> None:
        await self.sessions_repo.save(
            {
                "schema_version": 1,
                "sessions": {
                    key: value.to_dict() for key, value in self.sessions.items()
                },
            }
        )

    async def save_followups(self) -> None:
        await self.followups_repo.save(
            {
                "schema_version": 1,
                "tasks": {
                    key: value.to_dict() for key, value in self.followups.items()
                },
            }
        )
