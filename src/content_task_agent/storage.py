from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class StorageError(Exception):
    pass


class JsonStore:
    """Single-process concurrency, fail-fast cross-process lock, atomic JSON replacement."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.index = {"schema_version": 1, "tasks": {}}

    @staticmethod
    def write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise StorageError("runtime 正被其他进程使用，或上次异常退出留下锁；核对后再清理") from None
        os.close(fd)
        try:
            if self.index_path.exists():
                try:
                    self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
                    if self.index.get("schema_version") != 1 or not isinstance(self.index["tasks"], dict):
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    raise StorageError("幂等索引损坏，未覆盖原文件") from None
            yield self
        finally:
            lock.unlink(missing_ok=True)

    def open_version(self, task_id: str, fingerprint: str) -> tuple[int, dict | None]:
        versions = self.index["tasks"].setdefault(task_id, [])
        for item in versions:
            if item["fingerprint"] == fingerprint:
                result = copy.deepcopy(item["result"])
                if result is None:
                    result = {"status": "incomplete", "reason_code": "interrupted",
                              "reason": "上次执行中断，禁止自动重复副作用；不支持跨进程续跑"}
                return item["version"], result
        version = max((item["version"] for item in versions), default=0) + 1
        versions.append({"version": version, "fingerprint": fingerprint, "result": None})
        self.write(self.index_path, self.index)
        return version, None

    def update(self, task_id: str, version: int, result: dict) -> None:
        record = next(x for x in self.index["tasks"][task_id] if x["version"] == version)
        record["result"] = copy.deepcopy(result)
        self.write(self.index_path, self.index)

    def save(self, task_id: str, version: int, plan: dict) -> str:
        target = self.root / "plans" / task_id / ("v" + str(version) + ".json")
        if target.exists():
            raise StorageError("该版本方案已存在，拒绝覆盖")
        self.write(target, plan)
        return target.as_posix()
