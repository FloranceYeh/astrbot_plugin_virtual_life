from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from data.plugins.astrbot_plugin_virtual_life.core.models import DailyPlan
from data.plugins.astrbot_plugin_virtual_life.main import ProactiveVirtualDailyPlugin
from tests.fixtures import outfit_payload


def make_plugin(*, enabled: bool, network_plugin=None):
    plugin = object.__new__(ProactiveVirtualDailyPlugin)
    plugin.config = {
        "personal_network_integration": {
            "enable": enabled,
            "max_context_chars": 2500,
            "record_completed_events": True,
        }
    }
    plugin.context = MagicMock()
    plugin.context.get_registered_star.return_value = (
        SimpleNamespace(activated=True, star_cls=network_plugin)
        if network_plugin
        else None
    )
    plugin._personal_network_unavailable_logged = False
    plugin.timezone = ZoneInfo("Asia/Shanghai")
    plugin.storage = SimpleNamespace(plans={})
    return plugin


def plan_with_people() -> DailyPlan:
    return DailyPlan.from_dict(
        {
            "date": "2026-07-28",
            "persona_id": "alice",
            "theme": "social",
            "mood": "warm",
            "outfit": outfit_payload(),
            "timeline": [
                {
                    "id": "lunch",
                    "start": "00:00",
                    "end": "12:00",
                    "activity": "Lunch with Lin",
                    "location": "Cafe",
                    "state": "social",
                    "availability": "normal",
                    "participant_ids": ["person-1"],
                },
                {
                    "id": "dinner",
                    "start": "12:00",
                    "end": "24:00",
                    "activity": "Dinner with Lin",
                    "location": "Home",
                    "state": "social",
                    "availability": "normal",
                    "participant_ids": ["person-1"],
                },
            ],
            "proactive_windows": [],
            "budget_bonus": {"private": 0, "group": 0},
        }
    )


def test_integration_is_inert_by_default():
    plugin = make_plugin(enabled=False)

    assert plugin._personal_network_plugin() is None
    plugin.context.get_registered_star.assert_not_called()


def test_context_uses_loaded_plugin_and_configured_limit():
    network = SimpleNamespace(
        get_context_for_plugin=AsyncMock(return_value="network context"),
        record_life_event_from_plugin=AsyncMock(),
    )
    plugin = make_plugin(enabled=True, network_plugin=network)

    result = asyncio.run(plugin._personal_network_context("alice"))

    assert result == "network context"
    network.get_context_for_plugin.assert_awaited_once_with(
        "alice", max_chars=2500
    )


def test_only_completed_timeline_items_are_recorded():
    network = SimpleNamespace(
        get_context_for_plugin=AsyncMock(return_value=""),
        record_life_event_from_plugin=AsyncMock(return_value={"created": True}),
    )
    plugin = make_plugin(enabled=True, network_plugin=network)
    plan = plan_with_people()
    plugin.storage.plans = {"2026-07-28::alice": plan}

    asyncio.run(
        plugin._record_completed_personal_network_events(
            "alice", datetime(2026, 7, 28, 13, 0, tzinfo=plugin.timezone)
        )
    )

    network.record_life_event_from_plugin.assert_awaited_once()
    call = network.record_life_event_from_plugin.await_args
    assert call.args == ("alice",)
    assert call.kwargs["participant_ids"] == ["person-1"]
    assert call.kwargs["source_key"] == "2026-07-28:lunch"
    assert call.kwargs["source"] == "astrbot_plugin_virtual_life"
