import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core.image_renderer import ScheduleImageRenderer
from core.models import DailyPlan

from tests.fixtures import outfit_payload


class FakeHtmlRenderer:
    def __init__(self, directory: Path):
        self.directory = directory
        self.calls = []

    async def __call__(self, template, data, return_url, options):
        self.calls.append((template, data, return_url, options))
        path = self.directory / f"render-{len(self.calls)}.png"
        path.write_bytes(b"image")
        return str(path)


def daily_plan():
    return DailyPlan.from_dict(
        {
            "date": "2026-07-14",
            "persona_id": "Caranlaf",
            "theme": "学习日",
            "mood": "专注",
            "outfit": outfit_payload("夏季学院风"),
            "timeline": [
                {
                    "id": "sleep",
                    "start": "00:00",
                    "end": "08:00",
                    "activity": "睡觉",
                    "state": "sleep",
                    "availability": "blocked",
                },
                {
                    "id": "study",
                    "start": "08:00",
                    "end": "24:00",
                    "activity": "学习",
                    "location": "图书馆",
                    "state": "focus",
                    "availability": "low",
                },
            ],
            "proactive_windows": [],
            "budget_bonus": {},
        }
    )


def stage():
    return {
        "id": "semester",
        "name": "秋季学期",
        "kind": "academic",
        "start_date": "2026-09-01",
        "end_date": "2027-01-20",
        "priority": 50,
        "summary": "正常上课",
        "weekly_rules": [
            {
                "weekdays": [2],
                "start": "18:00",
                "end": "20:00",
                "title": "社团例会",
                "location": "活动室",
                "participants": ["社员"],
                "required": True,
            }
        ],
        "special_dates": [],
        "holidays": [{"date": "2026-09-25", "name": "中秋节", "kind": "traditional"}],
        "special_periods": [],
        "milestones": [],
        "constraints": [],
    }


class ImageRendererTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeline_marks_and_summarizes_current_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(root, backend)
            long_term_day = {
                "stage": {
                    "name": "暑期学习阶段",
                    "kind": "academic",
                    "start_date": "2026-07-01",
                    "end_date": "2026-08-31",
                    "summary": "集中学习",
                },
                "active_periods": [
                    {
                        "name": "考试周",
                        "start_date": "2026-07-13",
                        "end_date": "2026-07-19",
                        "constraints": ["减少娱乐", "保证睡眠"],
                    }
                ],
                "holidays": [
                    {"date": "2026-07-14", "name": "纪念日", "kind": "public"}
                ],
            }
            output = await renderer.render_timeline(
                daily_plan(), datetime(2026, 7, 14, 9, 30), long_term_day
            )
            self.assertTrue(Path(output).exists())
            data = backend.calls[0][1]
            items = data["items"]
            self.assertFalse(items[0]["current"])
            self.assertTrue(items[1]["current"])
            self.assertEqual(data["status_label"], "当前时段 08:00–24:00")
            self.assertEqual(data["theme_text"], "学习日")
            self.assertEqual(data["mood"], "专注")
            self.assertEqual(data["current_stage"]["name"], "暑期学习阶段")
            self.assertEqual(
                data["active_periods"][0]["constraints_text"], "减少娱乐；保证睡眠"
            )
            self.assertEqual(data["today_holidays"][0]["name"], "纪念日")
            self.assertEqual(backend.calls[0][3]["type"], "png")

    async def test_timeline_without_stage_uses_empty_stage_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(root, backend)
            await renderer.render_timeline(
                daily_plan(),
                datetime(2026, 7, 14, 9, 30),
                {"stage": None, "active_periods": [], "holidays": []},
            )
            data = backend.calls[0][1]
            self.assertIsNone(data["current_stage"])
            self.assertEqual(data["active_periods"], [])
            self.assertEqual(data["today_holidays"], [])

    async def test_outfit_contains_style_theme_and_mood(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(root, backend)
            await renderer.render_outfit(daily_plan(), datetime(2026, 7, 14, 9, 30))
            data = backend.calls[0][1]
            self.assertEqual(data["outfit_style"], "日常休闲风")
            self.assertEqual(data["theme_text"], "学习日")
            self.assertEqual(data["mood"], "专注")
            self.assertEqual(data["outfit_items"][1]["label"], "内衣与打底")
            self.assertEqual(data["outfit_items"][2]["label"], "内裤")

    async def test_stage_render_is_cached_by_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(root, backend)
            first = await renderer.render_stage(stage(), "Caranlaf")
            second = await renderer.render_stage(stage(), "Caranlaf")
            self.assertEqual(first, second)
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(
                backend.calls[0][1]["stage"]["weekly_rules"][0]["weekday_text"], "周二"
            )
            self.assertEqual(
                backend.calls[0][1]["stage"]["holidays"][0]["name"], "中秋节"
            )

    async def test_stage_list_builds_relative_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(
                root, backend, {"image_theme": "light", "image_width": 900}
            )
            await renderer.render_stage_list([stage()], "Caranlaf")
            data = backend.calls[0][1]
            self.assertEqual(data["theme"], "light")
            self.assertEqual(data["width"], 900)
            self.assertEqual(data["stages"][0]["left"], 0.0)
            self.assertEqual(data["stages"][0]["width"], 100.0)


if __name__ == "__main__":
    unittest.main()
