import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.models import (
    DailyPlan,
    FollowupTask,
    ScheduleRequirement,
    SessionState,
)
from core.storage import PluginStorage

from tests.fixtures import outfit_payload


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = PluginStorage(Path(directory))
            plan = DailyPlan.from_dict(
                {
                    "date": "2026-07-14",
                    "persona_id": "alice",
                    "theme": "日常",
                    "mood": "平静",
                    "outfit": outfit_payload(),
                    "timeline": [
                        {
                            "id": "all",
                            "start": "00:00",
                            "end": "24:00",
                            "activity": "休息",
                            "state": "available",
                            "availability": "normal",
                        }
                    ],
                    "proactive_windows": [],
                    "budget_bonus": {"private": 0, "group": 0},
                }
            )
            storage.plans[storage.plan_key(plan.date, plan.persona_id)] = plan
            storage.schedule_requirements["req"] = ScheduleRequirement(
                id="req",
                persona_id="alice",
                start_date="2026-07-15",
                end_date="2026-07-16",
                requirement="减少外出",
                created_at="2026-07-14T12:00:00+08:00",
            )
            storage.sessions["umo"] = SessionState(
                date=plan.date, persona_id="alice", daily_budget=2
            )
            storage.followups["task"] = FollowupTask(
                "task",
                "umo",
                "alice",
                "2026-07-14T13:00:00+08:00",
                "问结果",
                "2026-07-14T12:00:00+08:00",
            )
            await storage.save_plans()
            await storage.save_sessions()
            await storage.save_followups()

            restored = PluginStorage(Path(directory))
            await restored.load()
            self.assertEqual(restored.get_plan(plan.date, "alice").theme, "日常")
            self.assertEqual(
                restored.schedule_requirements["req"].requirement, "减少外出"
            )
            self.assertEqual(restored.sessions["umo"].daily_budget, 2)
            self.assertEqual(restored.followups["task"].intent, "问结果")

    async def test_recent_plans_are_persona_isolated_and_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = PluginStorage(Path(directory))

            def make_plan(date_str, persona_id, theme):
                return DailyPlan.from_dict(
                    {
                        "date": date_str,
                        "persona_id": persona_id,
                        "theme": theme,
                        "mood": "平静",
                        "outfit": outfit_payload(),
                        "timeline": [
                            {
                                "id": "all",
                                "start": "00:00",
                                "end": "24:00",
                                "activity": theme,
                                "state": "available",
                                "availability": "normal",
                            }
                        ],
                        "proactive_windows": [],
                        "budget_bonus": {"private": 0, "group": 0},
                    }
                )

            for plan in (
                make_plan("2026-07-11", "alice", "太早"),
                make_plan("2026-07-12", "alice", "前天"),
                make_plan("2026-07-13", "alice", "昨天"),
                make_plan("2026-07-13", "bob", "其他人格"),
            ):
                storage.plans[storage.plan_key(plan.date, plan.persona_id)] = plan

            recent = storage.get_recent_plans("alice", date(2026, 7, 14), 2)
            self.assertEqual([plan.theme for plan in recent], ["前天", "昨天"])

    async def test_legacy_string_outfit_is_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = PluginStorage(Path(directory))
            await storage.plans_repo.save(
                {
                    "schema_version": 1,
                    "plans": {
                        "2026-07-14::alice": {
                            "date": "2026-07-14",
                            "persona_id": "alice",
                            "theme": "旧日程",
                            "mood": "平静",
                            "outfit": "旧版字符串穿搭",
                            "timeline": [],
                        }
                    },
                }
            )
            await storage.load()
            self.assertEqual(storage.plans, {})

    async def test_schedule_requirements_are_persona_scoped_and_consumed_per_day(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            storage = PluginStorage(Path(directory))
            storage.schedule_requirements["req"] = ScheduleRequirement(
                id="req",
                persona_id="alice",
                start_date="2026-07-15",
                end_date="2026-07-16",
                requirement="减少外出",
                created_at="2026-07-14T12:00:00+08:00",
            )

            self.assertEqual(
                [
                    item.id
                    for item in storage.active_schedule_requirements(
                        "alice", date(2026, 7, 15)
                    )
                ],
                ["req"],
            )
            self.assertEqual(
                storage.active_schedule_requirements("bob", date(2026, 7, 15)),
                [],
            )

            self.assertEqual(
                storage.consume_schedule_requirements(["req"], date(2026, 7, 15)),
                1,
            )
            self.assertEqual(
                storage.active_schedule_requirements("alice", date(2026, 7, 15)),
                [],
            )
            self.assertEqual(
                [
                    item.id
                    for item in storage.active_schedule_requirements(
                        "alice", date(2026, 7, 16)
                    )
                ],
                ["req"],
            )

            storage.consume_schedule_requirements(["req"], date(2026, 7, 16))
            self.assertNotIn("req", storage.schedule_requirements)

    async def test_expired_schedule_requirements_are_pruned(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = PluginStorage(Path(directory))
            storage.schedule_requirements["expired"] = ScheduleRequirement(
                id="expired",
                persona_id="alice",
                start_date="2026-07-14",
                end_date="2026-07-14",
                requirement="旧要求",
                created_at="2026-07-13T12:00:00+08:00",
            )
            storage.schedule_requirements["current"] = ScheduleRequirement(
                id="current",
                persona_id="alice",
                start_date="2026-07-15",
                end_date="2026-07-15",
                requirement="今日要求",
                created_at="2026-07-14T12:00:00+08:00",
            )

            self.assertEqual(storage.prune_schedule_requirements(date(2026, 7, 15)), 1)
            self.assertEqual(set(storage.schedule_requirements), {"current"})


if __name__ == "__main__":
    unittest.main()
