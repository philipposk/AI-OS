// Minimal SPA. Talks to the FastAPI shim at the same origin.
// All state is in plain JS; no framework.

// Same-origin but mounted at /ui — strip /ui prefix when calling backend.
const API = window.location.origin;
const TOKEN = ""; // set if you started the server with API_COMPANY_TOKEN

const state = {
  workflowId: null,
  events: [],
  pending: null,
  finished: false,
};

const $ = (sel) => document.querySelector(sel);
const elBadges = $("#badges");
const elTask = $("#task");
const elCrew = $("#crew");
const elSearch = $("#search");
const elRun = $("#run");
const elReset = $("#reset");
const elCheckpoint = $("#checkpoint");
const elTimeline = $("#timeline");
const elAccounting = $("#accounting");
const elToast = $("#toast");

function toast(text) {
  elToast.textContent = text;
  elToast.classList.add("show");
  setTimeout(() => elToast.classList.remove("show"), 2200);
}

function authHeaders() {
  const h = { "Content-Type": "application/json" };
  if (TOKEN) h["Authorization"] = `Bearer ${TOKEN}`;
  return h;
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: authHeaders(),
    ...opts,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  return res.json();
}

async function refreshBadges() {
  try {
    const h = await api("/health");
    const acc = await api("/v1/accounting");
    const provs = (h.providers || []).map(p => `<span class="badge ok">${p}</span>`).join("");
    const cost = acc.total_cost_usd != null ? `$${acc.total_cost_usd.toFixed(4)}` : "$0";
    elBadges.innerHTML = provs +
      ` <span class="badge">${acc.total_calls || 0} calls</span>` +
      ` <span class="badge">${cost}</span>`;
  } catch (e) {
    elBadges.innerHTML = `<span class="badge err">offline</span>`;
  }
}

function summariseEvent(ev) {
  for (const [node, payload] of Object.entries(ev)) {
    if (node === "interrupt") continue;
    if (typeof payload !== "object" || payload === null) continue;
    let summary = "ok";
    if (payload.analysis) summary = payload.analysis.slice(0, 160);
    else if (payload.plan?.length) summary = `${payload.plan.length} step plan`;
    else if (payload.code_changes?.length) summary = `${payload.code_changes.length} file(s) edited`;
    else if (payload.test_results) summary = `tests passed=${payload.test_results.passed}`;
    else if (payload.review_summary) summary = payload.review_summary.slice(0, 160);
    else if (payload.commit_sha) summary = `committed ${payload.commit_sha.slice(0, 7)}`;
    return { node, summary, raw: payload };
  }
  return null;
}

function renderTimeline() {
  if (!state.events.length) {
    elTimeline.innerHTML = "";
    return;
  }
  elTimeline.innerHTML = `<h2>Timeline</h2>` + state.events
    .map(summariseEvent)
    .filter(x => x)
    .map(({ node, summary, raw }) => `
      <div class="event">
        <div class="node">${node}</div>
        <div class="summary">
          ${escapeHtml(summary)}
          <details>
            <summary>raw</summary>
            <pre>${escapeHtml(JSON.stringify(raw, null, 2).slice(0, 4000))}</pre>
          </details>
        </div>
      </div>
    `).join("");
}

