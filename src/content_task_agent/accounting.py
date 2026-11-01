from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Any

from .contracts import Request, digest, timestamp


ACCOUNTING_VERSION = "2"


def scheduled_minutes(durations: list[int], workers: int) -> int:
    """List scheduling in request order; only independent jobs share workers."""
    slots = [0] * max(1, min(workers, len(durations) or 1))
    for duration in durations:
        slot = min(range(len(slots)), key=slots.__getitem__)
        slots[slot] += duration
    return max(slots)


def selected_scripts(plan: dict[str, Any], script_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in plan.get("scripts", [])}
    if not script_ids or len(set(script_ids)) != len(script_ids) or any(
            script_id not in by_id for script_id in script_ids):
        raise ValueError("估算必须引用真实存在且不重复的脚本 ID")
    assets = {item["id"] for item in plan.get("assets", [])}
    scripts = [by_id[script_id] for script_id in script_ids]
    for script in scripts:
        if (not script.get("content") or not script.get("asset_ids")
                or not set(script["asset_ids"]).issubset(assets)):
            raise ValueError("脚本的素材依赖缺失或内容为空")
    return scripts


def dependency_fingerprint(request: Request, catalog: dict, plan: dict,
                           script_ids: list[str]) -> str:
    scripts = selected_scripts(plan, script_ids)
    asset_ids = {key for item in scripts for key in item["asset_ids"]}
    return digest({"request": request.payload(), "catalog": catalog, "scripts": scripts,
                   "assets": sorted((item for item in plan["assets"] if item["id"] in asset_ids),
                                    key=lambda item: item["id"])})


def calculate_estimate(request: Request, catalog: dict, plan: dict, script_ids: list[str], *,
                       actual_cost_cents: int, elapsed_minutes: int, started_at: datetime,
                       concurrency: int, pending_tools: tuple[str, ...], phase: str) -> dict:
    scripts = selected_scripts(plan, script_ids)
    workers = min(concurrency, int(catalog.get("max_workers", 3)))
    formats = [catalog["formats"][item["format"]] for item in scripts]
    production_cents = sum(int(item["production_cents"]) for item in formats)
    production_minutes = scheduled_minutes([int(item["production_minutes"]) for item in formats], workers)
    pending_cents = sum(int(catalog["fees"][name]["success"]) for name in pending_tools)
    pending_minutes = sum(int(catalog["fees"][name]["minutes"]) for name in pending_tools)
    review_cents, publish_cents = int(catalog["review_cents"]), int(catalog["publish_cents"])
    total_cents = actual_cost_cents + pending_cents + production_cents + review_cents + publish_cents
    total_minutes = (elapsed_minutes + pending_minutes + production_minutes
                     + int(catalog["review_minutes"]) + int(catalog["publish_minutes"]))
    completion = started_at + timedelta(minutes=total_minutes)
    time_gap = max(0, ceil((completion - timestamp(request.deadline)).total_seconds() / 60))
    budget_gap = max(0, total_cents - request.budget_cents)
    return {
        "accounting_version": ACCOUNTING_VERSION, "phase": phase,
        "currency": catalog["currency"], "cents_per_unit": int(catalog["cents_per_unit"]),
        "format": scripts[0]["format"] if len(scripts) == 1 else "mixed",
        "script_ids": script_ids, "dependency_fingerprint": dependency_fingerprint(request, catalog, plan, script_ids),
        "planned_production_cents": production_cents, "planned_review_cents": review_cents,
        "planned_publish_cents": publish_cents, "actual_tool_cost_cents": actual_cost_cents,
        "pending_tool_cost_cents": pending_cents, "pending_tools": list(pending_tools),
        "estimated_total_cents": total_cents, "budget_cents": request.budget_cents,
        "elapsed_planning_minutes": elapsed_minutes, "pending_tool_minutes": pending_minutes,
        "production_minutes": production_minutes, "review_minutes": int(catalog["review_minutes"]),
        "publish_minutes": int(catalog["publish_minutes"]), "estimated_minutes": total_minutes,
        "estimated_remaining_minutes": total_minutes - elapsed_minutes,
        "parallel_execution": len(scripts) > 1 and workers > 1, "concurrency": workers,
        "started_at": started_at.isoformat(), "expected_completion_at": completion.isoformat(),
        "deadline": request.deadline, "feasible": budget_gap == 0 and time_gap == 0,
        "budget_gap_cents": budget_gap, "time_gap_minutes": time_gap,
    }
