# 内容任务规划 Agent

题号：2。仓库：https://github.com/ShikiEGHD/Content-Task-Planning-Agent

帮助运营人员在预算和截止时间内完成一份内容活动方案，交付任务计划、步骤状态、工具结果、费用合计、已保存方案或明确的未完成原因。

## 当前状态

已完成 P1—P3 的离线实现和证据生成，并已用 DeepSeek `deepseek-flash` 完成一条真实云端正常路径：LangGraph 图、离线 Mock、受控工具、JSON 幂等存储、CLI、Trace 与 unittest。云端故障/替代路径还需第二次真实 API 运行。

用户已确认的技术栈：

- 主要语言：Python；后续有明确需要时再引入其他语言。
- 框架：LangGraph。
- 运行方式：CLI。
- 模型模式：云端 API 与离线 Mock；云端采用 OpenAI Python SDK 直连 DeepSeek。
- 已确认模型：`deepseek-flash`，通过 `extra_body={"thinking": {"type": "disabled"}}` 关闭思考模式。
- 失败调用按独立失败费率计费；独立脚本调用可并发，估算工期使用并行关键路径。
- 相同 `task_id` 和输入复用已完成版本，输入变化自动创建新版本。

推荐先阅读 [实施路线](docs/00-roadmap.md)，再阅读 [需求与验收](docs/01-requirements.md)。模型供应商、SDK/协议、持久化方案等未定事项需由用户比较后决定。

## 目录

```text
.
├── README.md
├── requirements.txt                 # 已固定 LangGraph/OpenAI SDK 版本
├── .env.example                     # DeepSeek 云端配置示例（不含密钥）
├── .gitignore
├── src/
│   └── content_task_agent/          # 后续放 CLI、LangGraph 图、模型与工具接入
├── tests/                          # unittest 测试
├── examples/
│   ├── inputs/                     # 可复现输入场景
│   ├── outputs/                    # 实际运行输出
│   ├── traces/                     # JSONL 执行记录
│   └── test-results/               # 测试结果
├── data/                           # 合成素材、工具费用和耗时表
└── docs/
    ├── 00-roadmap.md
    ├── 01-requirements.md
    ├── 02-solution.md
    ├── 03-evaluation.md
    ├── 04-ai-usage.md
    └── 05-retrospective.md
```

## 环境与安装

已验证环境：Python 3.12.2、LangGraph 1.2.10、OpenAI SDK 3.42.0。云端路径的 SDK 已可导入；真实 DeepSeek 请求仍需要用户本机提供 API Key 和可用网络。

推荐安装方式：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

实现完成后的 README 必须补齐：

1. 经验证的 Python 版本、Windows 环境和依赖版本。
2. 创建虚拟环境与安装依赖的完整命令。
3. 云端模式的配置、运行与失败说明。
4. 不使用密钥、无需网络的离线 Mock 运行命令。
5. 测试命令、故障注入方式与至少三组复现入口。
6. 输出文件和 Trace 的位置、评审入口与已知限制。

## 运行与评审入口

入口为 `python -m content_task_agent`。离线运行示例：

```powershell
$env:PYTHONPATH="src"
python -m content_task_agent --input examples/inputs/normal.json --mode mock
python -m unittest discover -s tests -v
```

评审阅读顺序：

1. docs/01-requirements.md：任务范围与验收标准。
2. docs/02-solution.md：LangGraph 状态、模型决策与程序校验。
3. docs/03-evaluation.md：预期、实际结果及证据链接。
4. examples/：运行后保存的输入、实际输出、Trace、测试结果。
5. docs/04-ai-usage.md 与 docs/05-retrospective.md：AI 使用、个人验证和复盘。

离线 Mock 只用于流程、边界和复现；它不作为真实模型动态决策效果的证据。云端模式需要 `DEEPSEEK_API_KEY`，本仓库不保存密钥。

## 提交

最终提交题号 2、仓库 URL 与最终 commit SHA。私有仓库须让指定评审账号可读。所有代码、文档、合成数据和验证材料放在同一仓库，不提交真实密钥或隐私数据。

建议投入约 3 小时是考题的建议，不代表当前已经投入或完成；实际工时及未完成部分填写在 docs/05-retrospective.md。
