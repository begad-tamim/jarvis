// JARVIS HUD core: auth, live stream, router, home, projects, console and voice.
// Vanilla JS, no dependencies, no third-party requests.
"use strict";

const $ = (id) => document.getElementById(id);
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const usd = (x, d = 2) => (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const hhmm = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

// ------------------------------------------------------------------ auth
// The key arrives in the URL fragment (never sent to the server, never logged),
// moves to localStorage on this device, and is wiped from the address bar.
let TOKEN = "";
(() => {
  const m = location.hash.match(/token=([\w-]+)/);
  if (m) {
    TOKEN = m[1];
    store.set("jarvis.token", TOKEN);
    history.replaceState(null, "", location.pathname + "#home");
  } else {
    TOKEN = store.get("jarvis.token", "");
  }
})();
const api = (path, opts = {}) =>
  fetch(path, { ...opts, headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", ...(opts.headers || {}) } });
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

// ------------------------------------------------------------------ state
const H = { hub: null, project: null, messages: [], busy: 0, speak: store.get("jarvis.speak", "0") === "1" };
const STATE = {
  attention: ["Needs attention", "attention"], active: ["In progress", "active"],
  ready: ["Ready", "ready"], quiet: ["Quiet", "quiet"], unknown: ["Unknown", "quiet"],
};

async function loadHub() {
  const r = await api("/api/hub");
  if (r.status === 401) { $("gate").hidden = false; return; }
  H.hub = await r.json();
  H.messages = H.hub.messages || [];
  H.busy = H.hub.busy;
  renderChrome();
  renderHome();
  renderMessages(true);
  if (H.hub.active && !H.project) loadProject(H.hub.active, false);
  route();
}

// ------------------------------------------------------------------ live stream (SSE over fetch, so the key stays in a header)
async function connect(attempt = 0) {
  if (!TOKEN) { $("gate").hidden = false; return; }
  try {
    const res = await api("/api/stream");
    if (res.status === 401) { $("gate").hidden = false; return; }
    if (!res.ok || !res.body) throw new Error(res.status);
    setLink(true);
    if (attempt) loadHub();
    attempt = 0;
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i);
        buf = buf.slice(i + 2);
        let ev = "message", data = "";
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event: ")) ev = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (data) onEvent(ev, JSON.parse(data));
      }
    }
  } catch (e) { /* reconnect below */ }
  setLink(false);
  setTimeout(() => connect(attempt + 1), Math.min(10000, 800 * 2 ** attempt));
}

function onEvent(ev, d) {
  if (ev === "snapshot") return window.Markets?.update(d);
  if (ev === "thought") return window.Markets?.thought(d);
  if (ev === "message") {
    if (!H.messages.some((m) => m.id === d.id)) { H.messages.push(d); renderMessages(); }
    if (d.role === "jarvis") onReply(d);
    return;
  }
  if (ev === "busy") { H.busy = d.busy; renderBusy(); return; }
  if ((ev === "notes" || ev === "tasks") && H.project?.id === d.project) { H.project[ev] = d[ev]; renderProject(); return; }
  if (ev === "active") { if (H.hub) H.hub.active = d.project; renderContext(); }
}

function setLink(ok) {
  $("linkPulse").className = "pulse " + (ok ? "on" : "bad");
  $("linkText").textContent = ok ? "Connected" : "Reconnecting";
}

