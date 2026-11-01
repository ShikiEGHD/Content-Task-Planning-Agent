"""Run real CLI processes; write reproducible evidence as UTF-8 without shell redirection."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
NOW = "2030-01-15T09:00:00+08:00"


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_hash(scheme: str = "normalized-v2") -> str:
    fingerprint = hashlib.sha256()
    if scheme == "raw-v1":
        for path in sorted((ROOT / "src").rglob("*.py")):
            fingerprint.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
            fingerprint.update(path.read_bytes())
        fingerprint.update((ROOT / "data/tool_catalog.json").read_bytes())
        return fingerprint.hexdigest()
    if scheme != "normalized-v2":
        raise ValueError("Unknown source hash scheme: " + scheme)
    paths = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "data").glob("*.json"))
    for path in paths:
        fingerprint.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        # Git's autocrlf must not invalidate otherwise identical evidence.
        fingerprint.update(path.read_text(encoding="utf-8").encode("utf-8"))
    return fingerprint.hexdigest()


def output_hash(path: Path, scheme: str = "utf8-lf-v1") -> str:
    raw = path.read_bytes() if scheme == "raw-v1" else path.read_text(encoding="utf-8").encode("utf-8")
    if scheme not in {"raw-v1", "utf8-lf-v1"}:
        raise ValueError("Unknown output hash scheme: " + scheme)
    return hashlib.sha256(raw).hexdigest()


def scenarios(mode: str):
    if mode == "cloud":
        return [
            ("cloud-normal", "normal.json", "cloud-normal", [], 0, "completed"),
            ("cloud-failure", "tool-failure.json", "cloud-failure", ["--fail-tool", "generate_script"], 2, "incomplete"),
        ]
    return [
        ("normal", "normal.json", "normal", [], 0, "completed"),
        ("normal-venv", "normal.json", "normal-venv", [], 0, "completed"),
        ("tool-failure", "tool-failure.json", "failure", ["--fail-tool", "generate_script"], 2, "incomplete"),
        ("tool-failure-verified", "tool-failure.json", "failure-verified", ["--fail-tool", "generate_script"], 2, "incomplete"),
        ("missing-budget", "missing-budget.json", "missing-budget", [], 2, "incomplete"),
        ("missing-deadline", "missing-deadline.json", "missing-deadline", [], 2, "incomplete"),
        ("no-materials", "no-materials.json", "no-materials", [], 2, "incomplete"),
        ("tight-deadline", "tight-deadline.json", "tight-deadline", ["--now", "2030-01-15T17:00:00+08:00"], 2, "incomplete"),
        ("budget-exact", "budget-exact.json", "budget-exact", [], 0, "completed"),
        ("budget-short", "budget-short.json", "budget-short", [], 2, "incomplete"),
        ("step-limit", "normal.json", "step-limit", ["--max-steps", "1"], 2, "incomplete"),
        ("tool-call-limit", "normal.json", "tool-call-limit", ["--max-tool-calls", "1"], 2, "incomplete"),
        ("idempotency-first", "normal.json", "idempotency", [], 0, "completed"),
        ("idempotency-second", "normal.json", "idempotency", [], 0, "completed"),
        ("idempotency-version2", "normal-changed.json", "idempotency", [], 0, "completed"),
    ]


def verify_run(result: dict, expected_status: str, name: str, mode: str) -> None:
    assert result["status"] == expected_status, (name, result)
    if "accounting_version" not in result:
        assert name in {"missing-budget", "missing-deadline"}, (name, result)
        assert result["reason_code"] == "input_or_setup_error", result
        return
    assert result["accounting_version"] == "2", result
    trace_path = ROOT / result["trace_path"]
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert sum(item["charged_cents"] for item in records) == result["actual_tool_cost_cents"]
    if name in {"tool-failure", "tool-failure-verified", "cloud-failure"}:
        assert result["plan"]["assets"] and result["plan"]["saved"] is None
        assert any(error["tool"] == "generate_script" and error["code"] == "injected_failure"
                   for error in result["errors"]), result
        assert any(item["event"] == "tool" and item["action"] == "generate_script"
                   and item["status"] == "failed" for item in records)
        assert result["reason_code"] in {"model_finished", "model_stopped", "tool_call_limit", "step_limit"}
        if result["reason_code"] == "model_finished":
            assert records[-1]["event"] == "tool" and records[-1]["action"] == "finish_task"
    if result["status"] == "completed":
        estimate = result["plan"]["estimate"]
        assert estimate["pending_tool_cost_cents"] == 0
        assert estimate["actual_tool_cost_cents"] == result["actual_tool_cost_cents"]
        assert estimate["estimated_total_cents"] == (
            result["actual_tool_cost_cents"] + estimate["planned_production_cents"]
            + estimate["planned_review_cents"] + estimate["planned_publish_cents"])
        assert estimate["budget_gap_cents"] == estimate["time_gap_minutes"] == 0
        saved = json.loads((ROOT / result["plan"]["saved"]["path"]).read_text(encoding="utf-8"))
        assert saved["estimate"] == estimate
        assert saved["actual_cost_cents"] == result["actual_tool_cost_cents"]
    if name == "idempotency-second":
        assert result["cache_hit"] is True and result["version"] == 1
    elif name == "idempotency-version2":
        assert result["cache_hit"] is False and result["version"] == 2
    if mode == "mock" and name in {"normal", "normal-venv", "budget-exact", "idempotency-first"}:
        assert result["plan"]["estimate"]["estimated_total_cents"] == 900
        assert result["plan"]["estimate"]["estimated_minutes"] == 76
        assert [r["result_summary"]["feasible"] for r in records
                if r["event"] == "tool" and r["action"] == "estimate_cost"] == [False, True]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock", "cloud"), default="mock")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if args.mode == "cloud" and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("MODEL_API_KEY")):
        print("cloud requires a local DEEPSEEK_API_KEY; never send it in chat.", file=sys.stderr)
        return 2
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    runtime_base = Path("examples") / "runs" / (args.mode + "-" + batch)
    trace_dir = Path("examples/traces") / (args.mode + "-" + batch)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")
    runs = []
    failed = False
    for name, source, runtime_name, extra, expected_exit, expected_status in scenarios(args.mode):
        output = Path("examples/outputs") / (name + ".json")
        command = [sys.executable, "-m", "content_task_agent", "--input", "examples/inputs/" + source,
                   "--mode", args.mode, "--runtime-dir", str(runtime_base / runtime_name),
                   "--trace-dir", str(trace_dir), "--now", NOW, "--output", str(output), *extra]
        proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=900)
        log_base = ROOT / "examples/test-results" / (args.mode + "-" + batch)
        log_base.mkdir(parents=True, exist_ok=True)
        for suffix, raw in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            raw.decode("utf-8")  # Validate without a second Windows newline conversion.
            (log_base / (name + "." + suffix + ".txt")).write_bytes(raw)
        result = json.loads((ROOT / output).read_text(encoding="utf-8"))
        try:
            assert proc.returncode == expected_exit, (proc.returncode, proc.stderr.decode("utf-8"))
            verify_run(result, expected_status, name, args.mode)
            verified = True
        except AssertionError as exc:
            verified, failed = False, True
            print(f"verification failed: {name}: {exc}", file=sys.stderr)
        runs.append({"name": name, "command": command, "input": "examples/inputs/" + source,
                     "output": output.as_posix(), "exit_code": proc.returncode,
                     "status": result["status"], "reason_code": result["reason_code"],
                     "run_id": result.get("run_id"), "trace": result.get("trace_path"),
                     "verified": verified, "expected_exit_code": expected_exit,
                     "output_sha256": output_hash(ROOT / output)})
        print(f"{name}: exit={proc.returncode} status={result['status']} verified={verified}")
    git_head = subprocess.run(["git", "-c", "safe.directory=" + ROOT.as_posix(), "rev-parse", "HEAD"],
                              cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": args.mode,
                "accounting_version": "2", "source_sha256": source_hash(),
                "source_hash_scheme": "normalized-v2",
                "output_hash_scheme": "utf8-lf-v1",
                "base_commit": git_head, "python": sys.version,
                "dependencies": {name: importlib.metadata.version(name) for name in ("langgraph", "openai")},
                "fixed_now": NOW, "runtime_root": runtime_base.as_posix(),
                "all_verified": not failed, "runs": runs}
    write(ROOT / "examples/test-results" / (args.mode + "-evidence.json"), manifest)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
