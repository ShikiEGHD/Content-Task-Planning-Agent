import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.content_task_agent.contracts import Config, Request
from src.content_task_agent.graph import run_task
from src.content_task_agent.models import MockModel


ROOT = Path(__file__).parents[1]
DATA = ROOT / "data"


class GraphMockTests(unittest.TestCase):
    def request(self, task_id="graph-test", budget=10, topic="校园环保"):
        return Request.parse({"task_id": task_id, "topic": topic, "budget": budget,
                              "deadline": "2030-01-15T18:00:00+08:00"})

    def run_case(self, request, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            return run_task(request, Config(), MockModel(), root=output_dir / "runtime",
                            assets_path=DATA / "assets.json",
                            catalog_path=DATA / "tool_catalog.json",
                            trace_dir=output_dir / "traces",
                            now=datetime(2030, 1, 1, tzinfo=timezone.utc), **kwargs)

    def test_budget_replans_from_premium_to_social_and_saves(self):
        output = self.run_case(self.request())
        self.assertEqual(output["status"], "completed")
        self.assertEqual(output["plan"]["estimate"]["format"], "social_post")
        self.assertGreaterEqual(len(output["plan"]["scripts"]), 2)
        self.assertFalse(output["cache_hit"])
        self.assertEqual(output["actual_tool_cost_cents"], 225)
        self.assertEqual(output["plan"]["estimate"]["estimated_total_cents"], 900)
        self.assertEqual(output["plan"]["estimate"]["estimated_minutes"], 76)

    def test_tool_failure_is_recorded_and_not_success(self):
        output = self.run_case(self.request("fail-test"), fail_tools={"search_materials"})
        self.assertEqual(output["status"], "incomplete")
        self.assertEqual(output["reason_code"], "model_finished")
        self.assertTrue(any(error["code"] == "injected_failure" for error in output["errors"]))

    def test_changed_request_creates_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = {"topic": "校园环保", "budget": 10, "deadline": "2030-01-15T18:00:00+08:00"}
            first = run_task(Request.parse({"task_id": "versioned", **base}), Config(), MockModel(),
                             root=root / "runtime", assets_path=DATA / "assets.json",
                             catalog_path=DATA / "tool_catalog.json",
                             trace_dir=root / "traces",
                             now=datetime(2030, 1, 1, tzinfo=timezone.utc))
            second = run_task(Request.parse({"task_id": "versioned", **{**base, "budget": 20}}),
                              Config(), MockModel(), root=root / "runtime",
                              assets_path=DATA / "assets.json", catalog_path=DATA / "tool_catalog.json",
                              trace_dir=root / "traces",
                              now=datetime(2030, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(first["version"], 1)
            self.assertEqual(second["version"], 2)

    def test_legacy_cache_is_not_reused_and_new_result_is_idempotent(self):
        from src.content_task_agent.contracts import digest
        from src.content_task_agent.storage import JsonStore

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request("legacy")
            with JsonStore(root / "runtime").locked() as store:
                version, _ = store.open_version(request.task_id, digest(request.payload()))
                store.update(request.task_id, version, {"status": "completed", "obsolete": True})
            kwargs = dict(root=root / "runtime", assets_path=DATA / "assets.json",
                          catalog_path=DATA / "tool_catalog.json", trace_dir=root / "traces",
                          now=datetime(2030, 1, 1, tzinfo=timezone.utc))
            current = run_task(request, Config(), MockModel(), **kwargs)
            traces = list((root / "traces").glob("*.jsonl"))
            cached = run_task(request, Config(), MockModel(), **kwargs)
            self.assertEqual(current["version"], 2)
            self.assertFalse(current["cache_hit"])
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(cached["actual_tool_cost_cents"], current["actual_tool_cost_cents"])
            self.assertEqual(traces, list((root / "traces").glob("*.jsonl")))
            plan = json.loads(Path(current["plan"]["saved"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(plan["actual_cost_cents"], current["actual_tool_cost_cents"])

    def test_step_limit_stops_before_second_tool_and_preserves_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = run_task(self.request("limit"), Config(max_steps=1), MockModel(),
                              root=root / "runtime", assets_path=DATA / "assets.json",
                              catalog_path=DATA / "tool_catalog.json", trace_dir=root / "traces")
            self.assertEqual(output["reason_code"], "step_limit")
            self.assertEqual(output["actual_tool_cost_cents"], 50)
            self.assertEqual(output["elapsed_planning_minutes"], 2)
            self.assertTrue(output["plan"]["assets"])
            self.assertIsNone(output["plan"]["saved"])