// ------------------------------------------------------------------ router
const VIEWS = ["home", "projects", "markets", "ask"];
function route() {
  let v = (location.hash.slice(1) || "home").split("/")[0];
  if (!VIEWS.includes(v)) v = "home";
  if (v === "ask" && matchMedia("(min-width: 1081px)").matches) { openConsole(true); v = document.body.dataset.view === "ask" ? "home" : document.body.dataset.view || "home"; }
  document.body.dataset.view = v;
  if (matchMedia("(max-width: 1080px)").matches && v !== "ask") openConsole(false);
  document.querySelectorAll("[data-nav]").forEach((a) => a.setAttribute("aria-current", a.dataset.nav === v ? "page" : "false"));
  const sub = location.hash.split("/")[1];
  if (v === "projects" && sub && sub !== H.project?.id) loadProject(sub, true);
  if (v === "projects" && !sub && !H.project && H.hub?.projects?.length) loadProject(H.hub.projects[0].id, false);
  if (v === "markets") requestAnimationFrame(() => window.Markets?.redraw());
  if (v === "ask") setTimeout(() => $("prompt").focus(), 50);
  renderContext();
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ chrome
function renderChrome() {
  const h = H.hub;
  $("brainName").textContent = h.brain.label;
  $("demoBadge").hidden = !h.demo;
  document.body.classList.toggle("no-markets", !h.markets);
  $("mkOff").hidden = h.markets;
  $("mkGrid").hidden = !h.markets;
  if (!h.markets) $("killBtn").disabled = $("pauseBtn").disabled = true;
  $("speakBtn").setAttribute("aria-pressed", String(H.speak));
  $("speakBtn").textContent = H.speak ? "Voice on" : "Voice off";
}

function greeting() {
  const hr = new Date().getHours();
  return hr < 5 ? "Working late." : hr < 12 ? "Good morning." : hr < 18 ? "Good afternoon." : "Good evening.";
}

// ------------------------------------------------------------------ home
function renderHome() {
  const h = H.hub, b = h.briefing;
  $("today").textContent = new Date().toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }).toUpperCase();
  $("hello").textContent = greeting();
  $("headline").textContent = b.headline;
  const attention = b.buckets.attention?.length || 0;
  const tiles = [
    ["Projects", b.projects, ""],
    ["Need attention", attention, attention ? "warn" : ""],
    ["Commits this week", b.commits_7d, ""],
    ["Open tasks", b.tasks_open, ""],
  ];
  if (b.markets) tiles.push(["Portfolio today", (b.markets.day_pnl >= 0 ? "+" : "-") + usd(Math.abs(b.markets.day_pnl), 0), b.markets.day_pnl >= 0 ? "up" : "down"]);
  $("tiles").innerHTML = tiles.map(([k, v, c]) => `<div class="tile ${c}"><dt>${k}</dt><dd class="num">${esc(v)}</dd></div>`).join("");

  const focus = [...(b.buckets.attention || []), ...(b.buckets.active || [])].slice(0, 5);
  $("focusCount").textContent = focus.length ? `${focus.length} items` : "";
  $("focus").innerHTML = focus.map((f) => {
    const st = h.projects.find((p) => p.id === f.id)?.status.state || "active";
    return `<li><a href="#projects/${esc(f.id)}" class="focus-row"><span class="dot ${STATE[st][1]}"></span><span class="f-name">${esc(f.name)}</span><span class="f-why">${esc(f.reason)}</span><span class="f-go" aria-hidden="true">→</span></a></li>`;
  }).join("") || `<li class="empty">Nothing needs you right now. Everything is committed and in sync.</li>`;

  $("projCount").textContent = `${h.projects.length} found`;
  $("cards").innerHTML = h.projects.map((p) => {
    const st = p.status, [label, cls] = STATE[st.state] || STATE.unknown;
    return `<a class="card" href="#projects/${esc(p.id)}">
      <div class="card-top"><span class="card-name">${esc(p.name)}</span><span class="state-pill ${cls}">${label}</span></div>
      <p class="card-desc">${esc(p.description || "No README description yet.")}</p>
      <p class="card-why">${esc(st.reason)}</p>
      <div class="card-meta num"><span>${esc(st.branch || "-")}</span><span>${esc(p.last_commit_ago)}</span><span>${st.todos} TODO</span></div>
      <div class="stack">${p.stack.map((s) => `<span>${esc(s)}</span>`).join("")}</div>
    </a>`;
  }).join("") || `<p class="empty">No projects found. Add folders to <code>project_roots</code> in jarvis.toml.</p>`;
  $("marketsMini").hidden = !h.markets;
  $("marketsTab").hidden = false;
  renderWsList();
}

window.JARVIS = {
  marketsMini(s) {
    if (!s || $("marketsMini").hidden) return;
    const a = s.account;
    $("mmEq").textContent = usd(a.equity, 0);
    const day = $("mmDay");
    day.textContent = (a.day_pl >= 0 ? "+" : "-") + usd(Math.abs(a.day_pl), 0) + " today";
    day.className = "chip num " + (a.day_pl > 0 ? "up" : a.day_pl < 0 ? "down" : "");
    $("mmPos").textContent = `${s.positions.length} positions · ${s.status}`;
    const t = [...(s.thoughts || [])].reverse().find((x) => x.kind !== "scan");
    $("mmThought").textContent = t ? (t.symbol ? t.symbol + " · " : "") + t.text : "";
    if (document.body.dataset.view === "home") spark($("mmSpark"), s.equity, a.start_equity);
  },
};

