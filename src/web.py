"""FastAPI dashboard — view run history, job status, trigger manual runs."""

import subprocess
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent))
from tracker import get_jobs, get_recent_runs, get_stats, init_db

ROOT = Path(__file__).parent.parent
LOG_FILE = ROOT / "data" / "agent.log"

app = FastAPI()
_proc: subprocess.Popen | None = None


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def api_stats():
    try:
        init_db()
        return get_stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/runs")
def api_runs():
    try:
        init_db()
        return get_recent_runs(20)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/jobs")
def api_jobs(status: str = "all", limit: int = 100):
    try:
        init_db()
        return get_jobs(status, limit)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run")
def api_trigger(dry_run: bool = False, test: bool = False):
    global _proc
    if _proc and _proc.poll() is None:
        return {"ok": False, "message": "Agent is already running"}

    cmd = ["uv", "run", "python", "src/agent.py"]
    if dry_run:
        cmd.append("--dry-run")
    if test:
        cmd.append("--test")

    LOG_FILE.parent.mkdir(exist_ok=True)
    log_f = open(LOG_FILE, "w", buffering=1)
    _proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log_f, stderr=log_f)
    return {"ok": True, "pid": _proc.pid}


@app.get("/api/status")
def api_status():
    running = _proc is not None and _proc.poll() is None
    exit_code = _proc.poll() if _proc else None
    return {"running": running, "exit_code": exit_code, "pid": _proc.pid if _proc else None}


@app.post("/api/stop")
def api_stop():
    global _proc
    if _proc is None or _proc.poll() is not None:
        return {"ok": False, "message": "No running process"}
    _proc.terminate()
    try:
        _proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _proc.kill()
    return {"ok": True}


@app.get("/api/logs")
def api_logs(lines: int = 200):
    if not LOG_FILE.exists():
        return {"lines": []}
    text = LOG_FILE.read_text(errors="replace")
    return {"lines": text.splitlines()[-lines:]}


# ── Dashboard HTML ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Job Search AI</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = { darkMode: 'class' }
</script>
<style>
  body { font-family: 'Inter', system-ui, sans-serif; }
  .badge-applied  { @apply bg-green-900 text-green-300; }
  .badge-failed   { @apply bg-red-900 text-red-300; }
  .badge-skipped  { @apply bg-gray-700 text-gray-400; }
  .badge-manual   { @apply bg-yellow-900 text-yellow-300; }
  .badge-dry_run  { @apply bg-blue-900 text-blue-300; }
  pre { white-space: pre-wrap; word-break: break-all; }
</style>
</head>
<body class="dark bg-gray-950 text-gray-100 min-h-screen">

