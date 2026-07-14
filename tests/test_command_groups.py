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
        self.assertIn('@virtual_daily_group.command("重写")', self.source)
        self.assertNotIn('@filter.command("查看虚拟日程"', self.source)
        self.assertNotIn('@filter.command("重写虚拟日程"', self.source)

    def test_long_term_commands_share_group(self):
        self.assertIn('@filter.command_group("大时间表")', self.source)
        for command in ("生成", "导入", "草稿", "批准", "拒绝", "列表", "查看", "重生成"):
            self.assertIn(f'@long_term_group.command("{command}")', self.source)

    def test_view_commands_use_image_renderer(self):
        self.assertIn("self.image_renderer.render_daily", self.source)
        self.assertIn("self.image_renderer.render_stage_list", self.source)
        self.assertIn('status="draft"', self.source)
        self.assertIn("self.image_renderer.render_stage(stage, persona.id)", self.source)
        self.assertIn("图片渲染失败，已切换为文字模式", self.source)

    def test_groups_and_stage_view_have_navigation_help(self):
        self.assertIn("不提供子命令时由 AstrBot 输出帮助", self.source)
        self.assertIn("stage_id: str | None = None", self.source)
        self.assertIn("self.long_term.resolve_stage", self.source)

    def test_default_prompt_requires_structured_outfit(self):
        schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
        system_prompt = schema["schedule_settings"]["items"]["generation_system_prompt"]["default"]
        prompt_template = schema["schedule_settings"]["items"]["prompt_template"]["default"]
        self.assertIn("outfit 必须是包含 summary 和 items 的 JSON 对象", system_prompt)
        self.assertIn("underwear", system_prompt)
        self.assertIn("outfit 必须是对象", prompt_template)


if __name__ == "__main__":
    unittest.main()