function spark(canvas, pts, start) {
  if (!pts || pts.length < 2) return;
  const { ctx, w, h } = fit(canvas);
  ctx.clearRect(0, 0, w, h);
  const ys = pts.map((p) => p[1]);
  const lo = Math.min(...ys, start), hi = Math.max(...ys, start), pad = (hi - lo) * 0.1 || 1;
  const x = (i) => (i / (pts.length - 1)) * w, y = (v) => h - ((v - lo + pad) / (hi - lo + 2 * pad)) * h;
  const col = ys[ys.length - 1] >= start ? css("--up") : css("--down");
  ctx.beginPath();
  ys.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(0, y(v))));
  ctx.strokeStyle = col; ctx.lineWidth = 1.8; ctx.stroke();
}

// ------------------------------------------------------------------ projects
function renderWsList() {
  const h = H.hub;
  if (!h) return;
  $("wsList").innerHTML = h.projects.map((p) => {
    const cls = (STATE[p.status.state] || STATE.unknown)[1];
    return `<a href="#projects/${esc(p.id)}" class="ws-item ${H.project?.id === p.id ? "on" : ""}"><span class="dot ${cls}"></span><span>${esc(p.name)}</span><span class="muted num">${esc(p.last_commit_ago)}</span></a>`;
  }).join("");
}

async function loadProject(id, activate) {
  const r = await api(`/api/project?id=${encodeURIComponent(id)}`);
  if (!r.ok) return;
  H.project = await r.json();
  if (activate) post("/api/active", { project: id });
  if (H.hub) H.hub.active = id;
  renderProject();
  renderWsList();
  renderContext();
}

