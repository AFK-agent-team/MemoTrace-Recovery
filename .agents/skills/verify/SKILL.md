---
name: verify
description: Run local verification for MemoTrace-Recovery before a commit or direct push, or when asked to verify a bug fix. Check the changed entry point on its real Windows or Linux surface.
---

# MemoTrace-Recovery 项目验证

基于 dot-pi 的 `templates/project-verify/SKILL.md`（`924bf75`），保留本地测试、实际入口、证据和结论的验证流程。

每次 commit 前执行此流程，push 前确认同一个候选版本已验证。收到 `/verify` 时，按当前改动或指定目标选择下面的验证路径。遇到可复用的新验证步骤时，补充已实测的命令和观察信号。

## 验证步骤

1. 核对暂存区和待推送的版本，确认测试覆盖候选代码。明确受影响的入口、预期结果和相关边界。
2. 在适用的本地或授权测试机上运行相关的现有测试。先覆盖最小的行为变化，再按影响范围扩大。
3. 从真实入口实际操作一次。导出功能要打开产物，检查字段和内容是否符合要求。修复发现的问题后，重新验证受影响的行为。
4. 记录候选版本、命令、退出状态、关键观察和一个结论。`PASS` 表示适用的测试和真实入口验证均通过，`FAIL` 表示检查失败，`BLOCKED` 表示缺少必要环境或授权。文档修改的运行时检查记为 `SKIP`。

## 项目验证路径

- **通用检查。** 提交前在仓库根目录运行 `git diff --cached --check`。推送前更新远端引用并运行 `git diff --check origin/main..HEAD`。仅修改文档时，再检查变动的文件路径和站内链接。
- **Windows 桌面或数据读取。** 按 [README](../../../README.md#安装与运行) 准备 Windows 环境，运行 `工具/test_recovery.py` 中与改动相关的检查。再从 README 的启动脚本或公开 CLI 打开获准使用的测试会话，验证受影响的浏览或导出流程。
- **Linux 群聊导出。** 按 [README](../../../README.md#linux-群聊记录导出) 准备 X11 桌面和已登录的测试账号。检查 `python3 工具/linux_wechat_history.py --help`。对获准读取的测试群执行 `--group "<测试群名>" --today`，检查 JSON 中的群名、日期、时区和记录数与客户端一致，再检查日期边界或无效时区。保留计数和结果，避免在验证报告中粘贴原始群聊。
