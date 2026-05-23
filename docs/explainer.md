# What is ai_company? (the plain-English version)

> Audience: a smart 15-year-old. No prior background. Read top-to-bottom.

## The one-sentence version

ai_company is a robot coworker that writes code for you. You tell it
what you want, it makes a plan, you say yes, it writes the code, you
say yes, it commits it to git. Three "yes" buttons. That's the whole
loop.

## Why does it exist?

ChatGPT can write code, but you still have to:
- copy-paste the answer into the right file
- run the tests yourself
- check if it broke anything
- remember which model is cheapest today
- pay for the API

ai_company does all of that for you. And it tries hard to do it for
**free** by rotating between providers that give away free API calls
(Groq, NVIDIA, OpenRouter) before it ever touches a paid one
(Anthropic / Claude).

## How it works (the cartoon version)

Picture a small office. You walk in, sit down, and write a sticky note:

> "Add a `--version` flag to my CLI tool."

You stick it on the desk and leave. Inside the office:

1. **The analyst** reads your note, looks at your project's files, and
   writes a one-paragraph summary of what you actually want.
2. **The planner** writes a step-by-step plan: "open file X, add this
   function, add this argument."
3. **You come back.** A message pops up: "Here's the plan. Look OK?"
   You hit ✅ or ❌.
4. **The coder** executes the plan — actually edits the files.
5. **The tester** runs the test suite.
6. **If tests fail**, the coder gets up to 3 retries to fix itself.
7. **The reviewer** reads the diff and writes a code review.
8. **You come back again.** "Here's the diff and the review. Commit?"
   You hit ✅ or ❌.
9. **The committer** writes a git commit and saves it.

Three checkpoints (plan, code-if-tests-fail, commit). Robot does the
boring middle. You stay in charge of every actual change to the repo.

## The five doors into the office

You don't have to be sitting at the desk. There are five ways to drop
the sticky note:

