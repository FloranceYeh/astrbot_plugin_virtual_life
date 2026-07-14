import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.long_term import LongTermTimelineStore, validate_stage, validate_stage_bundle


def academic_stage():
    return {
        "id": "semester-fall",
        "name": "秋季学期",
        "kind": "academic",
        "start_date": "2026-09-01",
        "end_date": "2027-01-20",
        "priority": 10,
        "summary": "正常上课并准备期末考试",
        "weekly_rules": [{"weekdays": [1], "start": "08:00", "end": "10:00", "title": "高等数学", "location": "教学楼"}],
        "special_dates": [{"date": "2026-09-07", "start": "08:00", "end": "11:00", "title": "开学典礼"}],
        "special_periods": [{"name": "期末周", "start_date": "2027-01-10", "end_date": "2027-01-20", "constraints": ["减少娱乐"]}],
        "milestones": [{"date": "2026-09-10", "title": "提交选课确认", "lead_days": 3}],
        "constraints": ["工作日保持学生作息"],
    }


class LongTermTests(unittest.IsolatedAsyncioTestCase):
    def test_validation_sets_persona_and_kind(self):
        stage = validate_stage(academic_stage(), "student")
        self.assertEqual(stage["persona_id"], "student")
        self.assertEqual(stage["kind"], "academic")

    async def test_special_date_overrides_weekly_event(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LongTermTimelineStore(Path(directory))
            store.stages = [validate_stage(academic_stage(), "student")]
            expanded = store.expand_day("student", date(2026, 9, 7))
            self.assertEqual([item["title"] for item in expanded["fixed_events"]], ["开学典礼"])
            self.assertEqual([item["title"] for item in expanded["milestones"]], ["提交选课确认"])

    async def test_persona_isolation_and_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LongTermTimelineStore(Path(directory))
            student = validate_stage(academic_stage(), "student")
            project = validate_stage(
                {
                    "id": "release",
                    "name": "发布工期",
                    "kind": "project",
                    "start_date": "2026-09-01",
                    "end_date": "2026-10-01",
                    "priority": 20,
                    "weekly_rules": [],
                    "special_dates": [],
                    "special_periods": [],
                    "milestones": [],
                    "constraints": ["优先解决阻塞项"],
                },
                "worker",
            )
            store.stages = [student, project]
            self.assertEqual(store.active_stage("student", date(2026, 9, 10))["id"], "semester-fall")
            self.assertEqual(store.active_stage("worker", date(2026, 9, 10))["id"], "release")

    async def test_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = LongTermTimelineStore(path)
            store.stages = [validate_stage(academic_stage(), "student")]
            await store.save()
            restored = LongTermTimelineStore(path)
            await restored.load()
            self.assertEqual(restored.find("student", "semester-fall")["name"], "秋季学期")

    def test_bundle_requires_continuous_stages(self):
        first = academic_stage()
        second = {
            "id": "winter-break",
            "name": "寒假",
            "kind": "academic",
            "start_date": "2027-01-22",
            "end_date": "2027-02-20",
        }
        with self.assertRaises(ValueError):
            validate_stage_bundle({"stages": [first, second]}, "student")

    async def test_draft_approval_records_notification_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = LongTermTimelineStore(path)
            stages = validate_stage_bundle({"stages": [academic_stage()]}, "student")
            await store.set_draft(
                "student",
                stages,
                source="natural",
                admin_umo="admin-session",
                created_at="2026-07-14T12:00:00+08:00",
                requirements="生成校历",
            )
            approved = await store.approve_draft("student", "admin-session")
            restored = LongTermTimelineStore(path)
            await restored.load()
            self.assertEqual(approved[0]["id"], "semester-fall")
            self.assertIsNone(restored.get_draft("student"))
            self.assertEqual(restored.notification_target("student"), "admin-session")


if __name__ == "__main__":
    unittest.main()
