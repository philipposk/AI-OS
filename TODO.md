# ai_company — TODO / pending decisions

Living list of things on hold or worth doing next. Closed items get crossed
out, not deleted, so the history of what was considered stays visible.

## Action items (you)

- [ ] **Publish `llm-free-rotator` 0.1.0 to PyPI**
  - Visit https://pypi.org/manage/account/publishing/
  - Add pending publisher: project `llm-free-rotator`, owner `philipposk`,
    repo `llm-free-rotator`, workflow `publish.yml`, environment `pypi`
  - Then: `cd "/Users/phktistakis/Devoloper Projects/llm-free-rotator"
    && git tag v0.1.0 && git push origin v0.1.0
    && gh release create v0.1.0 --generate-notes`
  - Manual fallback: `.venv/bin/python -m twine upload dist/*`
    (PyPI token at https://pypi.org/manage/account/token/)

- [ ] **Rotate exposed Anthropic key.** The key in `ai_company/.env` was
  shared in chat history. Rotate at https://console.anthropic.com when
  convenient.

- [ ] **OCI deploy dry-run.** Fill `infrastructure/terraform.tfvars` from
  `terraform.tfvars.example`, run `terraform init && terraform plan`, then
  `terraform apply` when ready.

## Decisions on hold

- [ ] **Obsidian second-brain (b) — done.** `storage/obsidian.py` mirrors
  memory.add() into `$OBSIDIAN_VAULT_PATH/ai_company/<workflow>/<id-slug>.md`
  with YAML frontmatter; analyze_node also pulls top-3 substring matches
  from the vault. Set `OBSIDIAN_VAULT_PATH` to enable. Path (a) — pointing
  [logancyang/obsidian-copilot](https://github.com/logancyang/obsidian-copilot)
  at our shim — is still optional; tracked as Phase U in the build plan.

- [ ] **Git/versioning visibility — done.** Dashboard now has a Git panel
  (recent commits, scope toggle to workflow-touched files, one-click
  revert). `tools/git_ops` gained `gh_pr_view` / `gh_pr_list` /
  `gh_issue_view` / `gh_issue_list` / `gh_context_for_task` so
  analyze_node pulls #123 + URL refs out of the user task and injects
  PR/issue body as context. MCP server still skipped.

- [ ] **Jarvis desktop assistant** ([pickle-com/glass]
  (https://github.com/pickle-com/glass) or similar). Point it at our
  FastAPI shim at `http://localhost:8765/v1`. Decide after publishing
  rotator and getting Obsidian wired.

- [ ] **Real video call with the agent.** Web-RTC peer + screen capture +
  TTS overlay. 1–2 weeks of work. Defer; current path (live dashboard
  narration + per-checkpoint screenshot/voice in Slack/Telegram) covers
  90% of the need at 10% of the cost.

## Done (recent)

- [x] Phase A–H — initial rebuild of orchestrator (commits 92e0bff → 00b3456)
- [x] Phase I — generalised free-model rotation to Groq + NVIDIA (0769a39)
- [x] Phase J — semantic memory via Ollama / sentence-transformers (13b2239)
- [x] Phase K — token streaming across all providers + dashboard (4fa6590)
- [x] Phase L — Slack bot with Block Kit checkpoint buttons (50cc71a)
- [x] Phase M — Telegram bot with inline keyboards (50f6283)
- [x] Phase N — voice (Groq Whisper STT + browser TTS) (8e52184)
- [x] Phase O+P — OpenAI-compat API shim + dashboard narration (4568be2)
- [x] Side: `llm-free-rotator` extracted to standalone GitHub repo with
  MIT licence, pyproject, GitHub Actions Trusted-Publishing workflow,
  CHANGELOG (fde3cfb on https://github.com/philipposk/llm-free-rotator)

## In progress

(none)

## Done (additional)

- [x] Phase Q — Slack + Telegram screenshot + voice-note per checkpoint (470e58f)
- [x] Phase R — Obsidian vault mirror + gh PR/issue wrappers in tools/git_ops (21b2fe4)
- [x] Phase S — Dashboard git panel (recent commits + revert + PR/issue fetch) (965cda8)