function renderProject() {
  const p = H.project;
  if (!p) return;
  const st = p.status, [label, cls] = STATE[st.state] || STATE.unknown;
  $("pPath").textContent = p.path.replace(/^\/(Users|home)\/[^/]+/, "~");
  $("pTitle").textContent = p.name;
  $("pDesc").textContent = p.description || "";
  $("pStack").innerHTML = p.stack.map((s) => `<span>${esc(s)}</span>`).join("");
  $("pState").className = "state-pill big " + cls;
  $("pState").textContent = label;
  $("pTiles").innerHTML = [
    ["Uncommitted", st.dirty + st.untracked, st.dirty + st.untracked >= 8 ? "warn" : ""],
    ["Ahead / behind", `${st.ahead} / ${st.behind}`, st.behind ? "warn" : ""],
    ["Commits (7d)", st.commits_7d, ""],
    ["TODOs in code", st.todos, ""],
  ].map(([k, v, c]) => `<div class="tile ${c}"><dt>${k}</dt><dd class="num">${esc(v)}</dd></div>`).join("");
  $("pCommit").innerHTML = `<span class="muted">Last commit · ${esc(p.last_commit_ago)} · ${esc(st.branch)}</span><b>${esc(st.last_commit || "No commits yet")}</b><span class="c-why">${esc(st.reason)}</span>`;
  const quick = ["What's left before I can ship this?", "Summarize the latest changes", "Find the riskiest code", "Draft release notes"];
  $("pQuick").innerHTML = quick.map((q) => `<button type="button" class="qchip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  const open = p.tasks.filter((t) => !t.done).length;
  $("tCount").textContent = p.tasks.length ? `${open} open` : "";
  $("pTasks").innerHTML = p.tasks.map((t) => `<li><label><input type="checkbox" data-task="${esc(t.id)}" ${t.done ? "checked" : ""}><span>${esc(t.text)}</span></label></li>`).join("") || `<li class="empty">No tasks yet.</li>`;
  $("pNotes").innerHTML = p.notes.map((n) => `<li><span>${esc(n.text)}</span><time class="muted num">${new Date(n.t * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}</time></li>`).join("") || `<li class="empty">Say “remember …” and it lands here.</li>`;
}

$("pQuick").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) send(b.dataset.q); });
$("pTasks").addEventListener("change", (e) => { const id = e.target.dataset.task; if (id && H.project) post("/api/task/toggle", { project: H.project.id, id }); });
$("taskForm").addEventListener("submit", (e) => { e.preventDefault(); const v = $("taskIn").value.trim(); if (v && H.project) { post("/api/task", { project: H.project.id, text: v }); $("taskIn").value = ""; } });
$("noteForm").addEventListener("submit", (e) => { e.preventDefault(); const v = $("noteIn").value.trim(); if (v && H.project) { post("/api/note", { project: H.project.id, text: v }); $("noteIn").value = ""; } });

// ------------------------------------------------------------------ console
function md(text) { // tiny, safe markdown: escape first, then format
  const lines = esc(text).split("\n");
  let html = "", list = null;
  const inline = (s) => s.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  for (const raw of lines) {
    const l = raw.trimEnd();
    const bullet = l.match(/^\s*(?:[-•*]|\d+\.)\s+(.*)$/);
    if (bullet) { if (!list) { html += "<ul>"; list = true; } html += `<li>${inline(bullet[1])}</li>`; continue; }
    if (list) { html += "</ul>"; list = null; }
    if (!l.trim()) continue;
    const head = l.match(/^#{1,4}\s+(.*)$/);
    html += head ? `<p class="h">${inline(head[1])}</p>` : `<p>${inline(l)}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

function visibleMessages() {
  return H.messages.slice(-80);
}

function renderMessages(jump) {
  const ol = $("msgs");
  const list = visibleMessages();
  const nearBottom = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 120;
  const brain = H.hub?.brain?.label || "your model";
  const welcome = `<li class="welcome"><div class="reactor big" aria-hidden="true"><span></span><span></span><span></span></div>
    <h3>Hello. I'm JARVIS.</h3><p>I know every project you have, read the code with <b>${esc(brain)}</b>, and watch your markets. Type, or tap the mic and talk.</p>
    <div class="caps"><span><b>Projects</b>"open orbit-api"</span><span><b>Brief</b>"brief me"</span><span><b>Memory</b>"remember …"</span><span><b>Phone</b>"call me"</span></div></li>`;
  ol.innerHTML = (list.length ? "" : welcome) + list.map((m) => {
    const proj = m.project && H.hub ? H.hub.projects.find((p) => p.id === m.project)?.name : "";
    const meta = m.role === "jarvis"
      ? `${esc(m.brain || "JARVIS")}${m.ms ? ` · ${(m.ms / 1000).toFixed(1)}s` : ""}${proj ? ` · ${esc(proj)}` : ""}`
      : `${esc(m.source === "hud" ? "you" : `you · ${m.source}`)}`;
    return `<li class="msg ${m.role}${m.action?.error ? " err" : ""}"><div class="bubble">${m.role === "jarvis" ? md(m.text) : `<p>${esc(m.text)}</p>`}</div><div class="meta num">${meta} · ${hhmm(m.t)}</div></li>`;
  }).join("") + (H.busy ? `<li class="msg jarvis thinking"><div class="bubble"><span class="dots"><i></i><i></i><i></i></span> Thinking…</div></li>` : "");
  $("suggest").hidden = list.length > 0;
  if (jump || nearBottom) ol.scrollTop = ol.scrollHeight;
}

function renderBusy() {
  $("cReactor").classList.toggle("busy", H.busy > 0);
  renderMessages();
}

function renderContext() {
  const pid = H.hub?.active;
  const p = pid && H.hub.projects.find((x) => x.id === pid);
  $("cCtx").innerHTML = p ? `Working in <b>${esc(p.name)}</b>` : "Anything. Any project.";
  const sug = [["Brief me", "brief me"], ["What should I focus on today?", "What should I focus on today?"], [p ? `Status of ${p.name}` : "Open orbit-api", p ? `status of ${p.id}` : "open orbit-api"], ["How are the markets?", "markets"]];
  if (!H.hub?.markets) sug.pop();
  $("suggest").innerHTML = sug.map(([l, q]) => `<button type="button" class="qchip" data-q="${esc(q)}">${esc(l)}</button>`).join("");
}
$("suggest").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) send(b.dataset.q); });

