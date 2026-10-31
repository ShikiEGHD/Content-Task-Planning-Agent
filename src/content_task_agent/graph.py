from __future__ import annotations

import copy
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .contracts import Call, Config, Decision, Request, digest, validate_call
from .models import ModelClient, ModelError
from .storage import JsonStore, StorageError
from .tools import ToolContext, ToolFailure, execute_calls
from .tracing import TraceWriter


def _summary(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {key: _summary(item, limit) for key, item in list(value.items())[:20]
                if key not in {"content"}}
    if isinstance(value, list):
        return [_summary(item, limit) for item in value[:10]]
    return value


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def initial_state(request: Request, config: Config, version: int, run_id: str,
                  trace_path: Path, assets_path: Path, catalog_path: Path,
                  fail_tools: set[str], now: datetime, client: ModelClient) -> dict[str, Any]:
    return {
        "request": request.payload(), "config": asdict(config), "version": version,
        "run_id": run_id, "trace_path": str(trace_path), "assets_path": str(assets_path),
        "catalog_path": str(catalog_path), "fail_tools": sorted(fail_tools),
        "now": now.isoformat(), "client": client, "status": "pending",
        "step": 0, "tool_calls": 0, "assets": [], "scripts": [], "estimate": None,
        "saved": None, "events": [], "errors": [], "actual_cost_cents": 0,
        "total_tool_minutes": 0, "final": None, "last_results": [], "last_decision": None,
    }


class TaskGraph:
    def __init__(self, client: ModelClient, storage: JsonStore, state_root: Path):
        self.client = client
        self.storage = storage
        self.state_root = Path(state_root)
        self.graph = self._build()

    def _build(self):
        workflow = StateGraph(dict)
        workflow.add_node("validate", self.validate_node)
        workflow.add_node("decide", self.decide_node)
        workflow.add_node("execute", self.execute_node)
        workflow.add_node("finish", self.finish_node)
        workflow.add_edge(START, "validate")
        workflow.add_conditional_edges("validate", self.after_validate,
                                       {"decide": "decide", "finish": "finish"})
        workflow.add_conditional_edges("decide", self.after_decide,
                                       {"execute": "execute", "finish": "finish"})
        workflow.add_conditional_edges("execute", self.after_execute,
                                       {"decide": "decide", "finish": "finish"})
        workflow.add_edge("finish", END)
        return workflow.compile()

    @staticmethod
    def after_validate(state):
        return "decide" if state["status"] == "running" else "finish"

    @staticmethod
    def after_decide(state):
        return "execute" if state.get("decision_calls") else "finish"

    @staticmethod
    def after_execute(state):
        return "decide" if state["status"] == "running" else "finish"

    def validate_node(self, state: dict[str, Any]) -> dict[str, Any]:
        state["status"] = "running"
        if not state["request"].get("topic"):
            state["status"] = "incomplete"
            state["final"] = {"status": state["status"], "reason_code": "missing_input",
                              "reason": "主题为空"}
            return state
        state["events"].append({"event": "validated", "status": "succeeded"})
        self._trace(state, "validate", "input_validation", "succeeded",
                    result={"topic_present": True})
        return state

    def _snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": state["status"], "request": state["request"],
            "version": state["version"], "step": state["step"],
            "tool_calls": state["tool_calls"], "assets": _summary(state["assets"]),
            "scripts": _summary(state["scripts"]), "estimate": _summary(state["estimate"]),
            "saved": state["saved"], "last_results": _summary(state["last_results"]),
            "errors": _summary(state["errors"]), "actual_cost_cents": state["actual_cost_cents"],
            "limits": {"max_steps": state["config"]["max_steps"],
                       "max_tool_calls": state["config"]["max_tool_calls"]},
        }

    def decide_node(self, state: dict[str, Any]) -> dict[str, Any]:
        if state["step"] >= state["config"]["max_steps"]:
            state["status"] = "incomplete"
            state["final"] = {"status": state["status"], "reason_code": "step_limit",
                              "reason": "达到 Agent 步数上限，保留已完成结果"}
            return state
        if state["tool_calls"] >= state["config"]["max_tool_calls"]:
            state["status"] = "incomplete"
            state["final"] = {"status": state["status"], "reason_code": "tool_call_limit",
                              "reason": "达到工具调用上限，保留已完成结果"}
            return state
        snapshot = self._snapshot(state)
        try:
            decision = self.client.decide(snapshot)
            for call in decision.calls:
                validate_call(call)
        except (ModelError, ValueError, KeyError, TypeError) as exc:
            state["status"] = "incomplete"
            state["errors"].append({"code": "model_decision_failed", "error": str(exc)})
            state["final"] = {"status": state["status"], "reason_code": "model_error",
                              "reason": str(exc)}
            self._trace(state, "model", "decide", "failed",
                        result={"error": str(exc)})
            return state
        if not decision.calls:
            state["status"] = "incomplete"
            state["final"] = {"status": state["status"], "reason_code": "model_stopped",
                              "reason": decision.stop_reason or "模型没有选择工具"}
            self._trace(state, "model", "finish_task", "succeeded",
                        reason=decision.stop_reason, result={"stopped": True})
            return state
        state["decision_calls"] = [asdict(call) for call in decision.calls]
        state["last_decision"] = [asdict(call) for call in decision.calls]
        self._trace(state, "model", "choose_action", "succeeded",
                    result={"calls": state["decision_calls"]})
        return state

    def execute_node(self, state: dict[str, Any]) -> dict[str, Any]:
        calls = [Call(**item) for item in state.pop("decision_calls", [])]
        state["step"] += 1
        if state["tool_calls"] + len(calls) > state["config"]["max_tool_calls"]:
            state["status"] = "incomplete"
            state["final"] = {"status": state["status"], "reason_code": "tool_call_limit",
                              "reason": "本批动作会超过工具调用上限"}
            return state
        request = Request.parse(state["request"])
        assets_path, catalog_path = Path(state["assets_path"]), Path(state["catalog_path"])
        context = ToolContext(
            request=request, assets_catalog=_load_json(assets_path), catalog=_load_json(catalog_path),
            generation_client=state["client"], storage=self.storage, version=state["version"],
            plan_dir=self.state_root, fail_tools=set(state["fail_tools"]),
            clock=datetime.fromisoformat(state["now"]),
            concurrency=state["config"]["concurrency"],
        )
        plan = {"request": state["request"], "version": state["version"],
                "assets": state["assets"], "scripts": state["scripts"],
                "estimate": state["estimate"], "actual_cost_cents": state["actual_cost_cents"],
                "events": state["events"]}
        try:
            results = execute_calls(context, calls, plan, state["actual_cost_cents"])
        except (ToolFailure, StorageError, ValueError) as exc:
            results = [{"call_id": "batch", "tool": "batch", "status": "failed",
                        "error_code": "batch_failed", "error": str(exc), "charged_cents": 0}]
        state["tool_calls"] += len(calls)
        state["last_results"] = []
        records_by_call_id = {record["call_id"]: record for record in results}
        for call in calls:
            record = records_by_call_id.get(call.id, {
                "call_id": call.id, "tool": call.name, "status": "failed",
                "error_code": "missing_result", "error": "工具执行未返回结果",
                "charged_cents": 0,
            })
            state["actual_cost_cents"] += record.get("charged_cents", 0)
            if record["status"] == "succeeded":
                result = record["result"]
                if call.name == "search_materials":
                    state["assets"] = result["items"]
                elif call.name == "generate_script":
                    state["scripts"].append(result)
                    state["estimate"] = None
                elif call.name == "estimate_cost":
                    state["estimate"] = result
                elif call.name == "save_plan":
                    state["saved"] = result
                elif call.name == "finish_task":
                    state["status"] = "incomplete" if state.get("saved") is None else "completed"
                    state["final"] = {"status": state["status"], "reason_code": "model_finished",
                                      "reason": result["summary"]}
            else:
                state["errors"].append({"tool": call.name, "code": record.get("error_code"),
                                        "error": record.get("error")})
            state["last_results"].append(record)
            state["events"].append(record)
            self._trace(state, "tool", call.name, record["status"],
                        reason=call.arguments.get("reason", ""), tool_args=call.arguments,
                        result=_summary(record.get("result") or {"error": record.get("error")}),
                        charged_cents=record.get("charged_cents", 0))
        if state["status"] == "running" and state.get("saved"):
            state["status"] = "running"
        return state

    def finish_node(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("final") is None:
            if state.get("saved"):
                state["status"] = "completed"
                state["final"] = {"status": "completed", "reason_code": "saved",
                                  "reason": "方案已保存"}
            elif state["status"] == "running":
                state["status"] = "incomplete"
                state["final"] = {"status": state["status"], "reason_code": "stopped",
                                  "reason": "模型停止前未保存方案"}
            else:
                state["final"] = {"status": state["status"], "reason_code": "incomplete",
                                  "reason": "任务未完成"}
        output = {
            "status": state["final"]["status"], "reason_code": state["final"]["reason_code"],
            "reason": state["final"]["reason"], "task_id": state["request"]["task_id"],
            "version": state["version"], "run_id": state["run_id"],
            "plan": {"assets": state["assets"], "scripts": state["scripts"],
                     "estimate": state["estimate"], "saved": state["saved"]},
            "actual_tool_cost_cents": state["actual_cost_cents"],
            "step_count": state["step"], "tool_call_count": state["tool_calls"],
            "errors": state["errors"],
        }
        self.storage.update(state["request"]["task_id"], state["version"], output)
        state["final_output"] = output
        return state

    def _trace(self, state: dict[str, Any], event: str, action: str, status: str,
               *, reason: str = "", tool_args: dict | None = None,
               result: dict | None = None, charged_cents: int = 0) -> None:
        TraceWriter(Path(state["trace_path"]), run_id=state["run_id"],
                    task_id=state["request"]["task_id"], mode=state["config"]["mode"]).write(
            step=state["step"], event=event, action=action, status=status, reason=reason,
            tool_args=tool_args, result_summary=result, charged_cents=charged_cents,
            next_action=state.get("last_decision", [{}])[0].get("name", "")
            if state.get("last_decision") else "")


def run_task(request: Request, config: Config, client: ModelClient, *,
             root: Path, assets_path: Path, catalog_path: Path, trace_dir: Path,
             fail_tools: set[str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    root = Path(root)
    request_fingerprint = digest(request.payload())
    run_id = uuid4().hex
    with JsonStore(root).locked() as storage:
        version, cached = storage.open_version(request.task_id, request_fingerprint)
        if cached is not None:
            cached = copy.deepcopy(cached)
            cached["cache_hit"] = True
            return cached
        now = now or datetime.now(timezone.utc)
        trace_path = Path(trace_dir) / f"{request.task_id}-v{version}-{run_id}.jsonl"
        client = client
        state = initial_state(request, config, version, run_id, trace_path,
                              assets_path, catalog_path, fail_tools or set(), now, client)
        final_state = TaskGraph(client, storage, root).graph.invoke(state)
        output = final_state["final_output"]
        output["cache_hit"] = False
        return output
