import unittest
from datetime import datetime

from src.content_task_agent.contracts import Call, Config, Request, native_tools, validate_call


class ContractTests(unittest.TestCase):
    def test_request_requires_timezone_and_converts_money(self):
        request = Request.parse({
            "task_id": "task-1", "topic": "校园环保", "budget": 10,
            "deadline": "2030-01-15T18:00:00+08:00",
        })
        self.assertEqual(request.budget_cents, 1000)
        with self.assertRaises(ValueError):
            Request.parse({"task_id": "task-1", "topic": "校园环保",
                           "budget": 10, "deadline": "2030-01-15T18:00:00"})

    def test_unknown_fields_and_missing_input_are_rejected(self):
        with self.assertRaises(ValueError):
            Request.parse({"task_id": "task-1", "topic": "x", "budget": 1})
        with self.assertRaises(ValueError):
            Request.parse({"task_id": "task-1", "topic": "x", "budget": 1,
                           "deadline": "2030-01-15T18:00:00+08:00", "extra": 1})

    def test_tool_schema_requires_reason(self):
        self.assertEqual(len(native_tools()), 5)
        with self.assertRaises(ValueError):
            validate_call(Call("x", "finish_task", {"summary": "停止"}))

    def test_config_limits(self):
        with self.assertRaises(ValueError):
            Config(max_steps=0)