1. **Terminal** (`cli.py run "<task>"`) — the no-frills way.
2. **Web dashboard** (Streamlit on http://localhost:8501) — buttons,
   live token-by-token streaming, a microphone you can talk into,
   a git panel to undo things with one click.
3. **Web SPA** (http://localhost:8765/) — a Claude-style chat page.
   Same backend, lighter UI.
4. **Slack** — type `/ai-run fix the login bug` in any channel.
   Approve / reject buttons appear right there. Also: if you just
   *describe* a bug in chat (no slash command), the bot notices and
   asks "Want me to start a workflow for that?"
5. **Telegram** — same thing on Telegram (`/ai_run …`).
6. (Bonus) **Email** — a poller can watch your inbox; matching emails
   queue up as pending tickets.

All five doors lead to the same office. Same plan → code → commit
flow.

## The free-by-default trick

Most "AI agents" cost real money per task because every call goes to
Claude or GPT-4. ai_company tries cheap-and-free first:

- A **router** in the middle decides: "this is just a label-the-task
  job → use Groq's free Llama model. This is a long code-review →
  use Claude only if the user opted in."
- Per task type (`analyze`, `plan`, `code`, `review`, `summarize`,
  `simple`) you get separate model choices.
- If one provider is rate-limited or down, **it falls back** to the
  next one automatically.
- Every call's token count and dollar cost is **logged to SQLite**, so
  you can ask "how much did I spend this week?" any time.
- Hard ceilings: `BUDGET_USD_PER_RUN`, `BUDGET_USD_PER_DAY`. If you
  hit them, the workflow pauses and asks if you want to raise the
  ceiling.

You can run the whole thing on $0/month if you stick to free
providers.

## The brain (memory)

ai_company remembers things across tasks. Three kinds of memory:

1. **Short-term** — what's happening in *this* workflow right now.
2. **Long-term keyword search** — SQLite FTS5; every workflow's notes
   are stored and grep-able.
3. **Long-term semantic search** — same notes, but indexed by
   *meaning* so you can ask "what did I do about login bugs last
   month?" without remembering the exact words. Powered by Ollama
   (local) or `sentence-transformers` (also local).
4. **Obsidian bridge** (optional) — if you use the Obsidian notes app,
   point it at your vault and every memory gets mirrored as a
   markdown file in your vault. Your second brain merges with the
   robot's brain.

## Use cases — real examples

### 1. "Fix this bug"

You: `/ai-run when I press 'submit' nothing happens, fix it`

Robot: reads your codebase, finds the submit handler, makes a plan to
add the missing event listener, asks if plan is OK, writes the code,
runs your tests, shows you the diff, you approve, commits it.

Time: ~2 minutes. Cost: $0 if free providers are working.

### 2. "Add a feature"

You: `python cli.py run "Add a dark mode toggle to the dashboard"`

Same flow. Robot picks the file, edits CSS, edits the toggle handler,
runs tests, commits.

### 3. "Review my pull request"

You: `python cli.py review-pr 42 --crew --inline`

Robot reads PR #42 on GitHub, has **two AI personalities** argue about
it (a Reviewer and a Critic — they cross-check each other), then posts
inline comments on the diff with suggested fixes. Like CodeRabbit but
free and runs on your machine.

### 4. "I just want a chatbot"

Hit http://localhost:8765/ in your browser. It's a Claude-style chat
page. Type stuff, get answers. Backed by the cheap-router, so a casual
"explain Python decorators" call costs nothing.

Or use the Slack bot the same way — `@ai_company what's a decorator?`

### 5. "Schedule it"

Push a task into the queue:

```
python cli.py queue push "Update the README with this week's changes" --priority 5
```

Then a cron job or systemd timer can drain the queue overnight.

### 6. "Voice it"

In the dashboard, hit the 🎤 button, **talk** your task. Groq Whisper
transcribes it. Robot plans the work. Hit the 🔊 narration toggle and
the dashboard reads each step aloud as it happens.

### 7. "Tell me about my code in Obsidian"

Set `OBSIDIAN_VAULT_PATH` to your vault. Every task the robot runs now
**writes a note in your vault** explaining what it did. Your vault
becomes a living project journal.

Also: install the `obsidian-copilot` plugin in Obsidian, point it at
`http://localhost:8765/v1`, and now the chat box *inside Obsidian*
uses the same free-routing backend.

### 8. "Run it in the cloud"

`infrastructure/` has Terraform modules for **six** clouds: Oracle
(free forever Ampere VMs), AWS, GCP, Azure, Hetzner, DigitalOcean.
`terraform apply` and you have ai_company running on a server. SSH
tunnel into the dashboard.

### 9. "Help me run a meeting"

Slack ticket detection: when teammates dump bug reports or feature
requests into a channel, the bot picks them out automatically and
queues them as draft tickets. You skim and hit Start on the ones
worth doing.

### 10. "Don't trust the robot, just want suggestions"

`--dry-run` mode on `review-pr`. Or just don't hit ✅ on the commit
checkpoint — workflow ends after the diff is shown to you, nothing
is committed.

## Why this is different from "just ChatGPT"

| Thing | ChatGPT | ai_company |
|---|---|---|
| Writes code | yes | yes |
| Actually edits your files | no | yes |
| Runs your tests | no | yes |
| Makes git commits | no | yes |
| Lets you stop it before each step | n/a | three checkpoints |
| Costs money per token | yes | free by default (Groq/NVIDIA/OpenRouter tiers) |
| Remembers across sessions | no | SQLite + FTS5 + semantic + Obsidian |
| Works from Slack / Telegram / voice | no | yes |
| Tracks spend | no | per-call ledger |
| Self-improves over time | no | yes — DSPy MIPROv2 re-tunes prompts from past runs |
| Auto-picks the best model | no | yes — tracks per-(provider, model, task) success rates and routes to the winner |
| Typed outputs from any LLM | no | yes — pydantic schemas via instructor + litellm |
| State survives a restart | no | yes — persistent LangGraph checkpointer |

The last four rows are the **self-improvement loop** (Phase Z + Z2):

1. Every workflow ends with a `do_retrospective` node that writes the
   outcome (success / tests-failed / rejected / budget-abort) to
   SQLite, along with cost, retries, and a one-line LLM self-critique.
2. A **cron job** (`cli.py tune auto`) reads accumulated retrospectives
   and uses **DSPy MIPROv2** to optimise the analyze/plan/review system
   prompts. Saved to `tuning/learned_prompts.json`.
3. The router uses joined ledger+retrospective data to pick the
   highest-success `(provider, model)` for each task type — so if Groq
   Llama keeps producing successful plans for *your* repo, it wins.
4. The next workflow uses the tuned prompts and the learned model.

Every layer is **off by default** and controlled by an env flag
(`USE_LEARNED_PROMPTS`, `USE_LEARNED_MODELS`, `USE_STRUCTURED_OUTPUTS`,
`LITELLM_PRIMARY`, `LANGGRAPH_CHECKPOINT_DB`, `AUTO_TUNE_*`). If a layer
errors mid-call, the orchestrator falls back to the legacy path
silently. You can flip them on one at a time and roll back without
re-deploying. See README → "Self-improvement loop" for the activation
table.

## What it is NOT

- **Not a chatbot you can deploy publicly.** Multi-tenant auth isn't
  built. Run it on your own machine or your own server.
- **Not magic.** It still makes mistakes. That's why every checkpoint
  exists. Read the plan. Read the diff. Don't auto-approve.
- **Not a replacement for understanding your code.** Treat it like a
  fast intern who will do exactly what you tell it. Garbage task →
  garbage code.
- **Not a fixed pipeline.** You can flip features off (no crew, no web
  search, no Obsidian, no Slack) and you just get a plain
  plan→code→commit loop. Or flip them all on.

## Where to look next

- New here? **Start with the dashboard.** `streamlit run ui/dashboard.py`.
- Wiring it into your team's Slack? **`README.md` → Slack bot section.**
- Building a custom UI on top? **`README.md` → HTTP shim section** (all
  endpoints documented).
- Deploying to a server? **`infrastructure/README.md`.**
- Want to understand the LangGraph state machine? **`orchestrator/graph.py`**
  — it's ~100 lines and reads top-to-bottom.
- Want the latest "what's done / what's next" list? **`TODO.md`.**

## TL;DR for the 15-year-old

> You ask a robot to fix or build something in your code. It makes a
> plan. You say yes. It writes the code. You say yes. It commits.
> It's free unless you opt into paid models. It runs on your laptop.
> You can talk to it from a website, a terminal, Slack, Telegram, or
> with your voice. It remembers what it's done. It gets smarter the
> more you use it.