function renderCheckpoint() {
  if (!state.pending) {
    elCheckpoint.innerHTML = "";
    return;
  }
  const kind = state.pending.kind || "checkpoint";
  let body = "";
  if (kind === "review_plan") {
    body = `
      <div class="muted" style="font-size:13px;margin-bottom:8px;">Analysis</div>
      <div>${escapeHtml(state.pending.analysis || "")}</div>
      <div class="muted" style="font-size:13px;margin:14px 0 6px;">Plan</div>
      <ol>${(state.pending.plan || []).map(s => `
        <li><strong>${escapeHtml(s.title || "")}</strong>
          <div class="muted" style="font-size:13px;">${escapeHtml(s.detail || "")}</div>
          ${(s.files || []).length ? `<div style="font-family:var(--mono);font-size:12px;">${(s.files||[]).join(", ")}</div>` : ""}
        </li>`).join("")}</ol>`;
  } else if (kind === "review_code") {
    const tr = state.pending.test_results || {};
    body = `
      <div>Tests passed: <strong>${tr.passed === true ? "yes" : "no"}</strong></div>
      <div class="muted" style="font-size:13px;margin:8px 0 6px;">Diffs</div>
      ${(state.pending.code_changes || []).map(c => `
        <div style="margin-bottom:8px;">
          <div style="font-family:var(--mono);font-size:12px;">${escapeHtml(c.path || "")}</div>
          <pre>${escapeHtml((c.diff || "").slice(0, 4000))}</pre>
        </div>`).join("")}`;
  } else if (kind === "review_commit") {
    body = `
      <div class="muted" style="font-size:13px;">Commit message (editable)</div>
      <textarea id="commit_msg" rows="6" style="width:100%;border:1px solid var(--rule);border-radius:8px;padding:8px;font-family:var(--mono);font-size:13px;">${escapeHtml(state.pending.commit_message || "")}</textarea>`;
  } else if (kind === "budget_exceeded") {
    body = `
      <div>Spend so far: <strong>$${(state.pending.current_usd ?? 0).toFixed(4)}</strong></div>
      <div>Ceiling: <strong>$${(state.pending.limit_usd ?? 0).toFixed(4)}</strong></div>
      <div class="muted" style="font-size:13px;margin-top:8px;">Raise ceiling to (USD), blank to abort</div>
      <input id="raise_to" type="number" step="0.01" min="0" placeholder="e.g. 1.50"
             style="border:1px solid var(--rule);border-radius:8px;padding:8px;width:200px;" />`;
  } else {
    body = `<pre>${escapeHtml(JSON.stringify(state.pending, null, 2).slice(0, 4000))}</pre>`;
  }

  const actions = kind === "budget_exceeded"
    ? `<button onclick="window._approveBudget()">Raise & continue</button>
       <button class="danger" onclick="window._reject()">Abort</button>`
    : `<button onclick="window._approve()">Approve</button>
       <button class="danger" onclick="window._reject()">Reject</button>`;

  elCheckpoint.innerHTML = `
    <div class="checkpoint">
      <div class="kind">${kind.replace(/_/g, " ")}</div>
      <h3>Human checkpoint</h3>
      <div class="body">${body}</div>
      <div class="actions">${actions}</div>
    </div>`;
}

function renderAccounting() {
  api("/v1/accounting" + (state.workflowId ? `?workflow_id=${state.workflowId}` : ""))
    .then(rep => {
      const cost = rep.total_cost_usd != null ? `$${rep.total_cost_usd.toFixed(4)}` : "$0";
      elAccounting.innerHTML = `
        <div>workflow: <strong>${state.workflowId || "—"}</strong></div>
        <div>calls: <strong>${rep.total_calls || 0}</strong></div>
        <div>tokens in/out: <strong>${rep.total_prompt_tokens || 0}/${rep.total_completion_tokens || 0}</strong></div>
        <div>cost: <strong>${cost}</strong></div>`;
    })
    .catch(() => { elAccounting.innerHTML = ""; });
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function startWorkflow() {
  const task = elTask.value.trim();
  if (!task) return;
  elRun.disabled = true;
  elRun.textContent = "running…";
  try {
    const res = await api("/v1/workflows/start", {
      method: "POST",
      body: JSON.stringify({
        task,
        crew_mode: elCrew.checked,
        search_enabled: elSearch.checked,
      }),
    });
    state.workflowId = res.workflow_id;
    state.events = res.events || [];
    state.pending = res.pending;
    state.finished = res.finished;
    elReset.disabled = false;
    render();
  } catch (e) {
    toast(`error: ${e.message}`);
  } finally {
    elRun.disabled = false;
    elRun.textContent = "Run";
  }
}

async function resume(decision) {
  if (!state.workflowId) return;
  elCheckpoint.querySelectorAll("button").forEach(b => b.disabled = true);
  try {
    const res = await api(`/v1/workflows/${state.workflowId}/resume`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
    state.events.push(...(res.events || []));
    state.pending = res.pending;
    state.finished = res.finished;
    render();
    if (state.finished) toast("workflow finished");
  } catch (e) {
    toast(`error: ${e.message}`);
  }
}

window._approve = () => {
  let decision = { approved: true };
  if (state.pending?.kind === "review_commit") {
    const txt = document.getElementById("commit_msg")?.value;
    if (txt) decision.commit_message = txt;
  }
  resume(decision);
};
window._reject = () => resume({ approved: false, reason: "rejected via UI" });
window._approveBudget = () => {
  const v = parseFloat(document.getElementById("raise_to")?.value || "");
  if (Number.isFinite(v) && v > 0) {
    resume({ approved: true, raise_to: v });
  } else {
    resume({ approved: false, reason: "budget abort" });
  }
};

function reset() {
  state.workflowId = null;
  state.events = [];
  state.pending = null;
  state.finished = false;
  elReset.disabled = true;
  render();
}

function render() {
  renderCheckpoint();
  renderTimeline();
  renderAccounting();
}

elRun.addEventListener("click", startWorkflow);
elReset.addEventListener("click", reset);
elTask.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) startWorkflow();
});

refreshBadges();
setInterval(refreshBadges, 30000);
