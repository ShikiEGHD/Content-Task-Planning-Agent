from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("时间必须为带时区的 ISO 8601 字符串")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return parsed


@dataclass(frozen=True)
class Request:
    task_id: str
    topic: str
    budget_cents: int
    deadline: str

    @classmethod
    def parse(cls, raw: Any) -> "Request":
        if not isinstance(raw, dict):
            raise ValueError("输入必须是 JSON 对象")
        missing = [key for key in ("task_id", "topic", "budget", "deadline")
                   if key not in raw or raw[key] is None or raw[key] == ""]
        if missing:
            raise ValueError("缺少必填字段: " + ", ".join(missing))
        if set(raw) - {"task_id", "topic", "budget", "deadline"}:
            raise ValueError("输入包含不支持的字段")
        if not isinstance(raw["task_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw["task_id"]):
            raise ValueError("task_id 只允许 1—64 位字母、数字、下划线和连字符")
        if not isinstance(raw["topic"], str) or not raw["topic"].strip() or len(raw["topic"]) > 500:
            raise ValueError("topic 必须为 1—500 字符")
        try:
            amount = Decimal(str(raw["budget"]))
            if isinstance(raw["budget"], bool) or not amount.is_finite() or amount <= 0:
                raise ValueError("budget 必须为正数")
            if amount * 100 != (amount * 100).to_integral_value():
                raise ValueError("budget 最多两位小数")
            if amount > 1_000_000:
                raise ValueError("budget 超过本演示上限")
        except InvalidOperation:
            raise ValueError("budget 必须为有效金额") from None
        due = timestamp(raw["deadline"]).isoformat()
        return cls(raw["task_id"], raw["topic"].strip(), int(amount * 100), due)

    def payload(self) -> dict:
        # Keep the external request shape (budget in user-facing units) in graph state;
        # internal accounting remains integer cents.
        return {
            "task_id": self.task_id,
            "topic": self.topic,
            "budget": self.budget_cents / 100,
            "deadline": self.deadline,
        }


@dataclass(frozen=True)
class Call:
    id: str
    name: str
    arguments: dict

    def payload(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    calls: list[Call] = field(default_factory=list)
    stop_reason: str = ""


@dataclass(frozen=True)
class Config:
    mode: str = "mock"
    model: str = "deepseek-flash"
    generation_model: str = "deepseek-flash"
    max_steps: int = 12
    max_tool_calls: int = 12
    concurrency: int = 3

    def __post_init__(self):
        if self.mode not in {"mock", "cloud"}:
            raise ValueError("mode 必须为 mock 或 cloud")
        for value in (self.max_steps, self.max_tool_calls, self.concurrency):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100:
                raise ValueError("步数、工具上限、并发度必须为 1—100 的整数")
        if not self.model or not self.generation_model:
            raise ValueError("模型名不能为空")


SCHEMAS = {
    "search_materials": {
        "description": "检索本地合成素材。返回实际匹配的素材，空结果不能假设成功。",
        "properties": {"query": {"type": "string"}},
    },
    "generate_script": {
        "description": "调用生成模型，根据已有素材生成一个内容草案。独立草案可同时调用。",
        "properties": {
            "asset_ids": {"type": "array", "items": {"type": "string"}},
            "format": {"type": "string", "enum": ["premium_video", "social_post", "carousel"]},
        },
    },
    "estimate_cost": {
        "description": "选定已有脚本估算活动执行成本、并行工期、预算/时间缺口；报价不直接扣费。",
        "properties": {"script_ids": {"type": "array", "items": {"type": "string"}}},
    },
    "save_plan": {
        "description": "独立调用。仅保存最近估算通过且依赖有效的方案；不得与其他工具同批。",
        "properties": {},
    },
    "finish_task": {
        "description": "停止任务并给出未完成原因。保存成功时也可结束。不得与其他动作同批。",
        "properties": {"summary": {"type": "string"}},
    },
}


def native_tools() -> list[dict]:
    tools = []
    for name, spec in SCHEMAS.items():
        props = {**spec["properties"], "reason": {
            "type": "string", "description": "一两句动作依据，仅给结论，不输出内部思维链"}}
        tools.append({"type": "function", "function": {
            "name": name, "description": spec["description"],
            "parameters": {"type": "object", "properties": props,
                           "required": list(props), "additionalProperties": False}}})
    return tools


def validate_call(call: Call) -> None:
    if call.name not in SCHEMAS or not isinstance(call.arguments, dict):
        raise ValueError("未知工具或参数不是对象")
    required = set(SCHEMAS[call.name]["properties"]) | {"reason"}
    if set(call.arguments) != required:
        raise ValueError("工具字段不匹配")
    reason = call.arguments["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        raise ValueError("reason 必须为 1—300 字符的简短依据")
    for name in ("query", "summary"):
        if name in call.arguments:
            value = call.arguments[name]
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise ValueError(name + " 必须为有效字符串")
    for name in ("asset_ids", "script_ids"):
        if name in call.arguments:
            values = call.arguments[name]
            if (not isinstance(values, list) or not 1 <= len(values) <= 8
                    or any(not isinstance(v, str) or not v or len(v) > 100 for v in values)
                    or len(values) != len(set(values))):
                raise ValueError(name + " 必须为不重复的 1—8 个 ID")
    if "format" in call.arguments and call.arguments["format"] not in (
            "premium_video", "social_post", "carousel"):
        raise ValueError("未知内容形式")
