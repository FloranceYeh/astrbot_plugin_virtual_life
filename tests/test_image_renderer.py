import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core.image_renderer import ScheduleImageRenderer
from core.models import DailyPlan


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
            "outfit": "夏季校服",
            "timeline": [
                {"id": "sleep", "start": "00:00", "end": "08:00", "activity": "睡觉", "state": "sleep", "availability": "blocked"},
                {"id": "study", "start": "08:00", "end": "24:00", "activity": "学习", "location": "图书馆", "state": "focus", "availability": "low"},
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
        "weekly_rules": [{"weekdays": [2], "start": "18:00", "end": "20:00", "title": "社团例会", "location": "活动室", "participants": ["社员"], "required": True}],
        "special_dates": [],
        "special_periods": [],
        "milestones": [],
        "constraints": [],
    }


class ImageRendererTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_marks_current_item(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(root, backend)
            output = await renderer.render_daily(daily_plan(), datetime(2026, 7, 14, 9, 30))
            self.assertTrue(Path(output).exists())
            items = backend.calls[0][1]["items"]
            self.assertFalse(items[0]["current"])
            self.assertTrue(items[1]["current"])
            self.assertEqual(backend.calls[0][3]["type"], "png")

    async def test_stage_render_is_cached_by_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(root, backend)
            first = await renderer.render_stage(stage(), "Caranlaf")
            second = await renderer.render_stage(stage(), "Caranlaf")
            self.assertEqual(first, second)
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(backend.calls[0][1]["stage"]["weekly_rules"][0]["weekday_text"], "周二")

    async def test_stage_list_builds_relative_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = FakeHtmlRenderer(root)
            renderer = ScheduleImageRenderer(root, backend, {"image_theme": "light", "image_width": 900})
            await renderer.render_stage_list([stage()], "Caranlaf")
            data = backend.calls[0][1]
            self.assertEqual(data["theme"], "light")
            self.assertEqual(data["width"], 900)
            self.assertEqual(data["stages"][0]["left"], 0.0)
            self.assertEqual(data["stages"][0]["width"], 100.0)


if __name__ == "__main__":
    unittest.main()
