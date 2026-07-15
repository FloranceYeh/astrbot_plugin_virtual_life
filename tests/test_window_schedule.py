import unittest

from core.models import DailyPlan
from core.window_schedule import proactive_window_offset_minutes
from tests.test_models import valid_payload


class ProactiveWindowScheduleTests(unittest.TestCase):
    def test_offset_is_deterministic_and_non_zero_for_sample(self):
        plan = DailyPlan.from_dict(valid_payload())
        window = plan.proactive_windows[0]
        first = proactive_window_offset_minutes(plan, window, 15)
        second = proactive_window_offset_minutes(plan, window, 15)
        self.assertEqual(first, second)
        self.assertEqual(first, 4)

    def test_zero_disables_jitter(self):
        plan = DailyPlan.from_dict(valid_payload())
        self.assertEqual(proactive_window_offset_minutes(plan, plan.proactive_windows[0], 0), 0)

    def test_offset_stays_inside_source_activity(self):
        payload = valid_payload()
        payload["proactive_windows"][0]["at"] = "07:00"
        plan = DailyPlan.from_dict(payload)
        offset = proactive_window_offset_minutes(plan, plan.proactive_windows[0], 15)
        self.assertGreaterEqual(offset, 0)

        payload["proactive_windows"][0]["at"] = "22:59"
        plan = DailyPlan.from_dict(payload)
        offset = proactive_window_offset_minutes(plan, plan.proactive_windows[0], 15)
        self.assertLessEqual(offset, 0)


if __name__ == "__main__":
    unittest.main()
