# 04 AI 使用、个人修改与验证

## 已发生的 AI 辅助

本轮 AI 根据用户提供的截图与对话：

- 整理考核目标、核心范围、Agent 决策、GitHub 规范与验证要求。
- 记录用户确认的 Python、LangGraph、CLI、cloud/mock 双模式。
- 准备目录骨架、README、环境变量示例、依赖版本和文档路线。
- 列出后续方法的备选项与优缺点。

AI 协助实现代码、修复并发结果对齐；用户在本机完成过两条旧版 DeepSeek 云端真实运行。2026-11-01 AI 发现并修复费用字段混用、后续费用遗漏、候选集合误算、工期依赖和 GBK 输出问题，补充 18 项测试及 15 个实际离线 CLI 场景。用户随后在本机运行两条 v2 DeepSeek 路径；AI 复核原输出、Trace、保存文件和哈希，正常完成、持续故障上限停止。

## 用户已经做出的选择

主语言 Python、框架 LangGraph、CLI、云端 API + 离线 Mock；DeepSeek + OpenAI SDK 直连、deepseek-flash 非思考模式、通用模型节点 + 受控工具分发、本地 JSON 幂等、失败单独计费、实际并发；工作目录 G:\Content-Task-Planning-Agent；GitHub 仓库由用户提供。

用户处理了目录创建、Git 目录信任与远程关联等环境步骤。不要把 AI 生成的代码或文档自动记为用户亲自完成。

## 后续记录模板

| 日期/阶段 | AI 辅助内容与涉及文件 | 个人修改与理由 | 实际验证命令/证据 | 结果 |
|---|---|---|---|---|
| v2 修复 | accounting/tools/graph/cli、UTF-8 --output、证据脚本和文档 | 用户报告中文乱码；个人代码修改待用户说明，不编造 | examples/test-results/unittest-final.txt、mock-evidence.json、cloud-evidence.json | 18 项测试、15 个离线及 2 个用户实测云端场景复核通过 |

## 记录原则

如实区分 AI 建议、用户选择、AI 实现、个人修改与实际验证。不编造个人贡献，不提交内部思维链，不记录密钥或私人数据。
