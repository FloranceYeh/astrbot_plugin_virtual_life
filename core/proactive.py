from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import DailyPlan, SessionState, TimelineItem
from .utils import deterministic_int, deterministic_probability, parse_datetime


@dataclass(slots=True, frozen=True)
class SendDecision:
    allowed: bool
    reason: str


def session_kind(umo: str) -> str:
    return "group" if "groupmessage" in umo.lower() else "friend"


class ProactivePolicy:
    def __init__(self, config, storage, timezone):
        self.config = config
        self.storage = storage
        self.timezone = timezone

    def settings_for(self, umo: str) -> dict:
        key = "group_settings" if session_kind(umo) == "group" else "friend_settings"
        return self.config.get(key, {}) or {}

    def enabled_sessions(self) -> list[str]:
        sessions: list[str] = []
        for key in ("friend_settings", "group_settings"):
            settings = self.config.get(key, {}) or {}
            if not settings.get("enable", False):
                continue
            sessions.extend(str(value).strip() for value in settings.get("session_list", []) if str(value).strip())
        return list(dict.fromkeys(sessions))

    def is_enabled(self, umo: str) -> bool:
        settings = self.settings_for(umo)
        return bool(settings.get("enable", False) and umo in settings.get("session_list", []))

    def subscribe(self, umo: str) -> bool:
        settings = self.settings_for(umo)
        settings["enable"] = True
        sessions = settings.get("session_list")
        if not isinstance(sessions, list):
            sessions = []
            settings["session_list"] = sessions
        if umo in sessions:
            return False
        sessions.append(umo)
        return True

    def ensure_state(self, umo: str, persona_id: str, plan: DailyPlan, now: datetime) -> SessionState:
        date_str = now.date().isoformat()
        state = self.storage.sessions.get(umo)
        if not state or state.date != date_str:
            settings = self.settings_for(umo)
            minimum = max(0, int(settings.get("daily_budget_min", 0)))
            maximum = max(minimum, int(settings.get("daily_budget_max", minimum)))
            base = deterministic_int(f"budget::{date_str}::{umo}", minimum, maximum)
            bonus_raw = plan.group_bonus if session_kind(umo) == "group" else plan.private_bonus
            bonus = max(0, min(int(settings.get("llm_bonus_max", 0)), bonus_raw))
            hard_max = max(0, int(settings.get("daily_hard_max", base + bonus)))
            state = SessionState(
                date=date_str,
                persona_id=persona_id,
                daily_budget=min(hard_max, base + bonus),
                last_user_message_at=now.isoformat(),
            )
            self.storage.sessions[umo] = state
        else:
            state.persona_id = persona_id
        return state

    def record_incoming(self, umo: str, now: datetime) -> None:
        state = self.storage.sessions.get(umo)
        if not state:
            state = SessionState(date=now.date().isoformat(), last_user_message_at=now.isoformat())
            self.storage.sessions[umo] = state
        state.last_user_message_at = now.isoformat()
        state.unanswered_count = 0

    def evaluate(
        self,
        *,
        umo: str,
        state: SessionState,
        current_item: TimelineItem | None,
        now: datetime,
        trigger: str,
        attempt_key: str = "",
    ) -> SendDecision:
        if not self.is_enabled(umo):
            return SendDecision(False, "session disabled or not whitelisted")
        delivery = self.config.get("delivery_settings", {}) or {}
        max_unanswered = max(0, int(delivery.get("max_unanswered", 3)))
        if max_unanswered and state.unanswered_count >= max_unanswered:
            return SendDecision(False, "maximum unanswered count reached")
        if state.sent_count >= state.daily_budget:
            return SendDecision(False, "daily budget exhausted")

        settings = self.settings_for(umo)
        if state.last_proactive_at:
            elapsed = (now - parse_datetime(state.last_proactive_at, self.timezone)).total_seconds() / 60
            if elapsed < max(0, int(settings.get("cooldown_minutes", 0))):
                return SendDecision(False, "cooldown active")

        if trigger == "window" and state.last_user_message_at:
            idle = (now - parse_datetime(state.last_user_message_at, self.timezone)).total_seconds() / 60
            if idle < max(0, int(delivery.get("minimum_idle_for_window_minutes", 20))):
                return SendDecision(False, "conversation is not idle enough")

        if trigger != "sleep":
            if current_item and current_item.state == "sleep":
                return SendDecision(False, "sleeping")
            probability = self.availability_probability(current_item)
            probability_key = attempt_key or (current_item.id if current_item else "none")
            if deterministic_probability(f"availability::{state.date}::{umo}::{probability_key}") >= probability:
                return SendDecision(False, "availability probability rejected")
        return SendDecision(True, "allowed")

    def availability_probability(self, current_item: TimelineItem | None) -> float:
        if current_item is None:
            return 1.0
        delivery = self.config.get("delivery_settings", {}) or {}
        configured = delivery.get("availability_probabilities", {}) or {}
        raw = configured.get(current_item.availability)
        if raw is None:
            return 1.0
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return 1.0

    def record_delivery(self, state: SessionState, now: datetime) -> None:
        state.sent_count += 1
        state.unanswered_count += 1
        state.last_proactive_at = now.isoformat()
