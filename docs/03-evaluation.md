# 03 评估、实际结果与限制

## 当前验收版本

2026-11-01 修正费用、依赖工期和输出编码，当前计算版本为 `accounting_version=2`。
18 项 unittest、15 个实际 Mock CLI 场景通过；用户在本机运行的 2 个 DeepSeek 云端场景已离线复核。结果见 [测试记录](../examples/test-results/unittest-final.txt)、[离线清单](../examples/test-results/mock-evidence.json) 和 [云端清单](../examples/test-results/cloud-evidence.json)。
清单记录命令、输入/输出、退出码、run_id、Trace 路径、依赖版本、源码和输出 SHA-256。`base_commit` 是重跑前提交，源码哈希用于确认重跑代码；不能将它当作新证据的最终提交 SHA。

旧版 DeepSeek 正常和故障记录已保留；它们证明当时的真实接口和决策运行，但旧正常记录的费用/工期算法有误，不能用作 v2 的预算时间验收。v2 云端正常/故障由用户在本机执行，当前结果不依赖旧版材料。

## 验收映射

下表的输入均位于 [inputs](../examples/inputs/)，完整命令和对应 Trace 见场景清单 `runs` 数组。

| 编号 | 场景 | 本次实际结果 | v2 证据 |
|---|---|---|---|
| R01 | 正常完成 | completed；总额 900 分，工具费 225 分；工期 76 分钟；保存文件与输出一致 | [normal](../examples/outputs/normal.json) |
| R02 | 缺预算 | incomplete；明确缺少 budget；退出码 2；输出不再为空 | [missing-budget](../examples/outputs/missing-budget.json) |
| R03 | 缺截止时间 | incomplete；明确缺少 deadline；退出码 2 | [missing-deadline](../examples/outputs/missing-deadline.json) |
| R04 | 工具失败 | 脚本失败后 Mock 停止；素材保留、未保存；检索成功费 50 + 失败费 25 = 75 分 | [tool-failure](../examples/outputs/tool-failure.json) |
| R05 | 超预算与精确边界 | premium_video 不可行后换 social_post；9.00 可保存，8.99 缺 1 分且拒绝保存 | [budget-exact](../examples/outputs/budget-exact.json)、[budget-short](../examples/outputs/budget-short.json) |
| R06 | 幂等和自动版本 | 相同有效输入复用 v1，费用/保存不重复；预算变化创建 v2；旧算法缓存不复用 | [first](../examples/outputs/idempotency-first.json)、[second](../examples/outputs/idempotency-second.json)、[version2](../examples/outputs/idempotency-version2.json) |
| R07 | 步数/调用上限 | 明确 step_limit/tool_call_limit，素材保留且无保存；退出码 2 | [step-limit](../examples/outputs/step-limit.json)、[tool-call-limit](../examples/outputs/tool-call-limit.json) |
| R08 | 素材不足 | 无匹配素材，失败费 10 分，未编造脚本或保存 | [no-materials](../examples/outputs/no-materials.json) |
| R09 | 截止时间不可达 | 固定 17:00 起算，降本后需 76 分钟，17:30 截止仍缺 46 分钟，拒绝保存 | [tight-deadline](../examples/outputs/tight-deadline.json) |
| R10 | 真实模型动态决策 | v2 正常在非法并批估算被拒后改为单次估算并保存；故障时重试、重新检索，最终上限停止，无伪造成功 | [cloud-normal](../examples/outputs/cloud-normal.json)、[cloud-failure](../examples/outputs/cloud-failure.json)、[清单](../examples/test-results/cloud-evidence.json) |
| E01 | 编码 | outputs 下全部 17 个 JSON 均为 UTF-8、非空、无 U+FFFD 损坏字符；GBK 环境变量下 CLI 仍写 UTF-8 | [编码回归测试](../tests/test_cli_encoding.py) |
| E02 | 并发与保存守卫 | 两个线程通过 Barrier 验证实际并发；多脚本制作费用合计；保存前费用、时间、内容/素材变更可拒绝 | [计算测试](../tests/test_accounting.py) |

## v2 计算复核

正常 Mock 实际费用：检索 50 + 两次生成 100 + 两次估算 50 + 保存 25 = 225 分。
最终只执行图文脚本，制作 575 + 审核 50 + 发布 50 = 675 分；合计 900 分（9.00 个模拟单位）。
旧视频报价不会与最终制作费相加，但已经执行过的生成/估算费用必须保留。

