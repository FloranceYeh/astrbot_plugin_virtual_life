from __future__ import annotations

from .models import DailyPlan, ProactiveWindow, minute_of_day
from .utils import deterministic_int


def proactive_window_offset_minutes(
    plan: DailyPlan,
    window: ProactiveWindow,
    jitter_minutes: int,
) -> int:
    jitter = max(0, min(60, int(jitter_minutes)))
    if jitter == 0:
        return 0
    source = next((item for item in plan.timeline if item.id == window.source_item_id), None)
    if source is None:
        return 0
    planned = minute_of_day(window.at)
    minimum = max(-jitter, minute_of_day(source.start) - planned)
    maximum = min(jitter, minute_of_day(source.end) - planned - 1)
    if minimum > maximum:
        return 0
    return deterministic_int(
        f"proactive-window::{plan.persona_id}::{plan.date}::{window.id}",
        minimum,
        maximum,
    )
