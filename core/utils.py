from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import OUTFIT_CATEGORY_LABELS, DailyPlan, TimelineItem, minute_of_day


def extract_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text).strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("LLM response does not contain a JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(cleaned[start : index + 1])
                if isinstance(value, dict):
                    return value
    raise ValueError("unable to parse JSON object")


def deterministic_int(seed: str, minimum: int, maximum: int) -> int:
    if maximum <= minimum:
        return minimum
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return minimum + int.from_bytes(digest[:8], "big") % (maximum - minimum + 1)


def deterministic_probability(seed: str) -> float:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def now_in(timezone: ZoneInfo) -> datetime:
    return datetime.now(timezone)


def parse_datetime(value: str, timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def timeline_item_at(plan: DailyPlan, moment: datetime) -> TimelineItem | None:
    minute = moment.hour * 60 + moment.minute
    for item in plan.timeline:
        if minute_of_day(item.start) <= minute < minute_of_day(item.end):
            return item
    return None


def next_available_at(plan: DailyPlan, moment: datetime) -> datetime | None:
    minute = moment.hour * 60 + moment.minute
    for item in plan.timeline:
        if minute_of_day(item.start) <= minute:
            continue
        if item.state != "sleep" and item.availability in {"normal", "high"}:
            hour, minute_value = map(int, item.start.split(":"))
            return moment.replace(hour=hour, minute=minute_value, second=0, microsecond=0)
    return None


def format_plan(plan: DailyPlan, moment: datetime | None = None) -> str:
    current = timeline_item_at(plan, moment) if moment else None
    outfit_items = "；".join(
        f"{OUTFIT_CATEGORY_LABELS[item.category]}：{item.name}"
        for item in plan.outfit.items
    )
    lines = [
        f"📅 {plan.date} · {plan.theme}",
        f"💭 心情：{plan.mood}",
        f"👗 穿搭：{plan.outfit.summary}" + (f"（{outfit_items}）" if outfit_items else ""),
    ]
    if current:
        lines.append(f"📍 当前：{current.activity}" + (f"（{current.location}）" if current.location else ""))
    lines.append("📝 日程：")
    lines.extend(f"- {item.start}-{item.end} {item.activity}" + (f" @ {item.location}" if item.location else "") for item in plan.timeline)
    return "\n".join(lines)


def format_timeline(
    plan: DailyPlan,
    moment: datetime | None = None,
    long_term_day: dict[str, object] | None = None,
) -> str:
    current = timeline_item_at(plan, moment) if moment else None
    stage = long_term_day.get("stage") if long_term_day else None
    active_periods = long_term_day.get("active_periods", []) if long_term_day else []
    holidays = long_term_day.get("holidays", []) if long_term_day else []
    lines = [
        f"📅 {plan.date} 虚拟日程",
        f"💡 今日主题：{plan.theme}",
        f"💭 心情状态：{plan.mood}",
    ]
    if isinstance(stage, dict):
        lines.append(
            f"🗓️ 当前大时间段：{stage['name']}（{stage['start_date']} 至 {stage['end_date']}）"
        )
    else:
        lines.append("🗓️ 当前大时间段：暂无当前阶段")
    for period in active_periods:
        constraints = "；".join(period.get("constraints", []))
        lines.append(
            f"⚠️ 特殊时间段：{period['name']}（{period['start_date']} 至 {period['end_date']}）"
            + (f" · {constraints}" if constraints else "")
        )
    if holidays:
        lines.append("🎉 今日节日：" + "、".join(item["name"] for item in holidays))
    if current:
        location = f" @ {current.location}" if current.location else ""
        lines.append(f"📍 当前时段：{current.start}-{current.end} {current.activity}{location}")
    lines.append("📝 24 小时时间轴：")
    lines.extend(
        f"- {item.start}-{item.end} {item.activity}" + (f" @ {item.location}" if item.location else "")
        for item in plan.timeline
    )
    return "\n".join(lines)


def format_outfit(plan: DailyPlan) -> str:
    outfit_items = "；".join(
        f"{OUTFIT_CATEGORY_LABELS[item.category]}：{item.name}"
        for item in plan.outfit.items
    )
    lines = [
        f"👗 {plan.date} 今日穿搭",
        f"🎨 穿搭风格：{plan.outfit.style}",
        f"💡 今日主题：{plan.theme}",
        f"💭 今日心情：{plan.mood}",
        f"✨ 造型概述：{plan.outfit.summary}",
    ]
    if outfit_items:
        lines.append(f"🧥 穿搭明细：{outfit_items}")
    return "\n".join(lines)


def prune_date_keys(values: dict[str, object], keep_days: int, today: date) -> dict[str, object]:
    threshold = today - timedelta(days=max(1, keep_days))
    result = {}
    for key, value in values.items():
        date_part = key.split("::", 1)[0]
        try:
            if date.fromisoformat(date_part) >= threshold:
                result[key] = value
        except ValueError:
            result[key] = value
    return result
