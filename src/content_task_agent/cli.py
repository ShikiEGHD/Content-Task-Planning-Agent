from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .contracts import Config, Request, timestamp
from .graph import run_task
from .models import DeepSeekClient, MockModel, ModelError
from .storage import JsonStore, StorageError


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="内容任务规划 Agent（LangGraph CLI）")
    command.add_argument("--input", required=True, help="输入 JSON 文件")
    command.add_argument("--output", help="直接保存 UTF-8 JSON 结果（含输入错误），无需终端重定向")
    command.add_argument("--mode", choices=("mock", "cloud"), default=os.getenv("AGENT_MODE", "mock"))
    command.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                         help="云端决策模型名")
    command.add_argument("--generation-model", default=os.getenv("DEEPSEEK_GENERATION_MODEL", "deepseek-flash"),
                         help="云端脚本生成模型名")
    command.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                         help="云端 API 地址")
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
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    args = parser().parse_args(argv)
    try:
        request = Request.parse(json.loads(Path(args.input).read_text(encoding="utf-8-sig")))
        config = Config(mode=args.mode, model=args.model, generation_model=args.generation_model,
                        max_steps=args.max_steps,
                        max_tool_calls=args.max_tool_calls, concurrency=args.concurrency)
        now = timestamp(args.now) if args.now else None
        if args.mode == "cloud":
            client = DeepSeekClient(model=args.model, generation_model=args.generation_model,
                                    base_url=args.base_url)
        else:
            client = MockModel()
        output = run_task(
            request, config, client, root=Path(args.runtime_dir),
            assets_path=Path(args.data_dir) / "assets.json",
            catalog_path=Path(args.data_dir) / "tool_catalog.json",
            trace_dir=Path(args.trace_dir), fail_tools=set(args.fail_tool), now=now)
        emit(output, args.output)
        return 0 if output["status"] == "completed" else 2
    except (OSError, ValueError, ModelError, StorageError, KeyError) as exc:
        output = {"status": "incomplete", "reason_code": "input_or_setup_error", "reason": str(exc)}
        try:
            emit(output, args.output, error=True)
        except OSError as write_error:
            print(json.dumps({**output, "output_error": str(write_error)}, ensure_ascii=False), file=sys.stderr)
        return 2


def emit(output: dict, path: str | None, *, error: bool = False) -> None:
    if path:
        JsonStore.write(Path(path), output)
    print(json.dumps(output, ensure_ascii=False, indent=2), file=sys.stderr if error else sys.stdout)
