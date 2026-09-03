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
      const res = await fetch('/api/redirector/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url_path: pathInput.value.trim(),
          headers: headers,
          method: 'POST',
        })
      });
      const data = await res.json();
      renderTrace(data);
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
      const res = await fetch('/api/sessions');
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
          <td>${sess.last_seen}</td>
          <td><span class="status-badge running">● ALIVE</span></td>
        `;
        tbody.appendChild(tr);
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" style="color:var(--crimson-glow);">Error loading fleet sessions: ${err.message}</td></tr>`;
    }
  }

  fetchFleet();
}
