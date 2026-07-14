import unittest
from datetime import date

from core.generator import DailyPlanGenerator
from core.models import DailyPlan
from core.persona import PersonaContext


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
    def __init__(self, provider):
        self.provider = provider

    def get_provider_by_id(self, provider_id):
        return None

    def get_using_provider(self):
        return self.provider


def valid_json():
    return """{
      "date":"ignored",
      "theme":"日常",
      "mood":"平静",
      "outfit":"休闲装",
      "timeline":[{"id":"all","start":"00:00","end":"24:00","activity":"正常生活","location":"家","state":"available","availability":"normal"}],
      "proactive_windows":[],
      "budget_bonus":{"private":1,"group":0}
    }"""


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
                "outfit": "居家裙",
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
        self.assertEqual(provider.system_prompts[0], "严格覆盖 00:00 到 24:00，并在输出前自检。")


if __name__ == "__main__":
    unittest.main()
