# Runbook: operations agent

For whoever owns the agent. Commands run from the repo root with the virtual environment active.

## Try it from the command line

```bash
cp ops/roles.example.yaml ops/roles.yaml        # then put real Slack user ids in it; it seeds an empty access record
python -m plumbline.agents ask "did last night's load reject anything?" --as U0OPS0001
python -m plumbline.agents ask "fix the stale docs" --as U0OPS0001
python -m plumbline.agents ask "approve P<id>" --as U0PRIV002     # a different person, with the docs role
```

## Roles and access

Roles live in `ops.access` inside `data/agent.duckdb`. `ops/roles.yaml` is read only while that table is
empty, to seed the first holders; editing the file afterwards changes nothing. Change access through
the agent, and have a second admin approve:

```bash
python -m plumbline.agents ask "give U0NEW0001 the privacy role" --as U0ADM0001
python -m plumbline.agents ask "approve P<id>" --as U0ADM0002
python -m plumbline.agents ask "offboard U0NEW0001" --as U0ADM0001   # every role removed, open requests closed
duckdb data/agent.duckdb "SELECT * FROM ops.access ORDER BY granted_at"
```

Keep at least two admins: one admin cannot approve their own requests, and the last admin cannot be
removed.

## Draft a security questionnaire

```bash
python -m plumbline.agents questionnaire questions.csv answers.csv --as U0OPS0001
```

The input needs a `question` column. Each output row has a status ("drafted" or "needs a person"), the
draft and its source section. The compliance owner reads every draft before it is sent; a row that
needs a person is a control the documents do not describe. When a control changes, change
`docs/security.md` in the same pull request.

## Connect a language model (optional)

```bash
export PLUMBLINE_LLM_MODEL=<model name>
export PLUMBLINE_LLM_API_KEY=<key>                 # never commit it; never paste it in Slack
export PLUMBLINE_LLM_BASE_URL=<endpoint>           # any OpenAI-compatible chat-completions endpoint
python -m plumbline.agents eval                    # quoted and model answers side by side, knowledge and questionnaire
```

If the model returns an answer with a sentence that cites nothing, or cites a passage it was not
given, the quoted answer is shown instead and the reason is in the audit log (`detail.note`).

## Connect Slack

1. Create a Slack app. Bot token scopes: `app_mentions:read`, `chat:write`, `im:history`.
2. Event subscriptions: `app_mention` and `message.im`. Interactivity: on. Both Request URLs point at
   the endpoint below, over HTTPS (a tunnel or a reverse proxy in front of it).
3. Run it:

```bash
export SLACK_SIGNING_SECRET=<from Basic Information>
export SLACK_BOT_TOKEN=<xoxb- token from OAuth & Permissions>
python -m plumbline.agents serve --port 3000
```

## When something goes wrong

- **Slack shows "dispatch_failed" or the agent answers twice.** Slack retries when it does not get a
  200 within three seconds. Retries are dropped by event id (`ops.slack_events`), so a double answer
  means two agents are running against two different `data/agent.duckdb` files. Run one.
- **Every request gets 401.** The signing secret is wrong, or the server clock is more than five
  minutes off (requests older than that are refused as possible replays). Check `date`.
- **"Not done: ... does not hold the ... role."** Ask the agent to grant the role and have an admin
  approve it. Roles are the only way to grant approval rights; there is no override, and editing
  `ops/roles.yaml` after the first start does nothing.
- **A proposal "approved, but it failed and changed nothing".** For a document edit, someone changed
  the passage since it was proposed. Ask for the fix again; a fresh proposal uses the current text.
- **The nightly `docs_match_the_warehouse` check is yellow.** A documented fact no longer matches the
  data. Ask the agent "are the docs still accurate?", then "fix the stale docs", and approve.

## Who did what

```bash
duckdb data/agent.duckdb "SELECT logged_at, actor, skill, action, tool, proposal_id, outcome FROM ops.audit ORDER BY logged_at DESC LIMIT 50"
duckdb data/agent.duckdb "SELECT id, requested_by, tool, status, decided_by, decided_at FROM ops.proposals ORDER BY created_at DESC"
python -m plumbline.agents usage --days 30
```

## Rotate a secret

Slack: regenerate the signing secret or reinstall the app for a new bot token, update the environment,
restart `serve`. Model: revoke the key at the provider, set the new one. Neither is stored anywhere
else, so nothing else needs changing.
