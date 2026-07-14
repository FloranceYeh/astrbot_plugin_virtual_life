import tempfile
import unittest
from pathlib import Path

from core.models import DailyPlan, FollowupTask, SessionState
from core.storage import PluginStorage


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
                    "outfit": "休闲装",
                    "timeline": [{"id": "all", "start": "00:00", "end": "24:00", "activity": "休息", "state": "available", "availability": "normal"}],
                    "proactive_windows": [],
                    "budget_bonus": {"private": 0, "group": 0},
                }
            )
            storage.plans[storage.plan_key(plan.date, plan.persona_id)] = plan
            storage.sessions["umo"] = SessionState(date=plan.date, persona_id="alice", daily_budget=2)
            storage.followups["task"] = FollowupTask("task", "umo", "alice", "2026-07-14T13:00:00+08:00", "问结果", "2026-07-14T12:00:00+08:00")
            await storage.save_plans()
            await storage.save_sessions()
            await storage.save_followups()

            restored = PluginStorage(Path(directory))
            await restored.load()
            self.assertEqual(restored.get_plan(plan.date, "alice").theme, "日常")
            self.assertEqual(restored.sessions["umo"].daily_budget, 2)
            self.assertEqual(restored.followups["task"].intent, "问结果")


if __name__ == "__main__":
    unittest.main()

