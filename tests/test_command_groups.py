import json
import unittest
from pathlib import Path


class CommandGroupContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("main.py").read_text(encoding="utf-8")

    def test_daily_schedule_uses_group_only(self):
        self.assertIn('@filter.command_group("虚拟日程")', self.source)
        self.assertIn('@virtual_daily_group.command("查看")', self.source)
        self.assertIn('@virtual_daily_group.command("穿搭")', self.source)
        self.assertIn('@virtual_daily_group.command("重写")', self.source)
        self.assertIn('@virtual_daily_group.command("重写日程")', self.source)
        self.assertIn('@virtual_daily_group.command("重写穿搭")', self.source)
        self.assertIn("self.plan_generator.rewrite_schedule", self.source)
        self.assertIn("self.plan_generator.rewrite_outfit", self.source)
        self.assertNotIn('@filter.command("查看虚拟日程"', self.source)
        self.assertNotIn('@filter.command("重写虚拟日程"', self.source)

    def test_virtual_life_group_contains_subscription(self):
        self.assertIn('@filter.command_group("虚拟人生")', self.source)
        self.assertIn('@virtual_life_group.command("订阅会话")', self.source)
        self.assertIn("self.config.save_config()", self.source)
        self.assertNotIn('@filter.command("sid")', self.source)

    def test_proactive_commands_share_group(self):
        self.assertIn('@filter.command_group("主动消息")', self.source)
        for command in ("状态", "立即", "回访列表", "取消回访", "执行时间"):
            self.assertIn(f'@proactive_group.command("{command}")', self.source)
        for legacy in ("主动消息状态", "立即主动", "回访列表", "取消回访"):
            self.assertNotIn(f'@filter.command("{legacy}")', self.source)
        self.assertIn("self.runtime.scheduled_jobs()", self.source)

    def test_long_term_commands_share_group(self):
        self.assertIn('@filter.command_group("大时间表")', self.source)
        for command in (
            "生成",
            "导入",
            "草稿",
            "批准",
            "拒绝",
            "列表",
            "查看",
            "重生成",
        ):
            self.assertIn(f'@long_term_group.command("{command}")', self.source)

    def test_view_commands_use_image_renderer(self):
        self.assertIn("self.image_renderer.render_timeline", self.source)
        self.assertIn("self.image_renderer.render_outfit", self.source)
        self.assertIn("self.long_term.expand_day", self.source)
        self.assertIn("self.long_term.holidays.on", self.source)
        self.assertIn("self.image_renderer.render_stage_list", self.source)
        self.assertIn('status="draft"', self.source)
        self.assertIn(
            "self.image_renderer.render_stage(stage, persona.id)", self.source
        )
        self.assertIn("图片渲染失败，已切换为文字模式", self.source)

    def test_groups_and_stage_view_have_navigation_help(self):
        self.assertIn("不提供子命令时由 AstrBot 输出帮助", self.source)
        self.assertIn("stage_id: str | None = None", self.source)
        self.assertIn("self.long_term.resolve_stage", self.source)

    def test_default_prompt_requires_structured_outfit(self):
        schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
        settings = schema["schedule_settings"]["items"]
        schedule_system = settings["schedule_generation_system_prompt"]["default"]
        schedule_template = settings["schedule_prompt_template"]["default"]
        outfit_system = settings["outfit_generation_system_prompt"]["default"]
        outfit_template = settings["outfit_prompt_template"]["default"]
        self.assertIn("timeline 第一项", schedule_system)
        self.assertIn("{outfit_context}", schedule_template)
        self.assertIn(
            "outfit 必须是包含非空 style、summary 和 items 数组", outfit_system
        )
        self.assertIn("underwear", outfit_system)
        self.assertIn("{outfit_style}", outfit_template)
        self.assertNotIn("generation_system_prompt", settings)
        self.assertNotIn("prompt_template", settings)


if __name__ == "__main__":
    unittest.main()
