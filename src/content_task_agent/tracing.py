from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceWriter:
    def __init__(self, path: Path, *, run_id: str, task_id: str, mode: str):
        self.path = Path(path)
        self.run_id = run_id
        self.task_id = task_id
        self.mode = mode
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, *, step: int, event: str, action: str, status: str,
              reason: str = "", tool_args: dict | None = None,
              result_summary: dict | None = None, charged_cents: int = 0,
              next_action: str = "") -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id, "task_id": self.task_id, "mode": self.mode,
            "step": step, "event": event, "action": action, "status": status,
            "tool_args": tool_args or {}, "result_summary": result_summary or {},
            "charged_cents": charged_cents, "next_action": next_action,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
