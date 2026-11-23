# Contributing

MemoTrace-Recovery is built for people who browse, recover, and export their own WeChat
chat history. A small team maintains the project. Public pull requests are for bug fixes.

Start with a specific bug you have encountered in daily use. Bring new features,
UI or architecture overhauls, and support for additional platforms to
[Ideas discussions](https://github.com/AFK-agent-team/MemoTrace-Recovery/discussions/categories/ideas).

## The best way to help

Help us find things we can close.

- **Duplicates.** Comment with a link to the original discussion or PR.
- **Fixed bugs.** If a reported bug no longer happens on the latest release, say so and give the version.
- **Dead PRs.** If a PR is superseded or abandoned, link what replaced it.

A +1 on an existing PR is ALWAYS better than another PR. Search the open PRs before you start. If one
covers your bug, react to it, test it, and comment with what you saw.

Report bugs in [Bug reports](https://github.com/AFK-agent-team/MemoTrace-Recovery/discussions/categories/bug-reports). Put feature requests and contribution proposals in
[Ideas discussions](https://github.com/AFK-agent-team/MemoTrace-Recovery/discussions/categories/ideas). Ask usage questions in [Q&A](https://github.com/AFK-agent-team/MemoTrace-Recovery/discussions/categories/q-a).

## Rules for a PR

These rules apply to every PR from outside the team, drafts included. The seven trusted
maintainers commit and push to `main` after local verification. See the [repository rules](AGENTS.md).

<a id="prior-approval"></a>

### Fix a bug

A PR must fix a bug. A crash, a regression, and a real performance problem are bugs.
Proposals to change the intended behavior belong in Ideas.

A small fix for an obvious bug can go directly to a PR. For a larger fix, link the
Discussion that shows the bug.

<a id="one-problem"></a>

### One problem per PR

Fix one problem. Include every change that fix needs across the client, recovery tools,
tests, and docs. Keep cleanup, refactors, and a second fix in separate changes.

<a id="verification"></a>

### Show that it works

Run the [project `/verify`](.agents/skills/verify/SKILL.md). Say how you found the bug, how you checked
it, and what you saw. Include the commands or steps, the results, and any environment you could not check.

UI changes need before and after screenshots. Add a short video when the change is about motion or
timing. Upload the evidence to the PR.

<a id="models"></a>

### Models we do not accept

Gemini 4 Neon is the accepted Gemini model for writing contribution code. Gemini models
may review code. If you used an agent to write the patch, name the model and harness.

<a id="open-pr-limit"></a>

### 3 open PRs per person

You can have 3 open PRs. Maintainers close a fourth when you open it. Get one merged or closed,
then open it again.

<a id="conflict-window"></a>

### Keep your PR mergeable

Maintainers label a PR that conflicts with its base branch `needs-rebase`. If it still conflicts
after 7 days with no activity, they close it.

## Bans

Maintainers may block accounts for automated spam, repeated pings, arguing after a
closure, or using another account to get around a limit.

<a id="closure-and-reconsideration"></a>

## When your PR is closed

A PR that breaks a rule gets a notice that names the rule and says how to fix it.
Maintainers close it 72 hours later. A reply or a push in that time sends it back for another look.

After it is closed, fix the problem and comment on the PR, or open a focused replacement PR that links
the old one.

## Setup

See [installation and running](README.md#安装与运行).
