import json
import unittest
from datetime import date

from core.generator import DailyPlanGenerator
from core.long_term import validate_stage
from core.models import DailyPlan
from core.persona import PersonaContext

from tests.fixtures import outfit_payload


class Response:
    def __init__(self, text):
        self.completion_text = text


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []
        self.system_prompts = []

    async def text_chat(self, prompt, session_id, system_prompt=None):
        self.calls += 1
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        return Response(self.responses.pop(0))


class Context:
    def __init__(self, provider, selected_provider=None):
        self.provider = provider
        self.selected_provider = selected_provider
        self.requested_provider_ids = []

    def get_provider_by_id(self, provider_id):
        self.requested_provider_ids.append(provider_id)
        return self.selected_provider

    def get_using_provider(self):
        return self.provider


def valid_json():
    return json.dumps(
        {
            "date": "ignored",
            "theme": "日常",
            "mood": "平静",
            "outfit": outfit_payload(),
            "timeline": [{"id": "all", "start": "00:00", "end": "24:00", "activity": "正常生活", "location": "家", "state": "available", "availability": "normal"}],
            "proactive_windows": [],
            "budget_bonus": {"private": 1, "group": 0},
        },
        ensure_ascii=False,
    )


class GeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_output_retries_then_succeeds(self):
        provider = Provider(["not json", valid_json()])
        config = {
            "schedule_settings": {"generation_retries": 1, "prompt_template": "{date} {persona} {theme} {mood} {outfit_style}"},
            "creative_pool": {},
        }
        generator = DailyPlanGenerator(Context(provider), config)
        plan = await generator.generate(date(2026, 7, 14), PersonaContext("alice", "persona"))
        self.assertEqual(plan.status, "ok")
        self.assertEqual(plan.persona_id, "alice")
        self.assertEqual(provider.calls, 2)

    async def test_invalid_outputs_create_failed_plan(self):
        provider = Provider(["bad", "still bad"])
        config = {
            "schedule_settings": {"generation_retries": 1, "prompt_template": "{date} {persona} {theme} {mood} {outfit_style}"},
            "creative_pool": {},
        }
        generator = DailyPlanGenerator(Context(provider), config)
        plan = await generator.generate(date(2026, 7, 14), PersonaContext("alice", "persona"))
        self.assertEqual(plan.status, "failed")
        self.assertEqual(provider.calls, 2)

    async def test_history_plans_are_injected_into_prompt(self):
        provider = Provider([valid_json()])
        config = {
            "schedule_settings": {"generation_retries": 0, "prompt_template": "{date} {persona} {theme} {mood} {outfit_style}"},
            "creative_pool": {},
        }
        history = DailyPlan.from_dict(
            {
                "date": "2026-07-13",
                "persona_id": "alice",
                "theme": "宅家日",
                "mood": "慵懒",
                "outfit": outfit_payload("舒适的居家造型"),
                "timeline": [{"id": "all", "start": "00:00", "end": "24:00", "activity": "在家看书", "state": "available", "availability": "normal"}],
                "proactive_windows": [],
                "budget_bonus": {"private": 0, "group": 0},
            }
        )
        generator = DailyPlanGenerator(Context(provider), config)
        await generator.generate(
            date(2026, 7, 14),
            PersonaContext("alice", "persona"),
            history_plans=[history],
        )
        self.assertIn("2026-07-13", provider.prompts[0])
        self.assertIn("在家看书", provider.prompts[0])
        self.assertIn("不要照抄", provider.prompts[0])

    async def test_generation_system_prompt_is_passed_separately(self):
        provider = Provider([valid_json()])
        config = {
            "schedule_settings": {
                "generation_retries": 0,
                "generation_system_prompt": "严格覆盖 00:00 到 24:00，并在输出前自检。",
                "prompt_template": "{date} {persona} {theme} {mood} {outfit_style}",
            },
            "creative_pool": {},
        }
        generator = DailyPlanGenerator(Context(provider), config)
        await generator.generate(date(2026, 7, 14), PersonaContext("alice", "persona"))
        self.assertIn("严格覆盖 00:00 到 24:00，并在输出前自检。", provider.system_prompts[0])
        self.assertIn("outfit 必须是 JSON 对象", provider.system_prompts[0])
        self.assertIn("underwear", provider.system_prompts[0])

    async def test_schedule_provider_can_be_selected_independently(self):
        default_provider = Provider([])
        selected_provider = Provider([valid_json()])
        context = Context(default_provider, selected_provider=selected_provider)
        config = {
            "schedule_settings": {
                "schedule_llm_provider": "schedule-provider",
                "generation_retries": 0,
                "prompt_template": "{date} {persona} {theme} {mood} {outfit_style}",
            },
            "creative_pool": {},
        }
        generator = DailyPlanGenerator(context, config)
        plan = await generator.generate(date(2026, 7, 14), PersonaContext("alice", "persona"))
        self.assertEqual(plan.status, "ok")
        self.assertEqual(context.requested_provider_ids, ["schedule-provider"])
        self.assertEqual(selected_provider.calls, 1)
        self.assertEqual(default_provider.calls, 0)

    async def test_legacy_provider_setting_is_not_used(self):
        default_provider = Provider([valid_json()])
        selected_provider = Provider([])
        context = Context(default_provider, selected_provider=selected_provider)
        config = {
            "schedule_settings": {
                "llm_provider": "legacy-provider",
                "generation_retries": 0,
                "prompt_template": "{date} {persona} {theme} {mood} {outfit_style}",
            },
            "creative_pool": {},
        }
        generator = DailyPlanGenerator(context, config)
        plan = await generator.generate(date(2026, 7, 14), PersonaContext("alice", "persona"))
        self.assertEqual(plan.status, "ok")
        self.assertEqual(context.requested_provider_ids, [])
        self.assertEqual(default_provider.calls, 1)
        self.assertEqual(selected_provider.calls, 0)

    async def test_long_term_context_is_injected(self):
        provider = Provider([valid_json()])
        config = {
            "schedule_settings": {
                "generation_retries": 0,
                "prompt_template": "{date} {persona} {theme} {mood} {outfit_style}",
            },
            "creative_pool": {},
        }
        generator = DailyPlanGenerator(Context(provider), config)
        await generator.generate(
            date(2026, 9, 7),
            PersonaContext("student", "persona"),
            long_term_context="<long_term_timeline>开学典礼</long_term_timeline>",
        )
        self.assertIn("开学典礼", provider.prompts[0])

    async def test_long_term_generation_uses_required_start(self):
        response = """{
          "stages": [{
            "id": "semester-spring",
            "name": "春季学期",
            "kind": "academic",
            "start_date": "2027-02-20",
            "end_date": "2027-07-10",
            "priority": 75,
            "summary": "正常上课",
            "weekly_rules": [],
            "special_dates": [],
            "special_periods": [],
            "milestones": [],
            "constraints": []
          }]
        }"""
        provider = Provider([response])
        generator = DailyPlanGenerator(
            Context(provider),
            {"schedule_settings": {}, "creative_pool": {}},
        )
        previous = validate_stage(
            {
                "id": "winter",
                "name": "寒假",
                "kind": "academic",
                "start_date": "2027-01-21",
                "end_date": "2027-02-19",
            },
            "student",
        )
        stages = await generator.generate_long_term_timeline(
            PersonaContext("student", "persona"),
            start_date=date(2027, 2, 20),
            previous_stage=previous,
        )
        self.assertEqual(stages[0]["start_date"], "2027-02-20")
        self.assertEqual(stages[0]["priority"], 75)
        self.assertIn("priority", provider.system_prompts[0])
        self.assertIn("text values such as high, medium, or low", provider.system_prompts[0])
        self.assertIn("start and end must be non-empty", provider.system_prompts[0])
        self.assertIn("unique integers from 1 to 7", provider.system_prompts[0])
        self.assertIn("required must be a JSON boolean", provider.system_prompts[0])
        self.assertIn("Special period rules", provider.system_prompts[0])
        self.assertIn("non-negative JSON integer", provider.system_prompts[0])
        self.assertIn("self-check every field", provider.system_prompts[0])
        self.assertIn("前一阶段：寒假", provider.prompts[0])


if __name__ == "__main__":
    unittest.main()
