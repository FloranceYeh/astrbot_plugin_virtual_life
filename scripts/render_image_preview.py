from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from jinja2 import Environment
    from playwright.async_api import Browser, async_playwright
except ModuleNotFoundError as exc:
    raise SystemExit("缺少预览依赖。请先执行: pip install jinja2 playwright") from exc

from core.image_renderer import ScheduleImageRenderer
from core.models import DailyPlan


PERSONA_ID = "preview-persona"
PREVIEW_NOW = datetime(2026, 10, 1, 14, 30)
VIEW_NAMES = ("timeline", "outfit", "stage-list", "stage-detail")


class PlaywrightHtmlRenderer:
    def __init__(self, temporary_dir: Path, browser_path: Path | None = None) -> None:
        self.temporary_dir = temporary_dir
        self.browser_path = browser_path
        self.playwright = None
        self.browser: Browser | None = None

    async def __aenter__(self) -> PlaywrightHtmlRenderer:
        self.playwright = await async_playwright().start()
        launch_options: dict[str, Any] = {"headless": True}
        if self.browser_path:
            launch_options["executable_path"] = str(self.browser_path)
        self.browser = await self.playwright.chromium.launch(**launch_options)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def __call__(
        self,
        template: str,
        data: dict[str, Any],
        return_url: bool = False,
        options: dict[str, Any] | None = None,
    ) -> str:
        if not self.browser:
            raise RuntimeError("Playwright browser is not running")
        html = Environment(autoescape=True).from_string(template).render(**data)
        context = await self.browser.new_context(
            viewport={"width": int(data.get("width", 1200)), "height": 900},
            device_scale_factor=1,
        )
        path = self.temporary_dir / f"screenshot-{uuid.uuid4().hex}.png"
        try:
            page = await context.new_page()
            await page.set_content(html, wait_until="load")
            await page.screenshot(
                path=str(path),
                full_page=bool((options or {}).get("full_page", True)),
                type="png",
            )
        finally:
            await context.close()
        return str(path)


def daily_plan() -> DailyPlan:
    return DailyPlan.from_dict(
        {
            "date": "2026-10-01",
            "persona_id": PERSONA_ID,
            "theme": "秋日探索日",
            "mood": "轻松愉快",
            "outfit": {
                "style": "复古文艺风",
                "summary": "适合秋日城市漫步的暖色层次造型",
                "items": [
                    {
                        "category": "hairstyle",
                        "name": "低马尾",
                        "details": "自然蓬松，搭配棕色发带",
                    },
                    {
                        "category": "underwear",
                        "name": "无痕打底套装",
                        "details": "柔软透气的浅杏色面料",
                    },
                    {
                        "category": "underpants",
                        "name": "浅杏色无痕内裤",
                        "details": "棉质中腰，贴合舒适",
                    },
                    {
                        "category": "top",
                        "name": "米白针织衫",
                        "details": "细针织圆领，袖口微收",
                    },
                    {
                        "category": "bottom",
                        "name": "焦糖色半身裙",
                        "details": "高腰过膝 A 字剪裁",
                    },
                    {
                        "category": "legwear",
                        "name": "深棕连裤袜",
                        "details": "轻薄哑光质感",
                    },
                    {
                        "category": "outerwear",
                        "name": "格纹短风衣",
                        "details": "适合早晚温差",
                    },
                    {
                        "category": "shoes",
                        "name": "棕色乐福鞋",
                        "details": "低跟软底，适合步行",
                    },
                    {
                        "category": "bag",
                        "name": "复古邮差包",
                        "details": "可放相机和随身物品",
                    },
                    {
                        "category": "accessory",
                        "name": "黄铜叶片胸针",
                        "details": "点缀风衣领口",
                    },
                ],
            },
            "timeline": [
                {
                    "id": "sleep",
                    "start": "00:00",
                    "end": "08:00",
                    "activity": "睡眠",
                    "location": "家",
                    "state": "sleep",
                    "availability": "blocked",
                },
                {
                    "id": "breakfast",
                    "start": "08:00",
                    "end": "09:30",
                    "activity": "早餐与整理",
                    "location": "家",
                    "state": "available",
                    "availability": "normal",
                },
                {
                    "id": "museum",
                    "start": "09:30",
                    "end": "12:30",
                    "activity": "参观秋季艺术展",
                    "location": "市美术馆",
                    "state": "focus",
                    "availability": "low",
                },
                {
                    "id": "lunch",
                    "start": "12:30",
                    "end": "14:00",
                    "activity": "午餐与休息",
                    "location": "河畔餐厅",
                    "state": "social",
                    "availability": "normal",
                },
                {
                    "id": "walk",
                    "start": "14:00",
                    "end": "17:30",
                    "activity": "城市漫步与拍照",
                    "location": "老城区",
                    "state": "available",
                    "availability": "high",
                },
                {
                    "id": "dinner",
                    "start": "17:30",
                    "end": "19:30",
                    "activity": "朋友聚餐",
                    "location": "创意园区",
                    "state": "social",
                    "availability": "low",
                },
                {
                    "id": "reading",
                    "start": "19:30",
                    "end": "22:30",
                    "activity": "整理照片与阅读",
                    "location": "家",
                    "state": "focus",
                    "availability": "normal",
                },
                {
                    "id": "night",
                    "start": "22:30",
                    "end": "24:00",
                    "activity": "洗漱与入睡",
                    "location": "家",
                    "state": "sleep",
                    "availability": "blocked",
                },
            ],
            "proactive_windows": [
                {
                    "id": "share-art",
                    "at": "11:30",
                    "intent": "分享展览中喜欢的作品",
                    "audience": "both",
                    "source_item_id": "museum",
                },
                {
                    "id": "share-photo",
                    "at": "16:00",
                    "intent": "分享老城区的秋日照片",
                    "audience": "private",
                    "source_item_id": "walk",
                },
            ],
            "budget_bonus": {"private": 1, "group": 0},
        }
    )


