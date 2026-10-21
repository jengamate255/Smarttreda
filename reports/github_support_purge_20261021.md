# GitHub support request — purge residual PR ref holding leaked credentials

Prepared 2026-10-21. GitHub cannot be reached for this action from the local shell
(`DELETE /repos/{owner}/{repo}/pulls/1` returns 404 — PR deletion is not exposed by the
API), so this must be submitted from the account's support portal, or PR #1 deleted in the
UI if that option is available to the repo owner.

## Request text (paste as-is)

> **Subject:** Residual `refs/pull/1/head` on a renamed/private repo still serves commits
> containing leaked credentials — please purge the ref and any objects reachable only from it.
>
> Account: `jengamate255`
> Repository: `jengamate255/Superbot-` (now **private**)
> Pull request: #1 — state `closed`, **never merged**, head branch `codex/prevent-credential-leaks`
> (deleted), head commit `2fc173fcccf7f048e6b957f1f149973b6eecef59`
>
> `refs/pull/1/head` still exists and still resolves, together with its full 61-commit
> ancestry, which includes the pre-rewrite commit `43f07579` ("upload: production services,
> launchers, …", 2026-10-17). That lineage contains hardcoded credentials in
> `archive/ai_vision_daemon.py`, `mt5_mcp/memory/settings.good.json`,
> `mt5_mcp/telegram_bot.py`, `supervisor.conf`, `start_services.ps1` and other paths — the
> repository was **public** from 2026-10-17 until it was made private, and GitHub push
> protection did not block them (only GCP/OpenRouter/Telegram formats were named by the
> GH013 warning on a different repo).
>
> A second, later audit found that the **FundingPips2-SIM account password (login
> 11911448)** was also still present in nine tracked files on `master` — `accounts.json`,
> `supervisor.conf`, `start_services.ps1`, `archive/telegram_bot.py`,
> `archive/start_dual_live.ps1`, `archive/gold_ndx_scalp.py`,
> `archive/backtest_gold_ndx_scalp.py`, `archive/forward_test_gold_ndx.py` and a dashboard
> token fragment in `TELEGRAM_PRODUCTION.md`. Those versions were published by the same
> force-push on 2026-10-19 and were public until the repository was privatized. A third
> rewrite (2026-10-21, tip `fa24c76`) removed them from `master`; the commit `43f07579`
> lineage served by PR #1 still contains the earlier versions, which is why this purge
> request matters.
>
> Remediation already completed on our side:
>
> 1. Two `git filter-branch --index-filter` rewrites removed the credential literals; the
>     rewritten `master` (now 62 commits, tip `5ea8c72`) was force-pushed with lease and
>     contains **no** pre-rewrite secret blob (verified by blob-SHA reachability scan over
>     every object reachable from `master`, plus per-path checks against the replacement map).
> 2. The leak-holding branch `codex/prevent-credential-leaks` was deleted and PR #1 closed.
> 3. Local pre-rewrite refs, reflogs and objects were pruned (`git gc --prune=now`);
>     `git fsck` is clean.
> 4. The affected credentials are being rotated by the account owner.
>
> Please:
>
> * delete/purge `refs/pull/1/head` for PR #1 (and confirm whether closing without deleting
>   the PR is why it persists),
> * run object garbage collection / reachability cleanup for any objects reachable **only**
>   from that ref, including commit `43f07579`,
> * list and detach or remove any **forks** or network copies created while the repository
>   was public, and confirm whether cached views (raw.githubusercontent.com, API caches,
>   search indexes, code-scanning clones) still retain those objects,
> * confirm when the objects are no longer fetchable.
>
> Note: the same repository history was also pushed to `jengamate255/Smarttreda`, which has
> **no** pull refs at all (verified via `git ls-remote refs/pull/*`) and whose `master` passes
> the same scan — no action needed there beyond GC if the replaced pre-rewrite tip is still
> retained as a dangling object.

## Local evidence attached when filing

| Check | Result |
|---|---|
| `git ls-remote origin "refs/pull/*"` | `2fc173fcccf7f048e6b957f1f149973b6eecef59  refs/pull/1/head` |
| `git ls-remote smarttreda "refs/pull/*"` | empty |
| `GET /repos/jengamate255/Superbot-/pulls/1` | `state=closed merged=False commits=60` |
| `GET /repos/…/commits/43f0757` | 200 — pre-rewrite commit still fetchable server-side |
| `GET /repos/…/compare/2fc173f...43f0757` | `status=behind behind_by=1` → pre-rewrite tip is an ancestor of the PR ref |
| pre-rewrite blob SHAs at PR-ref ancestors | still served (see `gh_ancestry_audit.txt`, count recorded below) |
| `master` per-path blob SHAs | none equal a pre-rewrite SHA |
| repo visibility now | `private=True` (authenticated API), anonymous API 404, anonymous `ls-remote` requires auth |

Total pre-rewrite blob servings found across the PR-ref ancestry: **84** (59 × `supervisor.conf`,
22 × `mt5_mcp/telegram_bot.py`, 3 × `mt5_mcp/memory/settings.good.json`).

## Why rotation still matters

History rewriting does not un-leak anything that was publicly fetchable during
2026-10-17 → privatization. Treat as compromised until rotated: **the FundingPips2-SIM
password for login 11911448** (newly confirmed leaked), 6 Deriv PATs, the Deriv MT5
password, the Telegram bot token, the OpenRouter key, the Gemini/GCP key and Groq key. The
dashboard auth token was already rotated on 2026-10-21 (only a dead 12-char fragment was
left in `TELEGRAM_PRODUCTION.md`).
