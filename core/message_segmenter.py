from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger


@dataclass(slots=True, frozen=True)
class SegmentResult:
    segments: list[str]
    mode: str
    source_length: int
    threshold: int
    skipped_reason: str = ""


class ProactiveMessageSegmenter:
    def __init__(self, settings: dict[str, Any]):
        self.enabled = bool(settings.get("enable"))
        self.threshold = self._nonnegative_int(settings.get("words_count_threshold"))
        self.mode = str(settings.get("split_mode") or "").strip().lower()
        self.regex = self._compile_split_regex(settings.get("regex"))
        self.split_words = self._split_words(settings.get("split_words"))
        self.words_pattern = self._compile_words_pattern(self.split_words)
        cleanup_rule = (
            str(settings.get("content_cleanup_rule") or "")
            if settings.get("enable_content_cleanup")
            else ""
        )
        self.cleanup_pattern = self._compile_cleanup_regex(cleanup_rule)
        self.interval_method = str(settings.get("interval_method") or "").strip().lower()
        self.random_interval = self._random_interval(settings.get("interval"))
        self.log_base = self._log_base(settings.get("log_base"))

    def split(self, text: str) -> SegmentResult:
        source_length = len(text)
        if not self.enabled:
            return self._unchanged(text, source_length, "disabled")
        if self.threshold is None:
            return self._unchanged(text, source_length, "invalid threshold")
        if source_length > self.threshold:
            return self._unchanged(text, source_length, "over threshold")
        if self.mode == "words":
            if self.words_pattern is None:
                return self._unchanged(text, source_length, "empty split words")
            segments = [match.group(0) for match in self.words_pattern.finditer(text)]
        elif self.mode == "regex":
            if self.regex is None:
                return self._unchanged(text, source_length, "invalid regex")
            segments = [match.group(0) for match in self.regex.finditer(text)]
        else:
            return self._unchanged(text, source_length, "invalid split mode")
        cleaned = self._clean_segments(segments)
        if not cleaned:
            return self._unchanged(text, source_length, "no segments")
        return SegmentResult(cleaned, self.mode, source_length, self.threshold)

    def interval_for(self, segment: str) -> float:
        if self.interval_method == "random":
            if self.random_interval is None:
                return 0.0
            return random.uniform(*self.random_interval)
        if self.interval_method != "log" or self.log_base is None:
            return 0.0
        if all(ord(character) < 128 for character in segment):
            word_count = len(segment.split())
        else:
            word_count = len([character for character in segment if character.isalnum()])
        minimum = math.log(word_count + 1, self.log_base)
        return random.uniform(minimum, minimum + 0.5)

    def _unchanged(self, text: str, source_length: int, reason: str) -> SegmentResult:
        return SegmentResult([text], self.mode, source_length, self.threshold, reason)

    def _clean_segments(self, segments: list[str]) -> list[str]:
        result = []
        for segment in segments:
            if self.cleanup_pattern is not None:
                segment = self.cleanup_pattern.sub("", segment)
            if segment.strip():
                result.append(segment)
        return result

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _split_words(value: Any) -> list[str]:
        if isinstance(value, str):
            values = [value]
        else:
            try:
                values = list(value)
            except TypeError:
                return []
        result = []
        for item in values:
            word = str(item)
            if word == r"\n":
                word = "\n"
            if word and word not in result:
                result.append(word)
        return result

    @staticmethod
    def _compile_words_pattern(words: list[str]) -> re.Pattern[str] | None:
        if not words:
            return None
        escaped = sorted((re.escape(word) for word in words), key=len, reverse=True)
        return re.compile(f".*?(?:{'|'.join(escaped)})|.+$", re.DOTALL)

    @staticmethod
    def _compile_split_regex(value: Any) -> re.Pattern[str] | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return re.compile(value, re.DOTALL | re.MULTILINE)
        except re.error as exc:
            logger.warning(
                "[Virtual Life] Invalid proactive split regex; segmentation skipped: %s",
                exc,
            )
            return None

    @staticmethod
    def _compile_cleanup_regex(value: str) -> re.Pattern[str] | None:
        if not value:
            return None
        try:
            return re.compile(value)
        except re.error as exc:
            logger.warning("[虚拟人生] 主动消息清理正则无效，已跳过: %s", exc)
            return None

    @staticmethod
    def _random_interval(value: Any) -> tuple[float, float] | None:
        try:
            parts = [float(item.strip()) for item in str(value).split(",")]
            if len(parts) != 2 or any(not math.isfinite(part) or part < 0 for part in parts):
                raise ValueError
            return min(parts), max(parts)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _log_base(value: Any) -> float | None:
        try:
            base = float(value)
            if not math.isfinite(base) or base <= 1:
                raise ValueError
            return base
        except (TypeError, ValueError):
            return None
