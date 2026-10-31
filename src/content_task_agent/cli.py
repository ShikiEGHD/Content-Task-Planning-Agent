from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .contracts import Config, Request
from .graph import run_task
from .models import DeepSeekClient, MockModel, ModelError


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="内容任务规划 Agent（LangGraph CLI）")
    command.add_argument("--input", required=True, help="输入 JSON 文件")
    command.add_argument("--mode", choices=("mock", "cloud"), default=os.getenv("AGENT_MODE", "mock"))
    command.add_argument("--data-dir", default="data")
    command.add_argument("--runtime-dir", default="runtime")
    command.add_argument("--trace-dir", default="examples/traces")
    command.add_argument("--fail-tool", action="append", default=[],
                         choices=("search_materials", "generate_script", "estimate_cost", "save_plan"))
    command.add_argument("--max-steps", type=int, default=int(os.getenv("AGENT_MAX_STEPS", "12")))
    command.add_argument("--max-tool-calls", type=int,
                         default=int(os.getenv("AGENT_MAX_TOOL_CALLS", "12")))
    command.add_argument("--concurrency", type=int, default=3)
    command.add_argument("--now", help="固定当前时间（带时区 ISO 8601），便于复现")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        request = Request.parse(json.loads(Path(args.input).read_text(encoding="utf-8")))
        config = Config(mode=args.mode, max_steps=args.max_steps,
                        max_tool_calls=args.max_tool_calls, concurrency=args.concurrency)
        if args.mode == "cloud":
            client = DeepSeekClient()
        else:
            client = MockModel()
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
        output = run_task(
            request, config, client, root=Path(args.runtime_dir),
            assets_path=Path(args.data_dir) / "assets.json",
            catalog_path=Path(args.data_dir) / "tool_catalog.json",
            trace_dir=Path(args.trace_dir), fail_tools=set(args.fail_tool), now=now)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output["status"] == "completed" else 2
    except (OSError, ValueError, ModelError, KeyError) as exc:
        print(json.dumps({"status": "incomplete", "reason_code": "input_or_setup_error",
                          "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
