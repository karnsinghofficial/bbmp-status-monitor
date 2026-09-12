/* BBMP / GBA Status Dashboard front-end.
 * No build step, no framework - plain fetch + DOM. Reads docs/data/status.json
 * which is written by scripts/monitor.py and committed by the GitHub Actions
 * workflow every ~5 minutes.
 */

const DATA_URL = "data/status.json?_=" + Date.now(); // cache-bust on load
const REFRESH_URL_BASE = "data/status.json";
const AUTO_REFRESH_MS = 60 * 1000; // refresh the displayed data every 60s

const OVERALL_LABELS = {
  ALL_OPERATIONAL: { icon: "🟢", text: "ALL SYSTEMS OPERATIONAL", cls: "overall-all_operational" },
  PARTIAL_OUTAGE: { icon: "🟡", text: "PARTIAL OUTAGE", cls: "overall-partial_outage" },
  MAJOR_OUTAGE: { icon: "🔴", text: "MAJOR OUTAGE", cls: "overall-major_outage" },
  CHECKING: { icon: "⚪", text: "CHECKING — no confirmed data yet", cls: "overall-checking" },
  UNKNOWN: { icon: "⚪", text: "STATUS UNKNOWN", cls: "overall-unknown" },
};

const STATUS_META = {
  OPERATIONAL: { icon: "🟢", label: "OPERATIONAL", cls: "operational" },
  DEGRADED: { icon: "🟡", label: "DEGRADED", cls: "degraded" },
  DOWN: { icon: "🔴", label: "DOWN", cls: "down" },
  UNKNOWN: { icon: "⚪", label: "UNKNOWN (checking)", cls: "unknown" },
};

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  } catch (e) {
    return iso;
  }
}

function fmtDurationSeconds(sec) {
  if (sec === null || sec === undefined) return "—";
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const parts = [];
  if (d) parts.push(d + "d");
  if (h) parts.push(h + "h");
  if (m) parts.push(m + "m");
  if (!parts.length) parts.push(s + "s");
  return parts.slice(0, 2).join(" ");
}

function fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return v.toFixed(1) + "%";
}

function fmtMs(v) {
  if (v === null || v === undefined) return "—";
  return v + " ms";
}

async function loadStatus(url) {
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error("Failed to load status.json: " + resp.status);
  return resp.json();
}

function renderOverall(overall, generatedAt) {
  const meta = OVERALL_LABELS[overall] || OVERALL_LABELS.UNKNOWN;
  const banner = document.getElementById("overallBanner");
  banner.className = "overall-banner " + meta.cls;
  document.getElementById("overallIcon").textContent = meta.icon;
  document.getElementById("overallText").textContent = meta.text;
  document.getElementById("lastUpdated").textContent = fmtTime(generatedAt);
}

function renderTimeline(container, timeline) {
  container.innerHTML = "";
  (timeline || []).forEach((state) => {
    const tick = document.createElement("div");
    tick.className = "tick " + (
      state === "UP" ? "up" :
      state === "DOWN" ? "down" :
      state === "DEGRADED" ? "degraded" : "unknown"
    );
    tick.title = state;
    container.appendChild(tick);
  });
}

function renderServices(services) {
  const grid = document.getElementById("servicesGrid");
  grid.innerHTML = "";
  const tmpl = document.getElementById("serviceCardTemplate");

  services.forEach((svc) => {
    const node = tmpl.content.cloneNode(true);
    const card = node.querySelector(".service-card");
    card.dataset.serviceId = svc.id;
    card.dataset.url = svc.url;

    const meta = STATUS_META[svc.status] || STATUS_META.UNKNOWN;
    node.querySelector(".status-dot").classList.add(meta.cls);
    node.querySelector(".service-name").textContent = svc.name;
    node.querySelector(".category-badge").textContent = svc.category;

    const statusLine = node.querySelector(".status-line");
    statusLine.textContent = meta.icon + " " + meta.label;
    statusLine.classList.add(meta.cls);

    renderTimeline(node.querySelector(".timeline"), svc.timeline_24h);

    node.querySelector(".stat-last-checked").textContent = fmtTime(svc.last_checked);
    node.querySelector(".stat-response-time").textContent = fmtMs(svc.response_time_ms);
    node.querySelector(".stat-last-success").textContent = fmtTime(svc.last_success);
    node.querySelector(".stat-last-failure").textContent = fmtTime(svc.last_failure);
    node.querySelector(".stat-uptime-24h").textContent = fmtPct(svc.uptime_24h);
    node.querySelector(".stat-uptime-7d").textContent = fmtPct(svc.uptime_7d);
    node.querySelector(".stat-uptime-30d").textContent = fmtPct(svc.uptime_30d);
    node.querySelector(".stat-outage-duration").textContent =
      svc.status === "DOWN" ? fmtDurationSeconds(svc.outage_duration_seconds) : "—";

    node.querySelector(".verified-note").textContent = svc.verified_note || "";

    const link = node.querySelector(".visit-link");
    link.href = svc.url;

    grid.appendChild(node);
  });
}

