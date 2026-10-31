# 03 评估、实际结果与限制

## 当前状态

离线业务代码、Mock 运行和 unittest 已执行；DeepSeek 云端模型尚未使用真实密钥验证，因此云端路径仍明确标为未完成。

## 验收映射

| 编号 | 场景 | 预期 | 实际结果 | 输入/输出/Trace/测试文件 |
|---|---|---|---|---|
| R01 | 正常完成 | 符合预算、时间与依赖并保存 | 通过（Mock） | `examples/outputs/normal.json`、`examples/traces/normal-001-v1-3fe32edc34d4458e9f623c7616981d4a.jsonl` |
| R02 | 缺预算 | 返回缺失字段，不补值 | 通过（CLI 输入校验） | `examples/test-results/missing-budget.stderr` |
| R03 | 缺截止时间 | 返回缺失字段，不补值 | 未运行 | 待生成 |
| R04 | 一次工具失败 | 模型调整或停止，保留成功结果 | 通过（Mock 故障注入；失败计费并停止重试） | `examples/outputs/tool-failure.json`、`examples/traces/failure-001-v1-d5e25ac75f4f4130a8dae7b64d6b027f.jsonl` |
| R05 | 超预算可替代/无可行 | 改方案重估或返回缺口 | 通过（正常材料中从 premium_video 重规划为 social_post） | `examples/outputs/normal.json` |
| R06 | 重复执行 | 不重复业务副作用/计费 | 通过（unittest 覆盖 JSON 幂等与版本） | `tests/test_storage.py`、`tests/test_graph_mock.py` |
| R07 | 达到上限 | 明确未完成并终止 | 通过（故障场景最终为 step_limit） | `examples/outputs/tool-failure.json` |
| R08 | 素材不足 | 识别不足，换方案或停止 | 未运行 | 待生成 |
| R09 | 截止时间不可达 | 换方案或报告时间缺口 | 未运行 | 待生成 |
| R10 | 两条动态路径 | 真实模型因不同工具结果改变行动 | 未完成：云端 API 尚未验证；Mock 路径已验证 | `examples/outputs/normal.json`、`examples/outputs/tool-failure.json` |

需求编号见 [01-requirements.md](01-requirements.md)。

## 材料组织

当前已保存正常、工具失败和缺预算三组材料；缺预算属于输入错误，故没有业务 Trace。重复执行由 unittest 覆盖，尚未单独保存 CLI 输出。

实际生成后，从本文件链接到 ../examples/inputs/、../examples/outputs/、../examples/traces/、../examples/test-results/ 的具体文件，不以截图或视频替代代码。

## 评估记录格式

每次评估填写：场景、运行模式、命令、运行环境和版本、模型名称、run_id、task_id、预期、实际结果、退出码、通过/失败、证据路径、限制。

默认离线测试不得依赖网络与密钥；云端测试单列。固定 Mock 回答不证明模型能力。至少两条云端实际路径应展示模型对超预算或失败返回的调整。

## 失败与限制

限制：DeepSeek 云端 API 尚未用真实密钥验证；OpenAI SDK 3.42.0 已写入 requirements，但本次环境安装未完成。Mock 证据不能替代真实模型路径；并行执行目前仅允许同批独立脚本生成，跨进程恢复未实现。
