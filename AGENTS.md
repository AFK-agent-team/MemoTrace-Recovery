# MemoTrace-Recovery 仓库约定

本文件适用于整个仓库。公开参与方式见 [贡献指南](CONTRIBUTING.md)。

## 直接提交和推送

- 直接推送权限只授予组织所有者 `liush2yuxjtu` 和本次获邀的六位成员。由所有者在 GitHub 核对这六位的账号并维护权限名单。以受信任协作者身份执行的代理遵守同样的权限范围。
- 维护者完成本地验证后，将聚焦的 commit 直接推送到默认分支 `main`。推送前核对远端和最新分支状态。使用普通快进推送，保留他人的提交。
- 日常维护、功能和重构走直接提交。GitHub Issues 禁用。Bug 报告、功能建议、贡献提议和使用问题走 Discussions。PR 只用于修复可复现的 bug，按[贡献指南](CONTRIBUTING.md#prior-approval)提供复现和验证结果。
- 使用 Gemini 撰写的代码只接受 Gemini 4 Neon。Gemini 模型可参与代码审查。使用代理撰写代码时，在 PR 中写明代理和实际撰写代码的模型。

## 本地验证代替 CI/CD

- 禁止 CI/CD，包括 GitHub Actions 工作流、外部 CI、自动部署和自动发布流水线。验证入口统一使用仓库内的 [`/verify`](.agents/skills/verify/SKILL.md)。
- 每次 commit 前执行与本次改动对应的本地验证。推送前确认验证覆盖待推送的候选版本，代码或验证条件有变化时复验。保留真实入口的验证结果，报告确实未覆盖的环境。

## 代码维护

- 一个提交解决一个明确的问题，包含这个问题所需的测试和文档。提交标题用简短动词说明结果。选择贴合需求的最小设计，复用已有入口和模块。
- 让操作系统、微信客户端、文件格式等差异留在对应的适配层。改动共享逻辑时检查 Windows 桌面、Linux 导出和 CLI 中受影响的实际入口，兼顾性能和远程运行。把环境值交给调用方配置。
- 验证实际行为。优先扩充能抓到回归的现有测试，在最靠近故障的层证明问题。视觉变化用真实界面前后对照验证。直接等待可观察的状态或结果，避免在测试中用固定延时猜测成功。
- 只记录代码看不出来的约束和理由。行为变化时更新已有说明，调试数据、私有聊天导出、临时截图和工作草稿保存在提交之外。

维护原则参考 T3 Code 的 [AGENTS.md](https://github.com/pingdotgg/t3code/blob/main/AGENTS.md)、[贡献指南](https://github.com/pingdotgg/t3code/blob/main/CONTRIBUTING.md)和[测试指南](https://github.com/pingdotgg/t3code/blob/main/docs/internals/15_testing.md)。