<div class="max-w-7xl mx-auto px-6 py-8">

  <!-- Header -->
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-2xl font-bold text-white">Job Search AI</h1>
      <p class="text-gray-400 text-sm mt-1">Naukri.com auto-apply agent</p>
    </div>
    <div class="flex items-center gap-3">
      <label class="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
        <input type="checkbox" id="chkTest" class="accent-blue-500"> Test mode
      </label>
      <label class="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
        <input type="checkbox" id="chkDry" class="accent-blue-500"> Dry run
      </label>
      <button id="stopBtn" onclick="stopRun()"
        class="hidden bg-red-700 hover:bg-red-600 px-5 py-2 rounded-lg font-medium text-sm transition flex items-center gap-2">
        <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
        Stop
      </button>
      <button id="runBtn" onclick="triggerRun()"
        class="bg-green-600 hover:bg-green-500 disabled:bg-gray-700 disabled:text-gray-500
               px-5 py-2 rounded-lg font-medium text-sm transition flex items-center gap-2">
        <svg id="runSpinner" class="hidden animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
        </svg>
        <span id="runBtnLabel">Run Now</span>
      </button>
    </div>
  </div>

  <!-- Stats -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
    <div class="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <p class="text-gray-400 text-xs uppercase tracking-wide mb-1">Total Applied</p>
      <p id="stat-applied" class="text-3xl font-bold text-green-400">—</p>
    </div>
    <div class="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <p class="text-gray-400 text-xs uppercase tracking-wide mb-1">Total Jobs Found</p>
      <p id="stat-found" class="text-3xl font-bold text-blue-400">—</p>
    </div>
    <div class="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <p class="text-gray-400 text-xs uppercase tracking-wide mb-1">Total Runs</p>
      <p id="stat-runs" class="text-3xl font-bold text-purple-400">—</p>
    </div>
    <div class="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <p class="text-gray-400 text-xs uppercase tracking-wide mb-1">Last Run</p>
      <p id="stat-last" class="text-sm font-medium text-gray-300 mt-2">—</p>
    </div>
  </div>

  <!-- Log panel -->
  <div id="logPanel" class="hidden bg-gray-900 rounded-xl border border-gray-800 mb-8">
    <div class="flex items-center justify-between px-5 py-3 border-b border-gray-800">
      <span class="font-medium text-sm">Live Log</span>
      <span id="runStatus" class="text-xs text-green-400 flex items-center gap-1">
        <span class="inline-block h-2 w-2 rounded-full bg-green-400 animate-pulse"></span>
        Running…
      </span>
    </div>
    <pre id="logOutput" class="text-xs text-gray-300 p-4 overflow-y-auto h-56 font-mono leading-relaxed"></pre>
  </div>

  <!-- Jobs table -->
  <div class="bg-gray-900 rounded-xl border border-gray-800 mb-8">
    <div class="flex items-center justify-between px-5 py-4 border-b border-gray-800">
      <h2 class="font-semibold">Jobs</h2>
      <div class="flex gap-2 text-xs">
        <button onclick="setTab('all')"      id="tab-all"     class="tab-btn active-tab px-3 py-1 rounded-md">All</button>
        <button onclick="setTab('applied')"  id="tab-applied" class="tab-btn px-3 py-1 rounded-md">Applied</button>
        <button onclick="setTab('failed')"   id="tab-failed"  class="tab-btn px-3 py-1 rounded-md">Failed</button>
        <button onclick="setTab('manual_required')" id="tab-manual_required" class="tab-btn px-3 py-1 rounded-md">Manual</button>
        <button onclick="setTab('skipped')"  id="tab-skipped" class="tab-btn px-3 py-1 rounded-md">Skipped</button>
      </div>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="text-xs text-gray-400 uppercase border-b border-gray-800">
          <tr>
            <th class="px-5 py-3 text-left">Job</th>
            <th class="px-5 py-3 text-left">Company</th>
            <th class="px-5 py-3 text-left">Location</th>
            <th class="px-5 py-3 text-left">Score</th>
            <th class="px-5 py-3 text-left">Status</th>
            <th class="px-5 py-3 text-left">Seen</th>
          </tr>
        </thead>
        <tbody id="jobsBody" class="divide-y divide-gray-800"></tbody>
      </table>
      <p id="jobsEmpty" class="hidden text-center text-gray-500 py-8 text-sm">No jobs found.</p>
    </div>
  </div>

  <!-- Recent Runs -->
  <div class="bg-gray-900 rounded-xl border border-gray-800">
    <div class="px-5 py-4 border-b border-gray-800">
      <h2 class="font-semibold">Recent Runs</h2>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="text-xs text-gray-400 uppercase border-b border-gray-800">
          <tr>
            <th class="px-5 py-3 text-left">Started</th>
            <th class="px-5 py-3 text-left">Duration</th>
            <th class="px-5 py-3 text-left">Found</th>
            <th class="px-5 py-3 text-left">Applied</th>
            <th class="px-5 py-3 text-left">Failed</th>
          </tr>
        </thead>
        <tbody id="runsBody" class="divide-y divide-gray-800"></tbody>
      </table>
    </div>
  </div>

</div>

<script>
let currentTab = 'all';
let logInterval = null;

// ── Tab ──────────────────────────────────────────────────────────────────────
function setTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active-tab', 'bg-gray-700', 'text-white'));
  const active = document.getElementById('tab-' + tab);
  if (active) active.classList.add('active-tab', 'bg-gray-700', 'text-white');
  loadJobs();
}

// ── Stats ────────────────────────────────────────────────────────────────────
async function loadStats() {
  const r = await fetch('/api/stats').then(r => r.json()).catch(() => null);
  if (!r) return;
  document.getElementById('stat-applied').textContent = r.total_applied ?? '—';
  document.getElementById('stat-found').textContent   = r.total_found ?? '—';
  document.getElementById('stat-runs').textContent    = r.runs_total ?? '—';
  document.getElementById('stat-last').textContent    = r.last_run_at
    ? new Date(r.last_run_at + 'Z').toLocaleString() : '—';
}

// ── Jobs ─────────────────────────────────────────────────────────────────────
function statusBadge(s) {
  const map = {
    applied:  'bg-green-900 text-green-300',
    failed:   'bg-red-900 text-red-300',
    skipped:  'bg-gray-700 text-gray-400',
    manual_required: 'bg-yellow-900 text-yellow-300',
    dry_run:  'bg-blue-900 text-blue-300',
    seen:     'bg-gray-800 text-gray-500',
  };
  const cls = map[s] || 'bg-gray-800 text-gray-400';
  return `<span class="px-2 py-0.5 rounded-full text-xs font-medium ${cls}">${s.replace('_',' ')}</span>`;
}

