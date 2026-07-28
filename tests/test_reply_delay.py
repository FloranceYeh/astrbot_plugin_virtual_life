import asyncio
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.models import TimelineItem
from core.reply_delay import (
    DelayDecision,
    FormulaError,
    QueuedMessage,
    ReplyDelayCoordinator,
    ReplyDelayPolicy,
    SafeFormulaEvaluator,
    format_delay_context,
    format_queued_messages,
)


def reply_delay_settings() -> dict:
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    settings = {}
    for key, item in schema["reply_delay_settings"]["items"].items():
        if item.get("type") == "object":
            settings[key] = {
                nested_key: nested_item["default"]
                for nested_key, nested_item in item["items"].items()
            }
        elif "default" in item:
            settings[key] = item["default"]
    return settings


class SafeFormulaEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = SafeFormulaEvaluator(
            random_fn=lambda low, high: (low + high) / 2
        )

    def test_supported_variables_functions_and_arithmetic(self):
        result = self.evaluator.evaluate(
            "ceil(random(0, 4) + min(remaining, message_length) / 2)",
            {"remaining": 30, "message_length": 6},
        )
        self.assertEqual(result, 5)

    def test_rejects_attributes_and_unknown_names(self):
        with self.assertRaises(FormulaError):
            self.evaluator.evaluate("remaining.__class__", {"remaining": 10})
        with self.assertRaises(FormulaError):
            self.evaluator.evaluate("unknown + 1", {"remaining": 10})

    def test_rejects_code_execution(self):
        with self.assertRaises(FormulaError):
            self.evaluator.evaluate("__import__('os').system('whoami')", {})

    def test_rejects_non_finite_result(self):
        with self.assertRaises(FormulaError):
            self.evaluator.evaluate("1 / 0", {})


class ReplyDelayPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 28, 9, 59, 50)
        self.item = TimelineItem(
            id="study",
            start="09:00",
            end="10:00",
            activity="自习",
            location="图书馆",
            state="focus",
            availability="low",
        )

    def test_delay_is_capped_by_period_end(self):
        policy = ReplyDelayPolicy(
            {
                "enable": True,
                "max_delay_seconds": 1800,
                "delay_formulas": {"low": "300"},
            }
        )
        decision = policy.decide(self.item, self.now, 4)
        self.assertEqual(decision.remaining_seconds, 10)
        self.assertEqual(decision.delay_seconds, 10)

    def test_subsecond_period_end_is_never_exceeded(self):
        policy = ReplyDelayPolicy({"delay_formulas": {"low": "300"}})
        now = datetime(2026, 7, 28, 9, 59, 59, 500000)
        decision = policy.decide(self.item, now, 4)
        self.assertEqual(decision.remaining_seconds, 0)
        self.assertEqual(decision.delay_seconds, 0)

    def test_delay_is_capped_by_configured_maximum(self):
        policy = ReplyDelayPolicy(
            {
                "max_delay_seconds": 30,
                "delay_formulas": {"low": "remaining"},
            }
        )
        decision = policy.decide(self.item, datetime(2026, 7, 28, 9, 0), 4)
        self.assertEqual(decision.delay_seconds, 30)

    def test_active_conversation_skips_formula_delay(self):
        policy = ReplyDelayPolicy({"delay_formulas": {"low": "300"}})
        decision = policy.decide(self.item, self.now, 4, active_conversation=True)
        self.assertEqual(decision.delay_seconds, 0)
        self.assertTrue(decision.active_conversation)

    def test_invalid_configured_formula_does_not_use_hidden_fallback(self):
        evaluator = SafeFormulaEvaluator(random_fn=lambda low, high: low)
        policy = ReplyDelayPolicy(
            {
                "max_delay_seconds": 1800,
                "delay_formulas": {"low": "__import__('os')"},
            },
            evaluator=evaluator,
        )
        decision = policy.decide(self.item, datetime(2026, 7, 28, 9, 0), 4)
        self.assertEqual(decision.formula, "__import__('os')")
        self.assertEqual(decision.delay_seconds, 0)
        self.assertTrue(decision.formula_error)

    def test_empty_settings_do_not_supply_hidden_defaults(self):
        policy = ReplyDelayPolicy()
        decision = policy.decide(self.item, datetime(2026, 7, 28, 9, 0), 4)

        self.assertFalse(policy.enabled)
        self.assertFalse(policy.notify_user)
        self.assertEqual(policy.active_conversation_seconds, 0)
        self.assertEqual(policy.max_delay_seconds, 0)
        self.assertEqual(decision.delay_seconds, 0)
        self.assertTrue(decision.formula_error)
        self.assertEqual(policy.notification(decision), "")

    def test_notification_only_supports_public_fields(self):
        policy = ReplyDelayPolicy(
            {
                "notification_template": "{availability}: {public_reason}; {delay_seconds}s",
                "public_reasons": {"low": "暂时不便回复"},
            }
        )
        decision = policy.decide(self.item, self.now, 4, active_conversation=True)
        self.assertEqual(policy.notification(decision), "low: 暂时不便回复; 0s")

    def test_schema_defaults_match_runtime_defaults(self):
        settings = reply_delay_settings()
        policy = ReplyDelayPolicy(settings)

        self.assertTrue(policy.enabled)
        self.assertTrue(policy.notify_user)
        self.assertEqual(policy.active_conversation_seconds, 120)
        self.assertEqual(policy.max_delay_seconds, 1800)
        self.assertEqual(
            policy.notification(policy.decide(self.item, self.now, 4))[:2], "预计"
        )


class ReplyDelayCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def decision(seconds: int) -> DelayDecision:
        return DelayDecision(
            delay_seconds=seconds,
            remaining_seconds=100,
            availability="normal",
            formula=str(seconds),
            public_reason="正在处理事情",
        )

    @staticmethod
    def message(at: datetime, prompt: str) -> QueuedMessage:
        return QueuedMessage(at, "42", "用户", prompt)

    async def test_messages_join_existing_batch_without_extending_deadline(self):
        coordinator = ReplyDelayCoordinator()
        started = datetime(2026, 7, 28, 12, 0)
        batch, primary = await coordinator.enqueue(
            "umo", self.message(started, "第一条"), self.decision(10)
        )
        joined, joined_primary = await coordinator.enqueue(
            "umo",
            self.message(started + timedelta(seconds=8), "第二条"),
            self.decision(99),
        )

        self.assertTrue(primary)
        self.assertFalse(joined_primary)
        self.assertIs(joined, batch)
        self.assertEqual(batch.deadline, started + timedelta(seconds=10))
        messages = await coordinator.settle(batch, batch.deadline)
        self.assertEqual([item.prompt for item in messages], ["第一条", "第二条"])

    async def test_message_at_deadline_starts_a_new_batch(self):
        coordinator = ReplyDelayCoordinator()
        started = datetime(2026, 7, 28, 12, 0)
        old_batch, _ = await coordinator.enqueue(
            "umo", self.message(started, "旧批次"), self.decision(10)
        )
        new_batch, primary = await coordinator.enqueue(
            "umo",
            self.message(started + timedelta(seconds=10), "新批次"),
            self.decision(5),
        )

        self.assertTrue(primary)
        self.assertIsNot(new_batch, old_batch)
        old_messages = await coordinator.settle(old_batch, old_batch.deadline)
        new_messages = await coordinator.settle(new_batch, new_batch.deadline)
        self.assertEqual([item.prompt for item in old_messages], ["旧批次"])
        self.assertEqual([item.prompt for item in new_messages], ["新批次"])

    async def test_manual_settlement_wakes_waiting_batch(self):
        coordinator = ReplyDelayCoordinator()
        started = datetime(2026, 7, 28, 12, 0)
        batch, _ = await coordinator.enqueue(
            "umo", self.message(started, "立即结算"), self.decision(3600)
        )
        waiting = asyncio.create_task(coordinator.settle(batch, started))
        await asyncio.sleep(0)

        settled = await coordinator.settle_now("umo")
        messages = await asyncio.wait_for(waiting, timeout=0.1)

        self.assertIs(settled, batch)
        self.assertEqual([item.prompt for item in messages], ["立即结算"])
        self.assertIsNone(await coordinator.settle_now("umo"))

    async def test_settle_all_wakes_every_session(self):
        coordinator = ReplyDelayCoordinator()
        started = datetime(2026, 7, 28, 12, 0)
        batches = []
        waiters = []
        for umo in ("private", "group"):
            batch, _ = await coordinator.enqueue(
                umo, self.message(started, umo), self.decision(3600)
            )
            batches.append(batch)
            waiters.append(asyncio.create_task(coordinator.settle(batch, started)))
        await asyncio.sleep(0)

        settled = await coordinator.settle_all_now()
        results = await asyncio.wait_for(asyncio.gather(*waiters), timeout=0.1)

        self.assertEqual(set(map(id, settled)), set(map(id, batches)))
        self.assertEqual(
            [[item.prompt for item in result] for result in results],
            [["private"], ["group"]],
        )

    def test_active_window_expires_and_is_not_persisted(self):
        coordinator = ReplyDelayCoordinator()
        now = datetime(2026, 7, 28, 12, 0)
        coordinator.mark_replied("umo", now, 120)
        self.assertTrue(coordinator.is_active("umo", now + timedelta(seconds=119)))
        self.assertFalse(coordinator.is_active("umo", now + timedelta(seconds=120)))
        coordinator.mark_replied("umo", now, 120)
        coordinator.clear()
        self.assertFalse(coordinator.is_active("umo", now))

    async def test_batch_format_preserves_group_senders_and_private_state(self):
        coordinator = ReplyDelayCoordinator()
        started = datetime(2026, 7, 28, 12, 0)
        item = TimelineItem(
            id="lunch",
            start="12:00",
            end="13:00",
            activity="和同事吃午饭",
            location="餐厅",
            state="social",
            availability="normal",
        )
        batch, _ = await coordinator.enqueue(
            "umo",
            QueuedMessage(started, "1", "甲", "第一条", ("image",), ()),
            self.decision(10),
            item,
        )
        await coordinator.enqueue(
            "umo",
            QueuedMessage(started + timedelta(seconds=2), "2", "乙", "第二条"),
            self.decision(99),
            item,
        )
        messages = await coordinator.settle(batch, batch.deadline)
        merged = format_queued_messages(messages, group=True)
        context = format_delay_context(batch, batch.deadline, item)

        self.assertIn("甲 (1)：第一条 [附件：1 张图片]", merged)
        self.assertIn("乙 (2)：第二条", merged)
        self.assertIn("消息到达时活动：和同事吃午饭", context)
        self.assertIn("消息到达时地点：餐厅", context)
        self.assertIn("不要主动向用户披露", context)


if __name__ == "__main__":
    unittest.main()
