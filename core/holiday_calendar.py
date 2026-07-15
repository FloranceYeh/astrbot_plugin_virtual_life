from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import holidays

try:
    from lunar_python import Solar
except ImportError:  # pragma: no cover - requirements installation is managed by AstrBot
    Solar = None

TRADITIONAL_FESTIVALS = {
    "除夕",
    "春节",
    "元宵节",
    "端午节",
    "七夕节",
    "中秋节",
    "重阳节",
    "腊八节",
}
PUBLIC_NAME_ALIASES = {"农历除夕": "除夕"}


@lru_cache(maxsize=16)
def _public_holidays(year: int) -> dict[date, str]:
    try:
        return dict(holidays.country_holidays("CN", years=[year], language="zh_CN"))
    except Exception:
        return {}


class ChinaHolidayCalendar:
    def on(self, target: date) -> list[dict[str, Any]]:
        names: list[tuple[str, str]] = []
        public_name = _public_holidays(target.year).get(target, "")
        for value in str(public_name).split("; "):
            name = PUBLIC_NAME_ALIASES.get(value.strip(), value.strip())
            if name and not name.startswith("休息日"):
                names.append((name, "public"))

        if Solar is not None:
            try:
                lunar = Solar.fromYmd(target.year, target.month, target.day).getLunar()
                names.extend(
                    (name, "traditional")
                    for name in lunar.getFestivals()
                    if name in TRADITIONAL_FESTIVALS
                )
            except Exception:
                pass

        result = []
        seen = set()
        for name, kind in names:
            if name in seen:
                continue
            seen.add(name)
            result.append({"date": target.isoformat(), "name": name, "kind": kind})
        return result

    def between(self, start: date, end: date) -> list[dict[str, Any]]:
        if end < start:
            return []
        result = []
        target = start
        while target <= end:
            result.extend(self.on(target))
            target += timedelta(days=1)
        return result

    def upcoming(self, target: date, days: int = 7) -> list[dict[str, Any]]:
        return self.between(target + timedelta(days=1), target + timedelta(days=max(0, days)))
