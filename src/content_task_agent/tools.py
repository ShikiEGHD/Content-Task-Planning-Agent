from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .accounting import calculate_estimate, dependency_fingerprint, scheduled_minutes
from .contracts import Call, Request, digest
from .models import ModelClient, ModelError
from .storage import JsonStore, StorageError


class ToolFailure(RuntimeError):
    def __init__(self, message: str, *, code: str = "tool_failed"):
        super().__init__(message)
        self.code = code


@dataclass
class ToolContext:
    request: Request
    assets_catalog: list[dict[str, Any]]
    catalog: dict[str, Any]
    generation_client: ModelClient
    storage: JsonStore
    version: int
    plan_dir: Path
    fail_tools: set[str]
    clock: datetime
    concurrency: int = 3
    elapsed_minutes: int = 0
    available_assets: list[dict[str, Any]] = field(default_factory=list)

    def fee(self, name: str, success: bool) -> int:
        key = "success" if success else "failure"
        return int(self.catalog["fees"][name][key])

    def minutes(self, name: str, success: bool) -> int:
        key = "minutes" if success else "failure_minutes"
        return int(self.catalog["fees"][name][key])


def _fail_if_injected(context: ToolContext, name: str) -> None:
    if name in context.fail_tools:
        raise ToolFailure(f"故障注入：{name}", code="injected_failure")


