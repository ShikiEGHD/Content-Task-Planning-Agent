from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Protocol

from .contracts import Call, Decision, SCHEMAS, validate_call


class ModelError(RuntimeError):
    pass


class ModelClient(Protocol):
    model_name: str

    def decide(self, snapshot: dict[str, Any]) -> Decision: ...
    def generate_script(self, topic: str, assets: list[dict[str, Any]], format_name: str) -> str: ...


def _reason(arguments: dict[str, Any], fallback: str) -> str:
    return arguments.get("reason", fallback)


class MockModel:
    model_name = "mock-deterministic-v1"

    def decide(self, snapshot: dict[str, Any]) -> Decision:
        if snapshot.get("status") not in ("running", ""):
            return Decision(stop_reason="状态已终止")
        errors = snapshot.get("errors") or []
        if errors and errors[-1].get("tool") == "generate_script":
            return Decision([Call("mock-stop-failure", "finish_task", {
                "summary": "脚本生成工具失败，保留已成功的素材和失败记录并停止重试",
                "reason": "观察到脚本生成失败，继续重试不会产生新的可用结果",
            })])
        if snapshot.get("tool_calls", 0) == 0:
            return Decision([Call("mock-search", "search_materials", {
                "query": snapshot["request"]["topic"],
                "reason": "先取得主题相关的合成素材",
            })])
        if not snapshot.get("assets"):
            return Decision([Call("mock-stop", "finish_task", {
                "summary": "没有找到可用素材，无法生成方案",
                "reason": "观察到素材检索没有返回可用资料",
            })])
        if not snapshot.get("scripts"):
            return Decision([Call("mock-premium", "generate_script", {
                "asset_ids": [asset["id"] for asset in snapshot["assets"][:2]],
                "format": "premium_video",
                "reason": "先尝试内容质量较高的精制视频方案",
            })])
        if not snapshot.get("estimate"):
            return Decision([Call("mock-estimate", "estimate_cost", {
                "script_ids": [snapshot["scripts"][-1]["id"]],
                "reason": "根据初始方案计算费用和并行工期",
            })])
        estimate = snapshot["estimate"]
        if not estimate.get("feasible", False):
            has_social = any(s.get("format") == "social_post" for s in snapshot["scripts"])
            if not has_social:
                return Decision([Call("mock-social", "generate_script", {
                    "asset_ids": [asset["id"] for asset in snapshot["assets"][:2]],
                    "format": "social_post",
                    "reason": "观察到精制视频预算或工期不可行，尝试较低成本且较快的图文短帖",
                })])
            return Decision([Call("mock-stop-budget", "finish_task", {
                "summary": "替代内容形式仍不可行",
                "reason": "观察到替代方案仍不满足预算或截止时间",
            })])
        if not snapshot.get("saved"):
            return Decision([Call("mock-save", "save_plan", {
                "reason": "估算通过，保存可执行方案",
            })])
        return Decision([Call("mock-finish", "finish_task", {
            "summary": "方案已保存",
            "reason": "观察到方案已保存且所有约束通过",
        })])

    def generate_script(self, topic: str, assets: list[dict[str, Any]], format_name: str) -> str:
        labels = {"premium_video": "精制视频", "social_post": "图文短帖", "carousel": "多图轮播"}
        joined = "；".join(asset["title"] for asset in assets)
        return (
            f"主题：{topic}\n形式：{labels.get(format_name, format_name)}\n"
            f"素材：{joined}\n开场：提出一个与主题相关的具体问题。\n"
            "主体：引用合成素材中的事实，给出可执行的小步骤。\n"
            "结尾：邀请用户自愿参与并注明内容为活动建议。"
        )


class DeepSeekClient:
    """Official OpenAI Python SDK client configured for DeepSeek's API."""

    def __init__(self, model: str = "deepseek-flash", generation_model: str = "deepseek-flash",
                 api_key: str | None = None, base_url: str = "https://api.deepseek.com"):
        self.model_name = model
        self.generation_model = generation_model
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("MODEL_API_KEY")
        self.base_url = base_url
        if not self.api_key:
            raise ModelError("cloud 模式需要 DEEPSEEK_API_KEY 或 MODEL_API_KEY；未读取或打印密钥")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ModelError("cloud 模式需要安装 openai；离线 mock 不需要该依赖") from exc
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    @staticmethod
    def _message_dict(message: Any) -> dict[str, Any]:
        if hasattr(message, "model_dump"):
            return message.model_dump()
        if isinstance(message, dict):
            return message
        return {"content": getattr(message, "content", None),
                "tool_calls": getattr(message, "tool_calls", None)}

    def decide(self, snapshot: dict[str, Any]) -> Decision:
        system = (
            "你是内容任务规划 Agent。根据当前真实状态选择下一步工具。"
            "只能调用提供的工具；工具结果可能失败或超预算。"
            "优先保留成功结果，观察后再调整计划。完成只能在保存成功后。"
            "每个工具调用参数必须带 reason，reason 只写一句可核验依据，不写内部推理。"
            "预算币种固定为模拟单位，100分=1模拟单位，不是人民币、美元或API账单。"
            "estimate_cost的script_ids是最终实际执行的脚本，选多个会合计多个制作费；"
            "候选方案应分别估算，不要把候选全选当作仅执行最后一个。"
            "估算包含已经发生的全部工具费、本次估算费和待保存费用，以及制作、审核、发布费。"
            "只能使用已检索素材；估算、保存、结束必须单独调用；保存成功后只能结束。"
        )
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False, default=str)},
            ],
            tools=self._tool_specs(),
            tool_choice="auto",
            stream=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        message = response.choices[0].message
        message_data = self._message_dict(message)
        calls = []
        for raw in message_data.get("tool_calls") or []:
            call_data = raw if isinstance(raw, dict) else self._message_dict(raw)
            function = call_data.get("function") or {}
            name = function.get("name")
            args = function.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError as exc:
                    raise ModelError("云端模型返回的工具参数不是 JSON") from exc
            call = Call(call_data.get("id", "cloud-call"), name, args)
            validate_call(call)
            calls.append(call)
        if calls:
            return Decision(calls=calls)
        return Decision(stop_reason=(message_data.get("content") or "模型没有选择工具"))

    def generate_script(self, topic: str, assets: list[dict[str, Any]], format_name: str) -> str:
        response = self.client.chat.completions.create(
            model=self.generation_model,
            messages=[
                {"role": "system", "content": "你是内容脚本生成工具，只输出可审阅的短脚本，不编造素材中的事实。"},
                {"role": "user", "content": json.dumps({
                    "topic": topic, "format": format_name, "assets": assets
                }, ensure_ascii=False)},
            ],
            stream=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ModelError("脚本生成模型返回空内容")
        return content.strip()

    @staticmethod
    def _tool_specs() -> list[dict]:
        from .contracts import native_tools
        return native_tools()
