# Blind judge

A panel of independent, context-isolated Claude processes scores the **deployed** app against the official Junction judging criteria. It approximates what Valio Aimo's jury will see. The aim is to catch what we have stopped seeing ourselves.

```
architect ──► scripts/judge <url>
                 │  fresh temp dir outside the repo, per persona:
                 │    CHALLENGE.md   (official challenge text, no credentials)
                 │    SUBMISSION.md  (copy of docs/SUBMISSION.md = what we actually submit)
                 │    ./browse ./http (generic helpers, locked to the app's origin)
                 ├─► judge: commercial        ┐
                 ├─► judge: online-sales      ├─ in parallel, `claude -p`, same rubric
                 └─► judge: tech-governance   ┘
                 ▼
          judging/reports/<ts>.md|json   (scores, ranked improvements, bugs, requests tried)
                 ▼
architect triages → GitHub tickets (label `judge`) → developers
```

## The information firewall (why the judge is useful at all)

A judge that knows our plan, our reasons and our trade-offs will forgive exactly the weaknesses that real judges punish. So the judge gets **only what a real Junction judge gets**:

- the challenge text;
- the submission text;
- the running product.

It does not get the plan, the brief, tickets, code, CLAUDE.md, git history, chats, memory or earlier reports.

How the firewall is enforced (verified by `scripts/judge --selftest`):

- Each judge runs in a fresh temp directory outside the repo, with:
  - `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`;
  - `--setting-sources local`;
  - `--strict-mcp-config` with no servers;
  - `--no-session-persistence`.
- `--tools "Bash,Read"`, `--permission-mode dontAsk` and an allowlist of `./browse`, `./http` and `echo`. Everything else is auto-denied, including `cat`/`ls` outside the run directory, `curl`, compound commands and command substitution.
- Read deny rules cover the whole projects folder and `~/.claude`, and `blockReadsOutsideWorkingDirectories` is on.
- `./browse` and `./http` refuse any URL outside the app's origin (also `file://`).
- The OS sandbox (bubblewrap) cannot start on this Ubuntu machine (AppArmor user-namespace restriction), so it is **not** relied on.

Rules for humans and agents:

1. Only the **architect** runs `scripts/judge`. Developers never run it and never edit `judging/`.
2. Never edit `JUDGE.md`, `personas/` or `CHALLENGE.md` to describe our product, our design or our reasons. Change the rubric only if it misstates the official criteria, and then only from the official text.
3. `docs/SUBMISSION.md` is the **real** submission text: claims only about what is deployed, no internal rationale. If we would not paste it into the Junction form, it does not belong there.
4. Never feed a judge an earlier report. Every run is blind.
5. Don't optimise for the judge prompt. A finding becomes a ticket only if it describes a real problem for Valio's jury or users.
   - "The judge misunderstood X" means X is unclear in the product. That is a UX/copy ticket.
   - If a finding contradicts Valio's own brief, the brief wins. Note it in the triage and make no ticket.

## Running

```bash
scripts/judge --selftest https://<app>   # < $0.01, ~15 s: must print "PASS (judge is isolated)"
scripts/judge https://<app>              # 3 judges in parallel (opus), ~10–20 min
JUDGES=tech-governance scripts/judge https://<app>    # one persona only
JUDGE_MODEL=sonnet scripts/judge https://<app>        # cheaper run
```

- The app and its API must be served from **one origin**. The helpers refuse every other origin.
- Raw transcripts and screenshots go to `judging/runs/` (gitignored).
- Reports go to `judging/reports/` (committed by the architect, so the score history is visible to the team).