def search_materials(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = "search_materials"
    _fail_if_injected(context, name)
    query = args["query"].lower()
    terms = [term for term in query.replace("，", " ").replace(",", " ").split() if term]
    matches = [item for item in context.assets_catalog
               if query in item["title"].lower()
               or any(term in item["title"].lower() for term in terms)
               or any(term in tag.lower() for term in terms for tag in item["tags"])
               or any(tag.lower() in query for tag in item["tags"])]
    if not matches:
        raise ToolFailure("没有匹配的合成素材", code="no_materials")
    return {"items": matches, "count": len(matches), "query": args["query"]}


def generate_script(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = "generate_script"
    asset_by_id = {item["id"]: item for item in context.available_assets}
    if any(asset_id not in asset_by_id for asset_id in args["asset_ids"]):
        raise ToolFailure("脚本生成引用了未检索到的素材", code="missing_assets")
    assets = [asset_by_id[asset_id] for asset_id in args["asset_ids"]]
    if not assets:
        raise ToolFailure("脚本生成缺少有效素材", code="missing_assets")
    _fail_if_injected(context, name)
    try:
        content = context.generation_client.generate_script(
            context.request.topic, assets, args["format"])
    except (ModelError, TimeoutError) as exc:
        raise ToolFailure(str(exc), code="generation_failed") from exc
    script_id = f"{args['format']}-{digest({'assets': args['asset_ids'], 'content': content})[:16]}"
    return {"id": script_id, "format": args["format"], "asset_ids": args["asset_ids"],
            "content": content, "generator": getattr(context.generation_client, "generation_model",
                                                     context.generation_client.model_name)}


def estimate_cost(context: ToolContext, args: dict[str, Any], plan: dict[str, Any],
                  actual_cost_cents: int) -> dict[str, Any]:
    _fail_if_injected(context, "estimate_cost")
    try:
        return calculate_estimate(
            context.request, context.catalog, plan, args["script_ids"],
            actual_cost_cents=actual_cost_cents, elapsed_minutes=context.elapsed_minutes,
            started_at=context.clock, concurrency=context.concurrency,
            pending_tools=("estimate_cost", "save_plan"), phase="quoted")
    except ValueError as exc:
        raise ToolFailure(str(exc), code="invalid_dependencies") from exc


def save_plan(context: ToolContext, args: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    name = "save_plan"
    _fail_if_injected(context, name)
    estimate = plan.get("estimate") or {}
    if not estimate.get("feasible"):
        raise ToolFailure("当前估算不满足预算或截止时间，拒绝保存", code="plan_not_feasible")
    try:
        script_ids = estimate["script_ids"]
        if estimate.get("dependency_fingerprint") != dependency_fingerprint(
                context.request, context.catalog, plan, script_ids):
            raise ToolFailure("方案依赖已变化，必须重新估算", code="stale_estimate")
        current = calculate_estimate(
            context.request, context.catalog, plan, script_ids,
            actual_cost_cents=plan["actual_cost_cents"], elapsed_minutes=context.elapsed_minutes,
            started_at=context.clock, concurrency=context.concurrency,
            pending_tools=("save_plan",), phase="save_guard")
    except (ValueError, KeyError) as exc:
        raise ToolFailure(str(exc), code="invalid_dependencies") from exc
    if not current["feasible"]:
        raise ToolFailure("保存前重算发现预算或时间超限，必须重新规划", code="plan_not_feasible")
    # Successful persistence includes this save's fee and duration once, not twice.
    current["actual_tool_cost_cents"] += current["pending_tool_cost_cents"]
    current["elapsed_planning_minutes"] += current["pending_tool_minutes"]
    current["pending_tool_cost_cents"] = current["pending_tool_minutes"] = 0
    current["pending_tools"] = []
    current["phase"] = "saved"
    current["estimated_remaining_minutes"] = current["estimated_minutes"] - current["elapsed_planning_minutes"]
    saved_plan = copy.deepcopy(plan)
    saved_plan.update(estimate=current, actual_cost_cents=current["actual_tool_cost_cents"],
                      elapsed_planning_minutes=current["elapsed_planning_minutes"],
                      selected_script_ids=script_ids)
    saved_plan["events"].append({"tool": "save_plan", "status": "succeeded",
                                 "charged_cents": context.fee(name, True),
                                 "duration_minutes": context.minutes(name, True)})
    try:
        path = context.storage.save(context.request.task_id, context.version, saved_plan)
    except StorageError as exc:
        raise ToolFailure(str(exc), code="save_failed") from exc
    return {"saved": True, "path": path, "version": context.version, "estimate": current}


def execute_call(context: ToolContext, call: Call, plan: dict[str, Any],
                 actual_cost_cents: int) -> tuple[dict[str, Any], int]:
    if call.name == "search_materials":
        result = search_materials(context, call.arguments)
    elif call.name == "generate_script":
        result = generate_script(context, call.arguments)
    elif call.name == "estimate_cost":
        result = estimate_cost(context, call.arguments, plan, actual_cost_cents)
    elif call.name == "save_plan":
        result = save_plan(context, call.arguments, plan)
    elif call.name == "finish_task":
        result = {"finished": True, "summary": call.arguments["summary"]}
    else:
        raise ToolFailure(f"不支持的工具: {call.name}", code="unknown_tool")
    return result, context.fee(call.name, True) if call.name in context.catalog["fees"] else 0


def execute_calls(context: ToolContext, calls: list[Call], plan: dict[str, Any],
                  actual_cost_cents: int) -> list[dict[str, Any]]:
    if any(call.name in {"estimate_cost", "save_plan", "finish_task"} for call in calls) and len(calls) > 1:
        return [_rejected(call, "估算、保存或结束动作必须单独执行") for call in calls]
    if len(calls) <= 1:
        results = []
        for call in calls:
            results.append(_execute_record(context, call, plan, actual_cost_cents))
        return results
    # Only independent generation calls are run concurrently. The state update remains serialized.
    if not all(call.name == "generate_script" for call in calls):
        return [_rejected(call, "同批只允许并发生成独立脚本") for call in calls]
    results: list[dict[str, Any]] = []
    max_workers = min(len(calls), context.concurrency, context.catalog.get("max_workers", 3))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_execute_record, context, call, plan, actual_cost_cents): call for call in calls}
        for future in as_completed(futures):
            results.append(future.result())
    by_id = {record["call_id"]: record for record in results}
    return [by_id[call.id] for call in calls]


def _rejected(call: Call, message: str) -> dict:
    return {"call_id": call.id, "tool": call.name, "status": "failed",
            "error_code": "unsafe_batch", "error": message,
            "charged_cents": 0, "duration_minutes": 0, "wall_seconds": 0}


def batch_minutes(context: ToolContext, records: list[dict]) -> int:
    return scheduled_minutes([record.get("duration_minutes", 0) for record in records],
                             min(context.concurrency, context.catalog.get("max_workers", 3)))


def _execute_record(context: ToolContext, call: Call, plan: dict[str, Any],
                    actual_cost_cents: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result, _ = execute_call(context, call, plan, actual_cost_cents)
        charged = context.fee(call.name, True) if call.name in context.catalog["fees"] else 0
        return {"call_id": call.id, "tool": call.name, "status": "succeeded",
                "result": result, "charged_cents": charged,
                "duration_minutes": context.minutes(call.name, True) if call.name in context.catalog["fees"] else 0,
                "wall_seconds": round(time.perf_counter() - started, 6)}
    except ToolFailure as exc:
        charged = context.fee(call.name, False) if call.name in context.catalog["fees"] else 0
        return {"call_id": call.id, "tool": call.name, "status": "failed",
                "error_code": exc.code, "error": str(exc), "charged_cents": charged,
                "duration_minutes": context.minutes(call.name, False) if call.name in context.catalog["fees"] else 0,
                "wall_seconds": round(time.perf_counter() - started, 6)}
