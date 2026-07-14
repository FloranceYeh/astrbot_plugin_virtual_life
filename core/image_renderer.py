from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import OUTFIT_CATEGORY_LABELS, DailyPlan, minute_of_day

RenderHtml = Callable[..., Awaitable[str]]

STATE_LABELS = {
    "sleep": "睡眠",
    "busy": "忙碌",
    "focus": "专注",
    "transit": "通勤",
    "available": "空闲",
    "social": "社交",
}
AVAILABILITY_LABELS = {
    "blocked": "不便联系",
    "low": "较少联系",
    "normal": "正常联系",
    "high": "适合联系",
}
KIND_LABELS = {"academic": "校历", "project": "工期", "custom": "自定义"}
SOURCE_LABELS = {
    "natural": "自然语言生成",
    "json": "结构化数据导入",
    "revision": "按修改意见生成",
    "regenerate": "完整重生成",
    "auto_renewal": "自动续期",
}
WEEKDAY_LABELS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
OUTFIT_CATEGORY_ICONS = {
    "hairstyle": "✦",
    "headwear": "♕",
    "underwear": "◈",
    "top": "▱",
    "bottom": "▽",
    "dress": "♢",
    "legwear": "║",
    "outerwear": "◇",
    "shoes": "◒",
    "accessory": "✧",
    "bag": "▣",
    "makeup": "✿",
    "fragrance": "❋",
    "other": "◆",
}


