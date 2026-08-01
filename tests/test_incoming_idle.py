import os
import sys
import unittest
from types import SimpleNamespace

from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.regex import RegexFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from astrbot_plugin_virtual_life.main import ProactiveVirtualDailyPlugin


class FakeEvent:
    def __init__(self, parsed_params=None, activated_handlers=None):
        self._extras = {}
        if parsed_params is not None:
            self._extras["handlers_parsed_params"] = parsed_params
        if activated_handlers is not None:
            self._extras["activated_handlers"] = activated_handlers

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)


def plugin_instance():
    return object.__new__(ProactiveVirtualDailyPlugin)


def command_handler():
    return SimpleNamespace(event_filters=[CommandFilter(command_name="状态")])


def regex_handler():
    return SimpleNamespace(event_filters=[RegexFilter(r"签到")])


class IncomingIdleTests(unittest.TestCase):
    def test_plain_message_is_conversation(self):
        plugin = plugin_instance()
        event = FakeEvent(parsed_params=None, activated_handlers=[])
        self.assertTrue(plugin._is_conversation_event(event))

    def test_message_with_extra_handlers_is_conversation(self):
        plugin = plugin_instance()
        event = FakeEvent(
            parsed_params=None,
            activated_handlers=[SimpleNamespace(event_filters=[])],
        )
        self.assertTrue(plugin._is_conversation_event(event))

    def test_command_with_parsed_params_is_not_conversation(self):
        plugin = plugin_instance()
        event = FakeEvent(parsed_params={"handler": {}})
        self.assertFalse(plugin._is_conversation_event(event))

    def test_command_handler_in_activated_is_not_conversation(self):
        plugin = plugin_instance()
        event = FakeEvent(parsed_params=None, activated_handlers=[command_handler()])
        self.assertFalse(plugin._is_conversation_event(event))

    def test_command_group_handler_is_not_conversation(self):
        plugin = plugin_instance()
        handler = SimpleNamespace(
            event_filters=[CommandGroupFilter(group_name="虚拟人生")]
        )
        event = FakeEvent(parsed_params=None, activated_handlers=[handler])
        self.assertFalse(plugin._is_conversation_event(event))

    def test_regex_handler_is_not_conversation(self):
        plugin = plugin_instance()
        event = FakeEvent(parsed_params=None, activated_handlers=[regex_handler()])
        self.assertFalse(plugin._is_conversation_event(event))

    def test_absent_extras_treated_as_conversation(self):
        plugin = plugin_instance()
        event = FakeEvent()
        self.assertTrue(plugin._is_conversation_event(event))


if __name__ == "__main__":
    unittest.main()
