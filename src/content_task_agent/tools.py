from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .contracts import Call, Request, timestamp
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
    asset_by_id = {item["id"]: item for item in context.assets_catalog}
    assets = [asset_by_id[asset_id] for asset_id in args["asset_ids"] if asset_id in asset_by_id]
    if not assets:
        raise ToolFailure("脚本生成缺少有效素材", code="missing_assets")
    _fail_if_injected(context, name)
    try:
        content = context.generation_client.generate_script(
            context.request.topic, assets, args["format"])
    except (ModelError, TimeoutError) as exc:
        raise ToolFailure(str(exc), code="generation_failed") from exc
    script_id = f"{args['format']}-{len(content)}-{assets[0]['id']}"
    return {"id": script_id, "format": args["format"], "asset_ids": args["asset_ids"],
            "content": content, "generator": context.generation_client.model_name}


def _parse_plan_format(scripts: list[dict[str, Any]]) -> str:
    if not scripts:
        raise ToolFailure("没有可估算的脚本", code="missing_script")
    # The latest successful script is the active candidate. A lower-cost replacement can supersede it.
    return scripts[-1]["format"]


def _critical_path_minutes(format_name: str, catalog: dict[str, Any], parallel: bool) -> int:
    format_item = catalog["formats"][format_name]
    production = int(format_item["production_minutes"])
    review = int(catalog["review_minutes"])
    publish = int(catalog["publish_minutes"])
    if parallel:
        # Asset search and script generation are independent preparation tasks; their max duration
        # is included before the dependent production/review/publish chain.
        preparation = max(int(catalog["fees"]["search_materials"]["minutes"]),
                          int(catalog["fees"]["generate_script"]["minutes"]))
        return preparation + production + review + publish
    return (int(catalog["fees"]["search_materials"]["minutes"])
            + int(catalog["fees"]["generate_script"]["minutes"])
            + production + review + publish)


def estimate_cost(context: ToolContext, args: dict[str, Any], scripts: list[dict[str, Any]],
                  actual_cost_cents: int) -> dict[str, Any]:
    name = "estimate_cost"
    _fail_if_injected(context, name)
    selected = [script for script in scripts if script["id"] in args["script_ids"]]
    format_name = _parse_plan_format(selected)
    format_item = context.catalog["formats"][format_name]
    planned_cents = int(format_item["production_cents"]) + int(context.catalog["review_minutes"])
    planned_cents += int(context.catalog["publish_minutes"])
    total_estimated = actual_cost_cents + planned_cents
    minutes = _critical_path_minutes(format_name, context.catalog, parallel=True)
    deadline = timestamp(context.request.deadline)
    feasible = total_estimated <= context.request.budget_cents and (
        context.clock + timedelta(minutes=minutes) <= deadline)
    return {
        "format": format_name, "script_ids": args["script_ids"],
        "planned_production_cents": int(format_item["production_cents"]),
        "actual_tool_cost_cents": actual_cost_cents,
        "estimated_total_cents": total_estimated,
        "estimated_minutes": minutes, "parallel_execution": True,
        "deadline": context.request.deadline, "feasible": feasible,
        "budget_gap_cents": max(total_estimated - context.request.budget_cents, 0),
        "time_gap_minutes": max(int((context.clock + timedelta(minutes=minutes) - deadline).total_seconds() // 60), 0),
    }


def save_plan(context: ToolContext, args: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    name = "save_plan"
    _fail_if_injected(context, name)
    estimate = plan.get("estimate") or {}
    if not estimate.get("feasible"):
        raise ToolFailure("当前估算不满足预算或截止时间，拒绝保存", code="plan_not_feasible")
    try:
        path = context.storage.save(context.request.task_id, context.version, plan)
    except StorageError as exc:
        raise ToolFailure(str(exc), code="save_failed") from exc
    return {"saved": True, "path": path, "version": context.version}


def execute_call(context: ToolContext, call: Call, plan: dict[str, Any],
                 actual_cost_cents: int) -> tuple[dict[str, Any], int]:
    if call.name == "search_materials":
        result = search_materials(context, call.arguments)
    elif call.name == "generate_script":
        result = generate_script(context, call.arguments)
    elif call.name == "estimate_cost":
        result = estimate_cost(context, call.arguments, plan.get("scripts", []), actual_cost_cents)
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
        raise ToolFailure("估算、保存或结束动作必须单独执行", code="unsafe_batch")
    if len(calls) <= 1:
        results = []
        for call in calls:
            results.append(_execute_record(context, call, plan, actual_cost_cents))
        return results
    # Only independent generation calls are run concurrently. The state update remains serialized.
    if not all(call.name == "generate_script" for call in calls):
        raise ToolFailure("同批只允许并发生成脚本", code="unsafe_batch")
    results: list[dict[str, Any]] = []
    max_workers = min(len(calls), context.concurrency, context.catalog.get("max_workers", 3))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_execute_record, context, call, plan, actual_cost_cents): call for call in calls}
        for future in as_completed(futures):
            results.append(future.result())
    return results


def _execute_record(context: ToolContext, call: Call, plan: dict[str, Any],
                    actual_cost_cents: int) -> dict[str, Any]:
    try:
        result, _ = execute_call(context, call, plan, actual_cost_cents)
        charged = context.fee(call.name, True) if call.name in context.catalog["fees"] else 0
        return {"call_id": call.id, "tool": call.name, "status": "succeeded",
                "result": result, "charged_cents": charged}
    except ToolFailure as exc:
        charged = context.fee(call.name, False) if call.name in context.catalog["fees"] else 0
        return {"call_id": call.id, "tool": call.name, "status": "failed",
                "error_code": exc.code, "error": str(exc), "charged_cents": charged}
