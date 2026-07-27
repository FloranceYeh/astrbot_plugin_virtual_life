import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from core.message_segmenter import ProactiveMessageSegmenter


def segmenter_settings(**overrides):
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    items = schema["delivery_settings"]["items"]["segmented_reply_settings"]["items"]
    settings = {
        key: value["default"] for key, value in items.items() if "default" in value
    }
    settings.update(overrides)
    return settings


class MessageSegmenterTests(unittest.TestCase):
    def test_words_mode_splits_chinese_english_and_newline(self):
        segmenter = ProactiveMessageSegmenter(segmenter_settings())

        result = segmenter.split("下班啦！Want tea?\n我请客。")

        self.assertEqual(result.segments, ["下班啦！", "Want tea?", "我请客。"])
        self.assertEqual(result.mode, "words")
        self.assertFalse(result.skipped_reason)

    def test_text_over_threshold_is_not_segmented(self):
        text = "第一句。第二句。"
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(words_count_threshold=len(text) - 1)
        )

        result = segmenter.split(text)

        self.assertEqual(result.segments, [text])
        self.assertEqual(result.skipped_reason, "over threshold")

    def test_zero_threshold_disables_segmentation_for_nonempty_text(self):
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(words_count_threshold=0)
        )

        result = segmenter.split("一句。")

        self.assertEqual(result.segments, ["一句。"])
        self.assertEqual(result.skipped_reason, "over threshold")

    def test_regex_mode_supports_capture_groups(self):
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(
                split_mode="regex",
                regex=r"(.*?[。！])|(.+$)",
            )
        )

        self.assertEqual(segmenter.split("第一句。第二句！收尾").segments, ["第一句。", "第二句！", "收尾"])

    def test_invalid_regex_keeps_original_text(self):
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(split_mode="regex", regex="(")
        )

        result = segmenter.split("第一句。第二句！")

        self.assertEqual(result.segments, ["第一句。第二句！"])
        self.assertEqual(result.skipped_reason, "invalid regex")

    def test_invalid_cleanup_is_ignored(self):
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(
                enable_content_cleanup=True,
                content_cleanup_rule="[",
            )
        )

        self.assertEqual(
            segmenter.split("第一句。第二句！").segments, ["第一句。", "第二句！"]
        )

    def test_cleanup_rule_removes_matching_content(self):
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(
                enable_content_cleanup=True,
                content_cleanup_rule="[。！]",
            )
        )

        self.assertEqual(segmenter.split("第一句。第二句！").segments, ["第一句", "第二句"])

    def test_empty_words_list_keeps_original_text(self):
        segmenter = ProactiveMessageSegmenter(segmenter_settings(split_words=[]))

        result = segmenter.split("第一句。第二句。")

        self.assertEqual(result.segments, ["第一句。第二句。"])
        self.assertEqual(result.skipped_reason, "empty split words")

    @patch("core.message_segmenter.random.uniform", return_value=1.25)
    def test_random_interval_uses_configured_range(self, uniform):
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(interval_method="random", interval="2.0,0.8")
        )

        self.assertEqual(segmenter.interval_for("消息"), 1.25)
        uniform.assert_called_once_with(0.8, 2.0)

    @patch("core.message_segmenter.random.uniform", return_value=3.0)
    def test_log_interval_uses_segment_length(self, uniform):
        segmenter = ProactiveMessageSegmenter(segmenter_settings(log_base=2.6))

        self.assertEqual(segmenter.interval_for("四个字符"), 3.0)
        minimum = math.log(5, 2.6)
        uniform.assert_called_once_with(minimum, minimum + 0.5)

    @patch("core.message_segmenter.random.uniform", return_value=2.0)
    def test_log_interval_counts_ascii_words(self, uniform):
        segmenter = ProactiveMessageSegmenter(segmenter_settings(log_base=2.6))

        self.assertEqual(segmenter.interval_for("two words!"), 2.0)
        minimum = math.log(3, 2.6)
        uniform.assert_called_once_with(minimum, minimum + 0.5)

    def test_empty_settings_do_not_supply_hidden_defaults(self):
        segmenter = ProactiveMessageSegmenter({})

        result = segmenter.split("第一句。第二句。")

        self.assertEqual(result.segments, ["第一句。第二句。"])
        self.assertEqual(result.skipped_reason, "disabled")

    @patch("core.message_segmenter.random.uniform")
    def test_invalid_interval_does_not_fall_back(self, uniform):
        segmenter = ProactiveMessageSegmenter(
            segmenter_settings(interval_method="random", interval="invalid")
        )

        self.assertEqual(segmenter.interval_for("消息"), 0.0)
        uniform.assert_not_called()


if __name__ == "__main__":
    unittest.main()