async function loadJobs() {
  const url = '/api/jobs?status=' + currentTab + '&limit=100';
  const jobs = await fetch(url).then(r => r.json()).catch(() => []);
  const tbody = document.getElementById('jobsBody');
  const empty = document.getElementById('jobsEmpty');
  if (!jobs.length) {
    tbody.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  tbody.innerHTML = jobs.map(j => `
    <tr class="hover:bg-gray-800/50 transition">
      <td class="px-5 py-3 font-medium text-white max-w-xs truncate" title="${j.title}">${j.title}</td>
      <td class="px-5 py-3 text-gray-300">${j.company}</td>
      <td class="px-5 py-3 text-gray-400 text-xs">${j.location || '—'}</td>
      <td class="px-5 py-3">
        ${j.score ? `<span class="text-${j.score >= 72 ? 'green' : 'gray'}-400 font-mono font-bold">${j.score}</span>` : '—'}
      </td>
      <td class="px-5 py-3">${statusBadge(j.status)}</td>
      <td class="px-5 py-3 text-gray-500 text-xs">${j.seen_at ? j.seen_at.split('T')[0] : '—'}</td>
    </tr>`).join('');
}

// ── Runs ─────────────────────────────────────────────────────────────────────
async function loadRuns() {
  const runs = await fetch('/api/runs').then(r => r.json()).catch(() => []);
  const tbody = document.getElementById('runsBody');
  if (!runs.length) { tbody.innerHTML = '<tr><td colspan="5" class="px-5 py-6 text-center text-gray-500">No runs yet.</td></tr>'; return; }
  tbody.innerHTML = runs.map(r => {
    const start = new Date(r.started_at + 'Z');
    const end   = r.finished_at ? new Date(r.finished_at + 'Z') : null;
    const dur   = end ? Math.round((end - start) / 60000) + 'm' : '—';
    return `<tr class="hover:bg-gray-800/50 transition">
      <td class="px-5 py-3 text-gray-300 text-xs">${start.toLocaleString()}</td>
      <td class="px-5 py-3 text-gray-400 text-xs">${dur}</td>
      <td class="px-5 py-3 text-blue-400 font-mono">${r.jobs_found ?? 0}</td>
      <td class="px-5 py-3 text-green-400 font-mono font-bold">${r.jobs_applied ?? 0}</td>
      <td class="px-5 py-3 text-red-400 font-mono">${r.jobs_failed ?? 0}</td>
    </tr>`;
  }).join('');
}

// ── Stop ─────────────────────────────────────────────────────────────────────
async function stopRun() {
  const res = await fetch('/api/stop', { method: 'POST' }).then(r => r.json()).catch(() => null);
  if (res?.ok) {
    document.getElementById('runStatus').innerHTML =
      '<span class="text-red-400">Stopped by user</span>';
  }
}

// ── Run Now ───────────────────────────────────────────────────────────────────
async function triggerRun() {
  const dry  = document.getElementById('chkDry').checked;
  const test = document.getElementById('chkTest').checked;
  const res  = await fetch(`/api/run?dry_run=${dry}&test=${test}`, { method: 'POST' }).then(r => r.json());
  if (!res.ok) { alert(res.message); return; }
  startPolling();
}

function startPolling() {
  setRunning(true);
  if (logInterval) clearInterval(logInterval);
  logInterval = setInterval(pollStatus, 2000);
}

async function pollStatus() {
  const st = await fetch('/api/status').then(r => r.json()).catch(() => null);
  if (!st) return;

  // Update log
  const logs = await fetch('/api/logs?lines=200').then(r => r.json()).catch(() => ({ lines: [] }));
  const pre = document.getElementById('logOutput');
  pre.textContent = logs.lines.join('\\n');
  pre.scrollTop = pre.scrollHeight;

  if (!st.running) {
    clearInterval(logInterval);
    logInterval = null;
    setRunning(false);
    document.getElementById('runStatus').innerHTML =
      `<span class="text-gray-400">Finished (exit ${st.exit_code ?? '?'})</span>`;
    loadStats();
    loadJobs();
    loadRuns();
  }
}

function setRunning(on) {
  const btn    = document.getElementById('runBtn');
  const stopBtn = document.getElementById('stopBtn');
  const label  = document.getElementById('runBtnLabel');
  const spin   = document.getElementById('runSpinner');
  const panel  = document.getElementById('logPanel');
  const status = document.getElementById('runStatus');
  btn.disabled = on;
  label.textContent = on ? 'Running…' : 'Run Now';
  spin.classList.toggle('hidden', !on);
  stopBtn.classList.toggle('hidden', !on);
  panel.classList.toggle('hidden', !on);
  status.innerHTML = on
    ? `<span class="inline-block h-2 w-2 rounded-full bg-green-400 animate-pulse mr-1"></span>Running…`
    : '';
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  // Highlight the default tab
  setTab('all');
  await Promise.all([loadStats(), loadRuns()]);
  // Check if already running (page reload during a run)
  const st = await fetch('/api/status').then(r => r.json()).catch(() => null);
  if (st?.running) startPolling();
  // Auto-refresh stats every 30s
  setInterval(() => { loadStats(); loadRuns(); }, 30000);
}

init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    uvicorn.run("web:app", host="0.0.0.0", port=8080, reload=False)
