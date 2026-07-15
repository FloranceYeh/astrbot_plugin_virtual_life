from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

DEFAULT_REGEX = r".*?[。？！~…]+|.+$"
DEFAULT_SPLIT_WORDS = ["。", "？", "！", "?", "!", "~", "…", r"\n"]
DEFAULT_RANDOM_INTERVAL = (0.8, 2.0)
DEFAULT_LOG_BASE = 2.6


@dataclass(slots=True, frozen=True)
class SegmentResult:
    segments: list[str]
    mode: str
    source_length: int
    threshold: int
    skipped_reason: str = ""


class ProactiveMessageSegmenter:
    def __init__(self, settings: dict[str, Any]):
        self.enabled = bool(settings.get("segment_reply", True))
        self.threshold = self._positive_int(settings.get("segment_words_count_threshold"), 150)
        mode = str(settings.get("segment_split_mode", "words")).strip().lower()
        self.mode = mode if mode in {"words", "regex"} else "words"
        self.regex = self._compile_split_regex(str(settings.get("segment_regex", DEFAULT_REGEX)))
        self.split_words = self._split_words(settings.get("segment_words", DEFAULT_SPLIT_WORDS))
        self.words_pattern = self._compile_words_pattern(self.split_words)
        self.cleanup_pattern = self._compile_cleanup_regex(str(settings.get("segment_content_cleanup_rule", "")))
        interval_method = str(settings.get("segment_interval_method", "log")).strip().lower()
        self.interval_method = interval_method if interval_method in {"random", "log"} else "log"
        self.random_interval = self._random_interval(settings.get("segment_interval", "0.8,2.0"))
        self.log_base = self._log_base(settings.get("segment_log_base", DEFAULT_LOG_BASE))

    def split(self, text: str) -> SegmentResult:
        source_length = len(text)
        if not self.enabled:
            return self._unchanged(text, source_length, "disabled")
        if source_length > self.threshold:
            return self._unchanged(text, source_length, "over threshold")
        if self.mode == "words":
            if self.words_pattern is None:
                return self._unchanged(text, source_length, "empty split words")
            segments = [match.group(0) for match in self.words_pattern.finditer(text)]
        else:
            segments = [match.group(0) for match in self.regex.finditer(text)]
        cleaned = self._clean_segments(segments)
        if not cleaned:
            return self._unchanged(text, source_length, "no segments")
        return SegmentResult(cleaned, self.mode, source_length, self.threshold)

    def interval_for(self, segment: str) -> float:
        if self.interval_method == "random":
            return random.uniform(*self.random_interval)
        minimum = math.log(len(segment) + 1, self.log_base)
        return random.uniform(minimum, minimum + 0.5)

    def _unchanged(self, text: str, source_length: int, reason: str) -> SegmentResult:
        return SegmentResult([text], self.mode, source_length, self.threshold, reason)

    def _clean_segments(self, segments: list[str]) -> list[str]:
        result = []
        for segment in segments:
            if self.cleanup_pattern is not None:
                segment = self.cleanup_pattern.sub("", segment)
            segment = segment.strip()
            if segment:
                result.append(segment)
        return result

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _split_words(value: Any) -> list[str]:
        if not isinstance(value, list):
            return list(DEFAULT_SPLIT_WORDS)
        result = []
        for item in value:
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
    def _compile_split_regex(value: str) -> re.Pattern[str]:
        try:
            return re.compile(value or DEFAULT_REGEX, re.DOTALL | re.MULTILINE)
        except re.error as exc:
            logger.warning("[虚拟人生] 主动消息分段正则无效，使用默认规则: %s", exc)
            return re.compile(DEFAULT_REGEX, re.DOTALL | re.MULTILINE)

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
    def _random_interval(value: Any) -> tuple[float, float]:
        try:
            parts = [float(item.strip()) for item in str(value).split(",")]
            if len(parts) != 2 or min(parts) < 0:
                raise ValueError
            return min(parts), max(parts)
        except (TypeError, ValueError):
            logger.warning("[虚拟人生] 主动消息随机分段间隔无效，使用默认值 0.8,2.0")
            return DEFAULT_RANDOM_INTERVAL

    @staticmethod
    def _log_base(value: Any) -> float:
        try:
            base = float(value)
            if base <= 1:
                raise ValueError
            return base
        except (TypeError, ValueError):
            logger.warning("[虚拟人生] 主动消息对数分段基数无效，使用默认值 2.6")
            return DEFAULT_LOG_BASE
