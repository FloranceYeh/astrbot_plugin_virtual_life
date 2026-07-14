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


if __name__ == "__main__":
    unittest.main()