class ScheduleImageRenderer:
    def __init__(
        self,
        data_dir: Path,
        render_html: RenderHtml,
        settings: dict[str, Any] | None = None,
        *,
        template_path: Path | None = None,
    ) -> None:
        self.render_html = render_html
        self.settings = settings or {}
        self.cache_dir = data_dir / "image_cache"
        self.template_path = template_path or Path(__file__).resolve().parent.parent / "templates" / "schedule.html"
        self.template = self.template_path.read_text(encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("image_render_enabled", True))

    async def cleanup(self, *, max_age_days: int = 7) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - max_age_days * 86400
        for path in self.cache_dir.glob("*.png"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def invalidate_persona(self, persona_id: str) -> None:
        prefix = self._persona_key(persona_id)
        for path in self.cache_dir.glob(f"{prefix}-*.png"):
            try:
                path.unlink()
            except OSError:
                continue

    async def render_daily(self, plan: DailyPlan, now: datetime) -> str:
        items = []
        current_minute = now.hour * 60 + now.minute
        same_day = plan.date == now.date().isoformat()
        for item in plan.timeline:
            start_minute = minute_of_day(item.start)
            end_minute = minute_of_day(item.end)
            items.append(
                {
                    "start": item.start,
                    "end": item.end,
                    "activity": item.activity,
                    "location": item.location,
                    "state": item.state,
                    "state_label": STATE_LABELS.get(item.state, item.state),
                    "availability_label": AVAILABILITY_LABELS.get(item.availability, item.availability),
                    "duration": end_minute - start_minute,
                    "current": same_day and start_minute <= current_minute < end_minute,
                }
            )
        windows = [
            {"at": window.at, "intent": window.intent, "audience": window.audience}
            for window in plan.proactive_windows
        ]
        outfit_items = [
            {
                "category": item.category,
                "label": OUTFIT_CATEGORY_LABELS[item.category],
                "icon": OUTFIT_CATEGORY_ICONS[item.category],
                "name": item.name,
                "details": item.details,
            }
            for item in plan.outfit.items
        ]
        data = self._base_data(
            "daily",
            plan.persona_id,
            {
                "title": f"{plan.date} 虚拟日程",
                "subtitle": plan.persona_id,
                "theme_text": plan.theme,
                "mood": plan.mood,
                "outfit_summary": plan.outfit.summary,
                "outfit_items": outfit_items,
                "items": items,
                "windows": windows,
                "generated_at": now.strftime("%Y-%m-%d %H:%M"),
            },
        )
        return await self._render(data, persona_id=plan.persona_id, view="daily", cache=False)

    async def render_stage_list(self, stages: list[dict[str, Any]], persona_id: str) -> str:
        first = min(date.fromisoformat(stage["start_date"]) for stage in stages)
        last = max(date.fromisoformat(stage["end_date"]) for stage in stages)
        total_days = max(1, (last - first).days + 1)
        rows = []
        for stage in stages:
            start = date.fromisoformat(stage["start_date"])
            end = date.fromisoformat(stage["end_date"])
            rows.append(
                {
                    "id": stage["id"],
                    "name": stage["name"],
                    "kind": stage["kind"],
                    "kind_label": KIND_LABELS.get(stage["kind"], stage["kind"]),
                    "start_date": stage["start_date"],
                    "end_date": stage["end_date"],
                    "priority": stage["priority"],
                    "summary": stage.get("summary", ""),
                    "left": round((start - first).days / total_days * 100, 3),
                    "width": round(((end - start).days + 1) / total_days * 100, 3),
                }
            )
        payload = {
            "title": "已批准大时间表",
            "subtitle": f"{persona_id} · 共 {len(rows)} 个阶段",
            "range": f"{first.isoformat()} 至 {last.isoformat()}",
            "stages": rows,
        }
        data = self._base_data("stage_list", persona_id, payload)
        return await self._render(data, persona_id=persona_id, view="list", cache=True)

    async def render_stage(
        self,
        stage: dict[str, Any],
        persona_id: str,
        *,
        status: str = "approved",
        draft_metadata: dict[str, Any] | None = None,
    ) -> str:
        weekly_rules = []
        for item in stage.get("weekly_rules", []):
            weekly_rules.append(
                {
                    **item,
                    "weekday_text": "、".join(WEEKDAY_LABELS.get(day, str(day)) for day in item.get("weekdays", [])),
                    "participants_text": "、".join(item.get("participants", [])),
                }
            )
        special_dates = [
            {**item, "participants_text": "、".join(item.get("participants", []))}
            for item in stage.get("special_dates", [])
        ]
        metadata = None
        if draft_metadata:
            metadata = {
                "source": SOURCE_LABELS.get(str(draft_metadata.get("source", "")), str(draft_metadata.get("source", ""))),
                "created_at": str(draft_metadata.get("created_at", "")),
                "requirements": str(draft_metadata.get("requirements", "")),
            }
        payload = {
            "title": stage["name"],
            "subtitle": f"{persona_id} · {KIND_LABELS.get(stage['kind'], stage['kind'])}",
            "status": status,
            "status_label": "待批准草稿" if status == "draft" else "已批准",
            "stage": {
                **stage,
                "kind_label": KIND_LABELS.get(stage["kind"], stage["kind"]),
                "weekly_rules": weekly_rules,
                "special_dates": special_dates,
            },
            "draft_metadata": metadata,
        }
        data = self._base_data("stage", persona_id, payload)
        return await self._render(data, persona_id=persona_id, view=f"stage-{status}", cache=True)

    def _base_data(self, view: str, persona_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        theme = str(self.settings.get("image_theme", "dark")).strip().lower()
        if theme not in {"dark", "light"}:
            theme = "dark"
        try:
            width = int(self.settings.get("image_width", 1200))
        except (TypeError, ValueError):
            width = 1200
        font = str(self.settings.get("image_font", "")).strip()
        font = font.translate({ord(char): None for char in ";{}"})
        font_stack = f'"{font}", ' if font else ""
        return {
            "view": view,
            "persona_id": persona_id,
            "theme": theme,
            "width": min(2000, max(720, width)),
            "font_stack": font_stack + '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif',
            **payload,
        }

    async def _render(self, data: dict[str, Any], *, persona_id: str, view: str, cache: bool) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        prefix = self._persona_key(persona_id)
        filename = f"{prefix}-{view}-{digest}.png" if cache else f"{prefix}-{view}-{uuid.uuid4().hex}.png"
        target = self.cache_dir / filename
        if cache and target.exists():
            return str(target)
        rendered = Path(
            await self.render_html(
                self.template,
                data,
                return_url=False,
                options={"full_page": True, "type": "png"},
            )
        )
        if not rendered.exists():
            raise RuntimeError(f"rendered image does not exist: {rendered}")
        shutil.copyfile(rendered, target)
        try:
            if rendered.resolve() != target.resolve():
                rendered.unlink()
        except OSError:
            pass
        return str(target)

    @staticmethod
    def _persona_key(persona_id: str) -> str:
        return hashlib.sha1(persona_id.encode("utf-8")).hexdigest()[:12]
