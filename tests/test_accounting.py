import copy
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.content_task_agent.accounting import calculate_estimate
from src.content_task_agent.contracts import Call, Request
from src.content_task_agent.models import MockModel
from src.content_task_agent.storage import JsonStore
from src.content_task_agent.tools import ToolContext, batch_minutes, execute_calls, save_plan, ToolFailure


ROOT = Path(__file__).parents[1]
CATALOG = json.loads((ROOT / "data/tool_catalog.json").read_text(encoding="utf-8"))
ASSETS = json.loads((ROOT / "data/assets.json").read_text(encoding="utf-8"))[:2]
START = datetime(2030, 1, 15, 9, tzinfo=timezone.utc)


class AccountingTests(unittest.TestCase):
    def request(self, budget=10, minutes=300):
        return Request.parse({"task_id": "accounting", "topic": "校园环保", "budget": budget,
                              "deadline": (START + timedelta(minutes=minutes)).isoformat()})

    def plan(self):
        return {"assets": copy.deepcopy(ASSETS), "scripts": [
            {"id": "premium", "format": "premium_video", "asset_ids": [ASSETS[0]["id"]], "content": "视频草案"},
            {"id": "social", "format": "social_post", "asset_ids": [ASSETS[0]["id"]], "content": "图文草案"}],
            "events": [], "actual_cost_cents": 175}

    def quote(self, request=None, plan=None, **overrides):
        options = dict(actual_cost_cents=175, elapsed_minutes=19, started_at=START,
                       concurrency=3, pending_tools=("estimate_cost", "save_plan"), phase="quoted")
        options.update(overrides)
        return calculate_estimate(request or self.request(), CATALOG, plan or self.plan(),
                                  ["social"], **options)

    def test_exact_total_and_deadline_boundaries_include_pending_fees(self):
        estimate = self.quote(self.request(budget=9, minutes=76))
        self.assertEqual(estimate["estimated_total_cents"], 900)  # 175 + 25 + 25 + 575 + 50 + 50
        self.assertEqual(estimate["estimated_minutes"], 76)  # 19 + 1 + 1 + 30 + 15 + 10
        self.assertTrue(estimate["feasible"])
        self.assertEqual(self.quote(self.request(budget=8.99))["budget_gap_cents"], 1)
        self.assertEqual(self.quote(self.request(minutes=75))["time_gap_minutes"], 1)
        late = self.request(minutes=76)
        late = Request(late.task_id, late.topic, late.budget_cents,
                       (START + timedelta(minutes=75, seconds=59)).isoformat())
        self.assertEqual(self.quote(late)["time_gap_minutes"], 1)

    def test_selected_candidates_are_charged_and_scheduled_by_dependencies(self):
        plan = self.plan()
        options = dict(actual_cost_cents=150, elapsed_minutes=10, started_at=START,
                       pending_tools=("estimate_cost", "save_plan"), phase="quoted")
        parallel = calculate_estimate(self.request(budget=30), CATALOG, plan,
                                      ["premium", "social"], concurrency=2, **options)
        serial = calculate_estimate(self.request(budget=30), CATALOG, plan,
                                    ["premium", "social"], concurrency=1, **options)
        self.assertEqual(parallel["planned_production_cents"], 1575)
        self.assertEqual(parallel["production_minutes"], 120)
        self.assertEqual(serial["production_minutes"], 150)
        self.assertEqual(parallel["estimated_minutes"], 157)
        self.assertEqual(serial["estimated_minutes"], 187)
        self.assertEqual(self.quote(actual_cost_cents=150, elapsed_minutes=10)["estimated_total_cents"], 875)

    def test_missing_script_or_asset_dependency_is_rejected(self):
        plan = self.plan()
        plan["assets"] = []
        with self.assertRaises(ValueError):
            self.quote(plan=plan)
        plan = self.plan()
        plan["scripts"] = []
        with self.assertRaises(ValueError):
            self.quote(plan=plan)

    def test_save_rechecks_current_cost_time_and_dependency_and_persists_once(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = self.plan()
            plan["estimate"] = self.quote(self.request(budget=9))
            plan["actual_cost_cents"] = 200  # successful estimate charged its 25 cents
            context = ToolContext(self.request(budget=9), ASSETS, CATALOG, MockModel(),
                                  JsonStore(Path(directory)), 1, Path(directory), set(), START,
                                  elapsed_minutes=20, available_assets=ASSETS)
            more_cost = copy.deepcopy(plan)
            more_cost["actual_cost_cents"] = 201
            with self.assertRaises(ToolFailure) as caught:
                save_plan(context, {}, more_cost)
            self.assertEqual(caught.exception.code, "plan_not_feasible")
            self.assertFalse((Path(directory) / "plans").exists())
            changed = copy.deepcopy(plan)
            changed["scripts"][-1]["content"] = "修改后的脚本"
            with self.assertRaises(ToolFailure) as caught:
                save_plan(context, {}, changed)
            self.assertEqual(caught.exception.code, "stale_estimate")
            context.elapsed_minutes = 300
            with self.assertRaises(ToolFailure):
                save_plan(context, {}, plan)
            context.elapsed_minutes = 20
            result = save_plan(context, {}, plan)
            saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["actual_cost_cents"], 225)
            self.assertEqual(saved["estimate"]["estimated_total_cents"], 900)
            self.assertEqual(saved["estimate"]["pending_tool_cost_cents"], 0)
            self.assertEqual(saved["estimate"]["elapsed_planning_minutes"], 21)
            self.assertEqual(saved["selected_script_ids"], ["social"])

    def test_real_concurrent_generation_and_failure_time_accounting(self):
        barrier = threading.Barrier(2)

        class ConcurrentModel(MockModel):
            def generate_script(self, topic, assets, format_name):
                barrier.wait(timeout=5)  # Would fail if execution were serial.
                return super().generate_script(topic, assets, format_name)

        with tempfile.TemporaryDirectory() as directory:
            context = ToolContext(self.request(), ASSETS, CATALOG, ConcurrentModel(),
                                  JsonStore(Path(directory)), 1, Path(directory), set(), START,
                                  concurrency=2, available_assets=ASSETS)
            calls = [Call(name, "generate_script", {"asset_ids": [ASSETS[0]["id"]], "format": name})
                     for name in ("social_post", "carousel")]
            records = execute_calls(context, calls, self.plan(), 0)
            self.assertEqual([r["call_id"] for r in records], [c.id for c in calls])
            self.assertTrue(all(r["status"] == "succeeded" for r in records))
            self.assertEqual(sum(r["charged_cents"] for r in records), 100)
            self.assertEqual(batch_minutes(context, records), 8)
            context.concurrency = 1
            self.assertEqual(batch_minutes(context, records), 16)
            context.fail_tools = {"generate_script"}
            failed = execute_calls(context, calls, self.plan(), 0)
            self.assertEqual(sum(r["charged_cents"] for r in failed), 50)
            self.assertEqual(batch_minutes(context, failed), 6)