工期：检索 2 + 视频生成 8 + 首次估算 1 + 图文生成 8 + 再估算 1 + 保存 1 + 图文制作 30 + 审核 15 + 发布 10 = 76 分钟。
费用和时间临界值测试包含“恰好通过”和“差 1 分/1 秒不通过”，时间缺口向上取整到分钟。

## 本次真实云端证据（v2）

用户在本机执行 cloud-20261101T030800Z-e50097bb 批次，正常与故障均使用 deepseek-flash。

- 正常：检索后并发生成图文和轮播；同批两次估算被 unsafe_batch 拒绝（未执行、未收费），模型观察后改为只估算图文并保存。工具费 50 + 100 + 25 + 25 = 200 分，制作/审核/发布 675 分，合计 875 分；规划 12 + 制作审核发布 55 = 67 分钟。保存与最终输出完全一致。
- 故障：3 次检索成功、9 次脚本生成故障，3×50 + 9×25 = 375 分；规划累计 30 分钟。模型调整参数、重新检索后仍重试，达到 12 次工具调用上限返回 incomplete，素材保留，脚本和保存为空。这证明失败记录与上限保护，不证明模型能主动识别持续故障并结束。
- 初版核验规则错误地强制故障路径必须调用 finish_task，导致初次清单 all_verified=false。现按 R07 允许上限终止，用 --revalidate 逐项复核原退出码、输出哈希、Trace 费用、保存文件和源码；原始清单保留于 [initial-manifest](../examples/test-results/cloud-20261101T030800Z-e50097bb/initial-manifest.json)。没有重新请求 API，也没有改写输出。
- 哈希方案在清单显式记录；按 UTF-8/LF 规范化，避免 Git autocrlf 改变换行而误报；最初原始哈希仍保留在 initial-manifest。

## 历史云端证据（v1，不用于当前费用/工期验收）

- 正常：[索引](../examples/runtime-cloud/index.json)、[Trace](../examples/traces/normal-001-v1-94f7b0b329734f8596c00543551ed0fd.jsonl)。DeepSeek 同批生成两个候选再估算保存，未出现超预算观察，不能称为云端超预算重规划。
- 故障：[索引](../examples/runtime-cloud-failure/index.json)、[Trace](../examples/traces/failure-001-v1-901f9651efab4ecc905a546f58ab4138.jsonl)。6 次生成故障，2 次检索，合计 250 分，最终模型选择结束。此处模拟分不是美分。
- 其他旧 `examples/runtime-*` 和顶层 `traces/*.jsonl` 也属于 v1 历史记录；当前验收只引用 v2 清单里的文件。

历史 Trace 不改写成新算法输出。旧顶层 outputs 已由本次真实 CLI 运行重新生成，Git 历史仍保留原文件。

## 可复制的重跑方式

```powershell
.\.venv\Scripts\python.exe scripts/run_evidence.py --mode mock
.\.venv\Scripts\python.exe scripts/check_evidence.py
```

本机已配置 `DEEPSEEK_API_KEY` 后执行以下命令，会真实请求 API，产生真实供应商费用；不要把 Key 发到聊天中。

```powershell
.\.venv\Scripts\python.exe scripts/run_evidence.py --mode cloud
.\.venv\Scripts\python.exe scripts/check_evidence.py
```

脚本为每批生成独立 runtime 和 Trace 目录，避免复用旧成功缓存；cloud 输出为 `cloud-normal.json`、`cloud-failure.json`，清单为 `cloud-evidence.json`。
云端决策不保证每次选择相同动作；验证失败会在清单中记为失败，不预写通过。已有输出可用 scripts/check_evidence.py --revalidate 离线复核并保留原始清单；它不会执行模型请求。

## 已知限制

- 工具/模型实际调用可并发，素材到脚本、估算到保存均存在依赖；同批只允许独立脚本生成并发。
- 工期是费用表上的业务模拟分钟，不是实际等待；工具记录 `wall_seconds`，总执行记录 `execution_wall_seconds`，API 延迟单独观察，不冒充模拟工时。
- 制作、审核和发布仅估算，不真正发布活动；云端 token 账单不计入业务模拟预算，也未声称已统计真实 token 费用。
- 持久化为本地 JSON + 进程锁，不支持跨进程失败步骤恢复；相同失败输入也复用已有记录，重新尝试可选独立 runtime。
- 本次真实模型面对持续故障未主动停止，靠调用上限终止；不将其描述为自动恢复成功。
- 原 `--fail-tool` 是持续故障，不是仅失败一次。真实一次性故障后成功续跑不在本次新增范围。