let lastSource = "hud";
async function send(text, source) {
  text = (text || "").trim();
  if (!text) return;
  lastSource = source || (matchMedia("(max-width: 760px)").matches ? "phone" : "hud");
  if (matchMedia("(max-width: 760px)").matches) { if (document.body.dataset.view !== "ask") location.hash = "#ask"; }
  else openConsole(true);
  $("prompt").value = "";
  autosize();
  const project = H.hub?.active || null;
  try {
    const r = await post("/api/ask", { text, project, source: lastSource });
    if (!r.ok) throw new Error((await r.json()).error || r.status);
  } catch (e) {
    H.messages.push({ id: "e" + Date.now(), role: "jarvis", text: `Couldn't reach JARVIS: ${e.message}`, t: Date.now() / 1000, action: { error: true } });
    renderMessages(true);
  }
}

function onReply(m) {
  const a = m.action || {};
  if (a.view === "project" && a.project) { location.hash = `#projects/${a.project}`; }
  else if (a.view === "markets") { location.hash = "#markets"; }
  else if (a.view === "home") { loadHub(); }
  if (H.speak || lastSource === "voice") speak(m.text);
  if (["notes", "tasks"].some((k) => /Noted|Added to/.test(m.text)) && H.project) loadProject(H.project.id, false);
}

$("composer").addEventListener("submit", (e) => { e.preventDefault(); send($("prompt").value); });
$("prompt").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("prompt").value); } });
function autosize() { const t = $("prompt"); t.style.height = "auto"; t.style.height = Math.min(160, t.scrollHeight) + "px"; }
$("prompt").addEventListener("input", autosize);

function openConsole(on) {
  document.body.classList.toggle("console-open", on);
  $("askToggle").setAttribute("aria-expanded", String(on));
}
$("askToggle").addEventListener("click", () => { const on = !document.body.classList.contains("console-open"); openConsole(on); if (on) $("prompt").focus(); });
$("cClose").addEventListener("click", () => { openConsole(false); if (document.body.dataset.view === "ask") location.hash = "#home"; });
document.addEventListener("keydown", (e) => {
  const typing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName);
  if (e.key === "/" && !typing) { e.preventDefault(); openConsole(true); $("prompt").focus(); }
  if (e.key === "Escape" && document.body.classList.contains("console-open") && document.body.dataset.view !== "home" && document.body.dataset.view !== "projects") openConsole(false);
});

// ------------------------------------------------------------------ voice: talk to JARVIS, hear it answer
const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
$("micBtn").addEventListener("click", () => {
  if (!Rec) { $("prompt").placeholder = "Voice input needs Chrome, Edge or Safari over HTTPS / localhost."; return; }
  if (rec) { rec.stop(); return; }
  rec = new Rec();
  rec.lang = navigator.language || "en-US";
  rec.interimResults = true;
  rec.continuous = false;
  let final = "";
  $("micBtn").setAttribute("aria-pressed", "true");
  document.body.classList.add("listening");
  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) final += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
    }
    $("prompt").value = final + interim;
    autosize();
  };
  rec.onend = () => {
    $("micBtn").setAttribute("aria-pressed", "false");
    document.body.classList.remove("listening");
    rec = null;
    if (final.trim()) send(final, "voice");
  };
  rec.onerror = () => {};
  try { speechSynthesis.cancel(); } catch {}
  rec.start();
});

let voice = null;
function pickVoice() {
  const vs = speechSynthesis.getVoices();
  voice = vs.find((v) => /Daniel|Arthur|Oliver|George/.test(v.name) && /en-GB/.test(v.lang)) || vs.find((v) => /en-GB/.test(v.lang)) || vs.find((v) => /^en/.test(v.lang)) || null;
}
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
function speak(text) {
  if (!("speechSynthesis" in window)) return;
  const clean = text.replace(/[`*#>_]/g, "").replace(/\n+/g, ". ").slice(0, 900);
  const u = new SpeechSynthesisUtterance(clean);
  if (voice) u.voice = voice;
  u.rate = 1.04; u.pitch = 0.95;
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}
$("speakBtn").addEventListener("click", () => {
  H.speak = !H.speak;
  store.set("jarvis.speak", H.speak ? "1" : "0");
  renderChrome();
  if (!H.speak) try { speechSynthesis.cancel(); } catch {}
});

// ------------------------------------------------------------------ boot
setInterval(() => { if (!document.hidden && document.body.dataset.view === "home") loadHub(); }, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && H.hub) loadHub(); });
loadHub();
connect();
