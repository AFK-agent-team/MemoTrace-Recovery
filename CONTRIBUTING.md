# 参与 MemoTrace-Recovery

欢迎报告 bug、讨论功能和提出贡献方案。先搜索已有讨论和 PR。如果已有相关修复，欢迎试用并补充实际结果。如果问题在新版本已修复，请注明验证过的版本。报告、建议和提案统一在 GitHub Discussions 交流。 [进入 Discussions](https://github.com/AFK-agent-team/MemoTrace-Recovery/discussions)

## 报告 bug

- 写明 MemoTrace 版本或 commit、操作系统和微信版本。
- 提供最短复现步骤，写出预期结果和实际结果。
- 说明影响，例如是否阻止导出、是否可以重试，以及问题出现的频率。可附脱敏日志、截图或最小样例。

## 提交 bug 修复

1. 每个 PR 修复一个可复现的 bug，包含修复所需的测试和文档。明显的小修可直接提 PR。较大的修复先在 Discussions 记录复现，再在 PR 里链接该讨论。
2. 完成本地验证，写出实际运行的命令或操作、观察结果和未覆盖的环境。界面改动附前后截图，涉及交互或时序时附短录屏。把证据上传到 PR。 [项目 `/verify`](.agents/skills/verify/SKILL.md)
3. 用 PR 模板说明问题、修复范围和验证结果。使用代理撰写代码时，注明代理和实际撰写代码的模型。Gemini 撰写的代码只接受 Gemini 4 Neon，Gemini 模型可用于审查。 [PR 模板](.github/pull_request_template.md)

## 功能提案和日常维护

功能提案请写出使用场景、现有流程遇到的问题和期望结果。七位受信任协作者完成本地 `/verify` 后，将日常维护和功能改动直接 commit 并推送到 `main`。 [仓库维护规则](AGENTS.md)
