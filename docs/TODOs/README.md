# docs/TODOs — active plans & open work

This folder is the **single source of truth for work that is planned but not yet done**.
Anything that needs doing — a fix, a refactor, a follow-up, an investigation — gets a plan
file here, worked in place, and **deleted when complete**. If a file is in this folder, the
work is still on the table. If the work is done, the file is gone.

## Rules

1. **One plan = one file**, named `YYYY-MM-DD-short-slug.md` (creation date + kebab-case slug).
   Copy `_TEMPLATE.md` to start.
2. **Every plan carries a date created** and a **`Status:`** line in its front block
   (`PLANNED` / `IN PROGRESS` / `BLOCKED` / `DONE — pending removal`).
3. **Keep a running worklog** inside the plan as you work it — newest entry at the top of the
   Worklog section, each entry dated. The worklog is the durable record of what was tried,
   what landed, and what's left, so any agent (or Zack) can resume cold.
4. **When the work is complete and verified, delete the file.** Don't leave DONE plans lying
   around — the folder should only ever show *open* work. Capture anything worth remembering
   long-term in a `.claude` memory or a commit message before deleting (the worklog is
   throwaway once the change is merged + verified on the rig).
5. **Review this folder at session start.** Read every file here to see what's outstanding
   before picking up new work — this is listed as a session-start step in the repo `CLAUDE.md`.
6. **Backend / rig plans still follow the lane rules.** A plan that touches `orin/wavecam` or
   deploys to the Orin is Codex's lane: needs Zack's assignment + a `.agent-collab` bus claim
   before code is written. Writing the *plan* is fine anytime; executing it is gated.

## Lifecycle

```
copy _TEMPLATE.md  ->  YYYY-MM-DD-slug.md   (Status: PLANNED, date created)
        |
        v
work it, appending dated Worklog entries    (Status: IN PROGRESS)
        |
        v
change merged + verified (rig/tests/build)  (Status: DONE — pending removal)
        |
        v
record any lasting lesson (memory/commit) -> DELETE the file
```

## What does NOT go here

- Long-lived architecture/design specs → `docs/superpowers/specs/`.
- Reviews / audits (point-in-time reports) → `docs/reviews/`.
- Durable cross-session facts → `.claude` memory (`MEMORY.md` index).

This folder is *only* for the live to-do list of plans being executed.
