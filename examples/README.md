# 实际输入、输出和 Trace

当前验收采用计算版本 v2（2026-11-01）。入口为 [离线清单](test-results/mock-evidence.json) 和 [云端清单](test-results/cloud-evidence.json)，记录 15 个 Mock + 2 个用户实测 cloud CLI 命令、退出码、run_id、Trace 和哈希。
当前输出位于 outputs/，全部是程序 --output 直接写出的 UTF-8 JSON；缺字段结果也非空。

- inputs/：正常、缺预算/截止时间、工具失败、素材不足、紧迫截止时间、预算精确/差 1 分及版本变化。
- outputs/：当前实际结果；normal 与 normal-venv 为兼容旧文件名的独立运行，tool-failure 与 tool-failure-verified 同理。
- runs/mock-批次/：该批真实保存方案和索引；同一 idempotency 子目录复用以验证缓存和版本。
- traces/mock-批次/：实际 JSONL；模型 reason、工具返回和累计费用/分钟可核验。
- test-results/：测试文本、场景清单、该批 CLI stdout/stderr。

v1 历史材料：旧 runtime-*（包括 runtime-cloud 和 runtime-cloud-failure）、traces 顶层旧 JSONL。
其中旧云端正常记录有计算错误，保留供追溯，不能用来证明 v2 预算/工期通过。历史云端摘要中“美元/美分”为错误单位，实际规则为模拟单位。

重跑：`.venv/Scripts/python.exe scripts/run_evidence.py --mode mock`；核验：`.venv/Scripts/python.exe scripts/check_evidence.py`。
本机配置 Key 后可用 --mode cloud 重跑。当前云端正常 875 分/67 分钟；故障 375 分/30 规划分钟、12 次调用上限。初次核验清单保留在对应批次的 initial-manifest.json；离线复核修正核验规则，没有修改原输出或调用 API。
哈希按 UTF-8/LF 规范化后计算，避免 Git autocrlf 造成跨平台误报。
