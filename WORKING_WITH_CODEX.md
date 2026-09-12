# Working With Codex on the FPL Model

This guide is for returning to the project after a week or more away. You do not need to copy and paste old chat transcripts or manually reconstruct everything that happened.

## The simplest routine

Keep the **FPL-model local project** in the Codex app connected to:

```text
/Users/craig/Documents/FPL-model
```

Chats created inside that project can work with the same repository. Each chat has its own conversation, while the repository, Git history, `AGENTS.md`, and checked-in documentation provide durable shared context.

When you return:

1. Open the FPL-model project.
2. Reopen the existing chat if you are continuing the same piece of work.
3. Start a new chat if you are beginning a distinct feature, investigation, or model decision.
4. State what you want to do next. Codex is instructed to read the project handoff and verify the live repository state first.

## Prompt for a new chat

You can paste this short prompt:

```text
Continue the FPL-model project. Read AGENTS.md, docs/PROJECT_STATE.md,
README.md, and the relevant parts of TODO.md. Check Git status, fetch origin,
and tell me briefly what has changed since the state file was last updated.
Do not build anything yet. My next topic is: [describe it here].
```

That is enough. There is no need to paste the previous chat summary unless an important decision was discussed but never recorded in the repository.

## When to keep or change chats

Keep using the current chat when:

- you are refining the same feature;
- Codex is still implementing or testing the current request;
- your next question depends heavily on the discussion immediately above.

Start a new chat when:

- the previous outcome is finished and you are starting a different one;
- you are switching from, for example, data-refresh reliability to a new model experiment;
- the old discussion is making the new task harder to describe or review.

Do not start a new chat every few turns solely to reduce context. Long chats can be compacted automatically, while a new chat must spend some effort rediscovering repository state. A fresh chat is most useful at a genuine work boundary.

## Useful ways to phrase requests

## Change-approval rule

Unless you explicitly say **“ok go”**, Codex should treat a request as discussion,
inspection, planning, or review only. It may read files, check Git and data status,
and explain options, but must not edit files, build or regenerate data, run a local
application, commit, push, deploy, or otherwise change project state. Saying
**“ok go”** authorizes the agreed implementation; committing and pushing still
require their own explicit request.

### Discuss before building

```text
I want to explore [change]. Inspect the existing implementation, explain the
options and trade-offs, and ask any material clarifying questions. Do not edit
files until I say “ok go”.
```

### Implement an agreed change

```text
Ok go. Implement the agreed change, test it appropriately, and show me the
result. Do not commit or push yet.
```

### Finish and publish

```text
Review the final diff, run the relevant tests, update docs/PROJECT_STATE.md if
the project state materially changed, then commit and push.
```

### Check data freshness

```text
Check the latest GitHub Actions refresh runs and the metadata in the published
prediction data. Tell me the latest completed FPL gameweek, ClubElo effective
date, whether any fallback was used, and whether there are warnings.
```

## Where information belongs

- `AGENTS.md`: stable instructions Codex should follow automatically.
- `docs/PROJECT_STATE.md`: short, time-sensitive handoff and recent milestones.
- `README.md`: what the application does and how it works.
- `TODO.md`: deferred model improvements.
- Git commits: the exact history of implemented changes.
- A chat: exploration, questions, and decisions still in progress.

If a decision should survive into future chats, ask Codex to record it in the appropriate file before ending the session.

## A good end-of-session habit

Before leaving the project for another week, ask:

```text
Please finish the session: verify the work, summarize what changed and what is
still open, update docs/PROJECT_STATE.md if needed, and tell me whether the
working tree is clean and whether everything requested has been pushed.
```

This keeps the next restart short and reliable without turning the handoff into a long running diary.

## Official guidance

- [Projects and chats](https://learn.chatgpt.com/docs/projects)
- [Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
