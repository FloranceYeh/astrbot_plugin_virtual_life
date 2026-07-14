import unittest
from datetime import date

from core.generator import DailyPlanGenerator
from core.persona import PersonaContext


class Response:
    def __init__(self, text):
        self.completion_text = text


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def text_chat(self, prompt, session_id):
        self.calls += 1
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


if __name__ == "__main__":
    unittest.main()
