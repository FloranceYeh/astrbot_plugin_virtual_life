from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time
from typing import Any, Literal

State = Literal["sleep", "busy", "focus", "transit", "available", "social"]
Availability = Literal["blocked", "low", "normal", "high"]
Audience = Literal["private", "group", "both"]

STATES = {"sleep", "busy", "focus", "transit", "available", "social"}
AVAILABILITIES = {"blocked", "low", "normal", "high"}
AUDIENCES = {"private", "group", "both"}


def parse_hhmm(value: str, *, allow_2400: bool = False) -> time:
    if allow_2400 and value == "24:00":
        return time.max
    parsed = datetime.strptime(value, "%H:%M").time()
    if parsed.strftime("%H:%M") != value:
        raise ValueError(f"invalid HH:MM value: {value}")
    return parsed


def minute_of_day(value: str) -> int:
    if value == "24:00":
        return 1440
    parsed = parse_hhmm(value)
    return parsed.hour * 60 + parsed.minute


@dataclass(slots=True, frozen=True)
class TimelineItem:
    id: str
    start: str
    end: str
    activity: str
    location: str = ""
    state: State = "available"
    availability: Availability = "normal"

    def __post_init__(self) -> None:
        if not self.id or not self.activity:
            raise ValueError("timeline id and activity are required")
        if self.state not in STATES:
            raise ValueError(f"invalid state: {self.state}")
        if self.availability not in AVAILABILITIES:
            raise ValueError(f"invalid availability: {self.availability}")
        if minute_of_day(self.start) >= minute_of_day(self.end):
            raise ValueError("timeline start must be earlier than end")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TimelineItem:
        return cls(
            id=str(value.get("id", "")).strip(),
            start=str(value.get("start", "")).strip(),
            end=str(value.get("end", "")).strip(),
            activity=str(value.get("activity", "")).strip(),
            location=str(value.get("location", "")).strip(),
            state=str(value.get("state", "available")),
            availability=str(value.get("availability", "normal")),
        )


@dataclass(slots=True, frozen=True)
class ProactiveWindow:
    id: str
    at: str
    intent: str
    audience: Audience = "both"
    source_item_id: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.intent:
            raise ValueError("window id and intent are required")
        parse_hhmm(self.at)
        if self.audience not in AUDIENCES:
            raise ValueError(f"invalid audience: {self.audience}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProactiveWindow:
        return cls(
            id=str(value.get("id", "")).strip(),
            at=str(value.get("at", "")).strip(),
            intent=str(value.get("intent", "")).strip(),
            audience=str(value.get("audience", "both")),
            source_item_id=str(value.get("source_item_id", "")).strip(),
        )


@dataclass(slots=True, frozen=True)
class DailyPlan:
    date: str
    persona_id: str
    theme: str
    mood: str
    outfit: str
    timeline: tuple[TimelineItem, ...]
    proactive_windows: tuple[ProactiveWindow, ...] = ()
    private_bonus: int = 0
    group_bonus: int = 0
    status: str = "ok"
    revision: str = ""

    def __post_init__(self) -> None:
        date.fromisoformat(self.date)
        if not self.persona_id:
            raise ValueError("persona_id is required")
        previous_end = 0
        ids: set[str] = set()
        for item in self.timeline:
            start = minute_of_day(item.start)
            if ids and start != previous_end:
                raise ValueError("timeline items must be continuous and non-overlapping")
            if start < previous_end:
                raise ValueError("timeline items overlap or are unsorted")
            previous_end = minute_of_day(item.end)
            if item.id in ids:
                raise ValueError("timeline ids must be unique")
            ids.add(item.id)
        if self.status == "ok" and self.timeline:
            if self.timeline[0].start != "00:00" or self.timeline[-1].end != "24:00":
                raise ValueError("timeline must cover 00:00 through 24:00")
            window_ids: set[str] = set()
            for window in self.proactive_windows:
                if window.id in window_ids:
                    raise ValueError("window ids must be unique")
                window_ids.add(window.id)
                if window.source_item_id and window.source_item_id not in ids:
                    raise ValueError("window references unknown timeline item")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DailyPlan:
        bonus = value.get("budget_bonus") or {}
        return cls(
            date=str(value["date"]),
            persona_id=str(value["persona_id"]),
            theme=str(value.get("theme", "日常")),
            mood=str(value.get("mood", "平静")),
            outfit=str(value.get("outfit", "日常穿搭")),
            timeline=tuple(TimelineItem.from_dict(item) for item in value.get("timeline", [])),
            proactive_windows=tuple(ProactiveWindow.from_dict(item) for item in value.get("proactive_windows", [])),
            private_bonus=int(bonus.get("private", value.get("private_bonus", 0))),
            group_bonus=int(bonus.get("group", value.get("group_bonus", 0))),
            status=str(value.get("status", "ok")),
            revision=str(value.get("revision", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["budget_bonus"] = {"private": value.pop("private_bonus"), "group": value.pop("group_bonus")}
        return value


@dataclass(slots=True)
class SessionState:
    date: str
    persona_id: str = "default"
    daily_budget: int = 0
    sent_count: int = 0
    unanswered_count: int = 0
    last_user_message_at: str = ""
    last_proactive_at: str = ""
    sleep_drawn: bool = False
    sleep_selected: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionState:
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value[key] for key in allowed if key in value})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FollowupTask:
    id: str
    umo: str
    persona_id: str
    scheduled_at: str
    intent: str
    created_at: str
    status: str = "pending"
    last_error: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FollowupTask:
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
