# 可执行测试

框架：unittest。当前 18 项测试通过，原始结果在 examples/test-results/unittest-final.txt。

- test_contracts：必填字段、金额精度、时区和工具 schema。
- test_accounting：费用字段、完整总额、预算/时间临界值、多脚本计费/工期、未知依赖、保存前成本/内容/时间变化、实际线程并发和失败计费/耗时。
- test_cli_encoding：GBK 环境变量下 stdout 和 --output 仍为 UTF-8；BOM 输入；缺字段错误非空 JSON；无时区 now 拒绝。
- test_graph_mock：降本流程精确总额/工期、错误状态、自动版本、旧缓存隔离、重复执行不再收费/写 Trace、执行上限保留素材。
- test_storage：原子 JSON 保存、版本索引、拒绝覆盖已有方案。

`.venv/Scripts/python.exe -m unittest discover -s tests -v` 无需 Key 或网络。
实际 CLI 多场景另用 scripts/run_evidence.py --mode mock，真实云端用 --mode cloud 单列，不归入默认离线测试。
