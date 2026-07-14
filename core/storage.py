from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .models import DailyPlan, FollowupTask, SessionState


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
                return value if isinstance(value, dict) else json.loads(json.dumps(self.default))
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
        self.plans_repo = JsonRepository(data_dir / "plans.json", {"schema_version": 1, "plans": {}})
        self.sessions_repo = JsonRepository(data_dir / "sessions.json", {"schema_version": 1, "sessions": {}})
        self.followups_repo = JsonRepository(data_dir / "followups.json", {"schema_version": 1, "tasks": {}})
        self.plans: dict[str, DailyPlan] = {}
        self.sessions: dict[str, SessionState] = {}
        self.followups: dict[str, FollowupTask] = {}

    async def load(self) -> None:
        plans = await self.plans_repo.load()
        sessions = await self.sessions_repo.load()
        followups = await self.followups_repo.load()
        self.plans = {key: DailyPlan.from_dict(value) for key, value in plans.get("plans", {}).items()}
        self.sessions = {key: SessionState.from_dict(value) for key, value in sessions.get("sessions", {}).items()}
        self.followups = {key: FollowupTask.from_dict(value) for key, value in followups.get("tasks", {}).items()}

    @staticmethod
    def plan_key(date_str: str, persona_id: str) -> str:
        return f"{date_str}::{persona_id}"

    def get_plan(self, date_str: str, persona_id: str) -> DailyPlan | None:
        return self.plans.get(self.plan_key(date_str, persona_id))

    def get_recent_plans(self, persona_id: str, before: date, days: int) -> list[DailyPlan]:
        result: list[DailyPlan] = []
        for offset in range(1, max(0, days) + 1):
            plan = self.get_plan((before - timedelta(days=offset)).isoformat(), persona_id)
            if plan and plan.status == "ok":
                result.append(plan)
        result.reverse()
        return result

    async def save_plans(self) -> None:
        await self.plans_repo.save({"schema_version": 1, "plans": {key: value.to_dict() for key, value in self.plans.items()}})

    async def save_sessions(self) -> None:
        await self.sessions_repo.save({"schema_version": 1, "sessions": {key: value.to_dict() for key, value in self.sessions.items()}})

    async def save_followups(self) -> None:
        await self.followups_repo.save({"schema_version": 1, "tasks": {key: value.to_dict() for key, value in self.followups.items()}})
