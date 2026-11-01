# 内容任务规划 Agent

题号：2。仓库：https://github.com/ShikiEGHD/Content-Task-Planning-Agent

帮助内容运营人员在预算与截止时间内形成方案；无法完成时，保留成功结果并说明失败或缺口。
Python + LangGraph，CLI，DeepSeek 云端 API 与离线 Mock，四个本地业务工具。

## 当前状态

计算版本 v2 已修正费用、依赖工期、保存守卫和中文文件编码：18 项 unittest、15 个实际离线 CLI 场景通过。
正常降本场景总额为 **9.00 个模拟单位，工期 76 分钟**。
用户已完成两条 v2 DeepSeek 实测：正常方案 8.75 模拟单位、67 分钟；持续故障保留素材并由调用上限停止。17 个 CLI 场景均已复核。故障模型持续重试，没有主动结束，属于已知限制。
验收入口见 [评估与证据](docs/03-evaluation.md)，原始选型比较见 [路线](docs/00-roadmap.md)。

## 环境与安装

已验证 Windows、Python 3.12.2、LangGraph 1.2.10、OpenAI Python SDK 3.42.0。
在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\src"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
```

运行期间 Mock 不请求网络或读取密钥；首次安装依赖需要网络。直接使用虚拟环境解释器，无需更改 PowerShell 执行策略。

## 离线运行

```powershell
.\.venv\Scripts\python.exe -m content_task_agent --input examples/inputs/normal.json --mode mock --runtime-dir runtime/demo-normal --now "2030-01-15T09:00:00+08:00" --output runtime/normal.json
```

主题、budget（模拟单位）、带时区 deadline 为业务输入，task_id 为任务标识。
`--output` 直接写入 UTF-8 JSON，输入错误也会写文件。请用该参数保存结果，避免旧 PowerShell `>` 的 GBK/UTF-16 转换。
控制台若仍乱码，请按上面的 UTF-8 设置；文件用编辑器按 UTF-8 打开，不要把 GBK 原文件强行保存为 UTF-8 冒充修复。

持续工具故障、缺字段、执行上限：

```powershell
.\.venv\Scripts\python.exe -m content_task_agent --input examples/inputs/tool-failure.json --mode mock --fail-tool generate_script --runtime-dir runtime/demo-failure --output runtime/failure.json
.\.venv\Scripts\python.exe -m content_task_agent --input examples/inputs/missing-budget.json --mode mock --output runtime/missing-budget.json
.\.venv\Scripts\python.exe -m content_task_agent --input examples/inputs/normal.json --mode mock --max-steps 1 --runtime-dir runtime/demo-limit --output runtime/limit.json
```

成功退出 0，未完成退出 2。故障的 incomplete 是预期业务结果。上述 `--fail-tool` 使该工具每次调用失败，不是仅失败一次。

## 测试与完整证据重跑

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/run_evidence.py --mode mock
.\.venv\Scripts\python.exe scripts/check_evidence.py
```

15 个 CLI 场景包括正常、缺预算、缺截止时间、失败、素材不足、时间不足、精确预算、差 1 分预算、步数上限、调用上限、幂等和自动版本。
每批使用独立 runtime 和 Trace 目录，最终文件在 `examples/outputs/`，完整命令、退出码、run_id、哈希和 Trace 链接位于 `examples/test-results/mock-evidence.json`。
相同 task_id/有效输入复用结果，输入或计算规则变化自动版本；不会复用旧算法缓存。

## DeepSeek 云端运行

Key 仅在本机配置，不放入代码、聊天、视频或 Git。`.env.example` 是变量说明，程序不会自动读取 .env。
本机设置 `DEEPSEEK_API_KEY` 后：

```powershell
.\.venv\Scripts\python.exe -m content_task_agent --input examples/inputs/normal.json --mode cloud --model deepseek-flash --generation-model deepseek-flash --base-url https://api.deepseek.com --runtime-dir runtime/cloud-v2 --output runtime/cloud-normal.json
```

官方文档推荐的 OpenAI SDK 直接连接 DeepSeek，使用原生 tool calls，关闭思考模式。
CLI 参数优先于 `DEEPSEEK_MODEL`、`DEEPSEEK_GENERATION_MODEL`、`DEEPSEEK_BASE_URL` 环境变量，默认模型均为 deepseek-flash。
完整重跑正常和故障两条路径（会真实消耗 API 额度）：

```powershell
.\.venv\Scripts\python.exe scripts/run_evidence.py --mode cloud
.\.venv\Scripts\python.exe scripts/check_evidence.py
```

脚本生成 `cloud-normal.json`、`cloud-failure.json` 和 `cloud-evidence.json`。如果模型未完成或不满足核验条件，脚本如实记录失败，不宣称通过。

## 计算与状态边界

100 分 = 1 模拟单位，和真实 API 账单分开。
当前估算包含实际已发生工具费、后续必需工具费、最终选定脚本的制作费、审核费和发布费；报价不是已花费用。
候选草案保留，最终执行集合由 script_ids 指定；选择多个就合计多个制作费。保存前重新验证预算、截止时间和依赖。

独立脚本生成实际通过线程池并发，状态/索引串行更新。工期使用费用表模拟分钟累计依赖任务，同批独立任务按并发度排程；真实执行秒另记，不用 sleep 模拟业务。
制作/审核/发布只做估算，不对外发布。JSON 进程锁和原子写入用于幂等，不支持跨进程失败步骤续跑。

## 目录与评审顺序

- `src/content_task_agent/`：contracts、accounting、tools、models、graph、storage、tracing、cli。
- `tests/`：输入契约、计算临界值、并发、保存守卫、UTF-8、缓存版本。
- `scripts/`：实际 CLI 证据重跑和编码/哈希核验。
- `data/`：合成素材、成功/失败费用及模拟分钟。
- `examples/`：输入、UTF-8 输出、实际 Trace、保存方案、清单和测试记录。
- [需求](docs/01-requirements.md)、[方案](docs/02-solution.md)、[评估](docs/03-evaluation.md)、[AI 使用](docs/04-ai-usage.md)、[复盘](docs/05-retrospective.md)。

旧顶层 `runtime-*` 和 Trace 是历史材料，当前验收以 v2 清单为准；不能把旧云端预算数字当作已修正结果。

## 最终提交

提交题号 2、公开仓库 URL `https://github.com/ShikiEGHD/Content-Task-Planning-Agent`，以及完成推送后通过 `git rev-parse HEAD` 获取的完整 SHA。公开仓库可直接供评审读取。
[个人贡献与 AI 分工](docs/04-ai-usage.md)及[学习复盘](docs/05-retrospective.md)已依据本人说明整理；实际投入时间未精确记录，不补写未经确认的数字。
代码、文档及验证材料均在本仓库，评审入口为 [评估与证据](docs/03-evaluation.md)。