def autumn_stage() -> dict[str, Any]:
    return {
        "id": "semester-autumn",
        "name": "秋季学期",
        "kind": "academic",
        "start_date": "2026-09-01",
        "end_date": "2027-01-20",
        "priority": 70,
        "summary": "完成秋季课程，同时保持摄影与阅读习惯。",
        "weekly_rules": [
            {
                "weekdays": [1, 3],
                "start": "09:00",
                "end": "11:30",
                "title": "专业课程",
                "location": "教学楼",
                "participants": ["同学"],
                "required": True,
            },
            {
                "weekdays": [5],
                "start": "15:00",
                "end": "17:00",
                "title": "摄影社活动",
                "location": "社团活动室",
                "participants": ["社员"],
                "required": True,
            },
        ],
        "special_dates": [
            {
                "date": "2026-10-01",
                "start": "09:30",
                "end": "12:30",
                "title": "国庆艺术展",
                "location": "市美术馆",
                "participants": ["朋友"],
                "required": True,
            }
        ],
        "special_periods": [
            {
                "name": "国庆假期",
                "start_date": "2026-10-01",
                "end_date": "2026-10-07",
                "constraints": ["暂停常规课程", "安排短途活动"],
            },
            {
                "name": "期末复习周",
                "start_date": "2027-01-10",
                "end_date": "2027-01-20",
                "constraints": ["减少娱乐", "优先复习"],
            },
        ],
        "milestones": [
            {"date": "2026-12-20", "title": "提交课程项目", "lead_days": 14}
        ],
        "constraints": ["工作日保持规律作息", "每周保留一次户外活动"],
        "holidays": [
            {"date": "2026-10-01", "name": "国庆节", "kind": "public"},
            {"date": "2026-09-25", "name": "中秋节", "kind": "traditional"},
        ],
    }


def winter_stage() -> dict[str, Any]:
    return {
        "id": "winter-break",
        "name": "寒假",
        "kind": "academic",
        "start_date": "2027-01-21",
        "end_date": "2027-02-20",
        "priority": 60,
        "summary": "休息、旅行并整理上一学期的学习成果。",
        "weekly_rules": [],
        "special_dates": [],
        "special_periods": [],
        "milestones": [],
        "constraints": ["保持基本作息"],
        "holidays": [],
    }


def long_term_day() -> dict[str, Any]:
    stage = autumn_stage()
    return {
        "stage": stage,
        "active_periods": [stage["special_periods"][0]],
        "holidays": [{"date": "2026-10-01", "name": "国庆节", "kind": "public"}],
    }


def detect_browser(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"浏览器不存在: {path}")
        return path
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    return next(
        (Path(value) for value in candidates if value and Path(value).exists()), None
    )


async def render_previews(args: argparse.Namespace) -> list[Path]:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = (
        list(VIEW_NAMES) if "all" in args.view else list(dict.fromkeys(args.view))
    )
    browser_path = detect_browser(args.browser)
    settings = {
        "image_render_enabled": True,
        "image_theme": args.theme,
        "image_width": args.width,
        "image_font": args.font,
    }
    plan = daily_plan()
    stages = [autumn_stage(), winter_stage()]
    outputs = []
    with tempfile.TemporaryDirectory(prefix="virtual-life-preview-") as directory:
        temporary_dir = Path(directory)
        async with PlaywrightHtmlRenderer(temporary_dir, browser_path) as backend:
            renderer = ScheduleImageRenderer(temporary_dir, backend, settings)
            for view in selected:
                if view == "timeline":
                    generated = await renderer.render_timeline(
                        plan, PREVIEW_NOW, long_term_day()
                    )
                elif view == "outfit":
                    generated = await renderer.render_outfit(plan, PREVIEW_NOW)
                elif view == "stage-list":
                    generated = await renderer.render_stage_list(stages, PERSONA_ID)
                else:
                    generated = await renderer.render_stage(stages[0], PERSONA_ID)
                target = output_dir / f"{view}.png"
                shutil.copyfile(generated, target)
                outputs.append(target)
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用内置示例数据渲染虚拟人生 PNG 预览图。"
    )
    parser.add_argument(
        "--view",
        nargs="+",
        required=True,
        choices=(*VIEW_NAMES, "all"),
        help="指定要生成的视图，可一次传入多个值。",
    )
    parser.add_argument("--output-dir", default="preview_output", help="PNG 输出目录。")
    parser.add_argument(
        "--theme", choices=("dark", "light"), default="dark", help="图片主题。"
    )
    parser.add_argument(
        "--width", type=int, default=1200, help="图片宽度，渲染器会限制在 720-2000。"
    )
    parser.add_argument("--font", default="", help="可选的系统字体名称。")
    parser.add_argument(
        "--browser", help="可选的 Chrome、Edge 或 Chromium 可执行文件路径。"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        outputs = asyncio.run(render_previews(args))
    except Exception as exc:
        raise SystemExit(f"预览图片生成失败: {exc}") from exc
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