function renderIncidents(incidents) {
  const list = document.getElementById("incidentsList");
  list.innerHTML = "";
  if (!incidents || incidents.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No incidents recorded yet. This list fills in automatically once the monitor has been running for a while.";
    list.appendChild(empty);
    return;
  }
  incidents.forEach((inc) => {
    const el = document.createElement("div");
    el.className = "incident-card" + (inc.resolved_at ? " resolved" : "");
    const title = document.createElement("div");
    title.className = "incident-title";
    title.textContent = inc.resolved_at
      ? `${inc.service_name} — RESOLVED`
      : `${inc.service_name} — ONGOING`;
    const metaLine1 = document.createElement("div");
    metaLine1.className = "incident-meta";
    metaLine1.textContent = `Down: ${fmtTime(inc.started_at)}` +
      (inc.resolved_at ? ` · Recovered: ${fmtTime(inc.resolved_at)} · Duration: ${fmtDurationSeconds(inc.duration_seconds)}` : " · still ongoing");
    const metaLine2 = document.createElement("div");
    metaLine2.className = "incident-meta";
    metaLine2.textContent = `Reason: ${inc.reason || "unknown"}`;
    el.appendChild(title);
    el.appendChild(metaLine1);
    el.appendChild(metaLine2);
    list.appendChild(el);
  });
}

let currentData = null;

async function refresh() {
  const btn = document.getElementById("refreshBtn");
  btn.disabled = true;
  btn.textContent = "Refreshing…";
  try {
    const data = await loadStatus(REFRESH_URL_BASE + "?_=" + Date.now());
    currentData = data;
    renderOverall(data.overall, data.generated_at);
    renderServices(data.services);
    renderIncidents(data.incidents);
  } catch (err) {
    document.getElementById("overallText").textContent =
      "Could not load status data yet — the monitor may not have run for the first time.";
    document.getElementById("overallBanner").className = "overall-banner overall-unknown";
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ Refresh";
  }
}

/* ---------------------------------------------------------------------------
 * "Check all services now" - an APPROXIMATE, client-side, browser-based
 * reachability check. It cannot read the real HTTP status of a cross-origin
 * government site (the browser blocks that for security reasons - CORS), so
 * it uses a no-cors request and simply times whether the browser is able to
 * open a connection at all within a few seconds. This is a best-effort,
 * instant "second opinion" between the authoritative ~5-minute server-side
 * GitHub Actions checks - it is clearly labelled as approximate and never
 * overwrites the real status shown above.
 * ------------------------------------------------------------------------- */
async function quickCheckOne(url, timeoutMs = 7000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    await fetch(url, { mode: "no-cors", cache: "no-store", signal: controller.signal });
    clearTimeout(timer);
    return true; // request resolved (even as an opaque response) => reachable
  } catch (err) {
    clearTimeout(timer);
    return false;
  }
}

async function checkAllNow() {
  const btn = document.getElementById("checkNowBtn");
  const note = document.getElementById("quickCheckNote");
  btn.disabled = true;
  btn.textContent = "Checking…";
  note.hidden = false;

  const cards = Array.from(document.querySelectorAll(".service-card"));
  await Promise.all(cards.map(async (card) => {
    const resultEl = card.querySelector(".quick-check-result");
    resultEl.hidden = false;
    resultEl.textContent = "checking…";
    resultEl.className = "quick-check-result";
    const ok = await quickCheckOne(card.dataset.url);
    resultEl.textContent = ok ? "Browser check: reachable" : "Browser check: unreachable";
    resultEl.classList.add(ok ? "reachable" : "unreachable");
  }));

  btn.disabled = false;
  btn.textContent = "Check all services now";
}

document.getElementById("refreshBtn").addEventListener("click", refresh);
document.getElementById("checkNowBtn").addEventListener("click", checkAllNow);

// Fill in the repo link automatically if this page is served from GitHub Pages
(function setRepoLink() {
  const host = window.location.hostname; // e.g. "username.github.io"
  const path = window.location.pathname; // e.g. "/bbmp-status-monitor/"
  if (host.endsWith("github.io")) {
    const user = host.split(".")[0];
    const repo = path.split("/").filter(Boolean)[0];
    if (repo) {
      document.getElementById("repoLink").href = `https://github.com/${user}/${repo}`;
    }
  }
})();

refresh();
setInterval(refresh, AUTO_REFRESH_MS);
