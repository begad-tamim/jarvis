// JARVIS HUD. Vanilla JS, no dependencies, no third-party requests.
"use strict";

const $ = (id) => document.getElementById(id);
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const usd = (x, d = 2) => (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const sgnUsd = (x, d = 2) => (x >= 0 ? "+" : "") + usd(x, d);
const pct = (x, d = 2) => (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%";
const hhmm = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ------------------------------------------------------------------ auth
// The key arrives in the URL fragment (never sent to the server, never logged),
// moves to sessionStorage, and is wiped from the address bar.
let TOKEN = "";
(() => {
  const m = location.hash.match(/token=([\w-]+)/);
  if (m) {
    TOKEN = m[1];
    try { sessionStorage.setItem("jarvis.token", TOKEN); } catch {}
    history.replaceState(null, "", location.pathname);
  } else {
    try { TOKEN = sessionStorage.getItem("jarvis.token") || ""; } catch {}
  }
})();
const api = (path, opts = {}) =>
  fetch(path, { ...opts, headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", ...(opts.headers || {}) } });

// ------------------------------------------------------------------ state
let S = null;
let pending = false;
const seenThoughts = new Set();
const seenTrades = new Set();

function schedule(snap) {
  S = snap;
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => { pending = false; render(); });
}

// ------------------------------------------------------------------ stream (SSE over fetch, so the key stays in a header)
async function connect(attempt = 0) {
  if (!TOKEN) { $("gate").hidden = false; return; }
  try {
    const res = await api("/api/stream");
    if (res.status === 401) { $("gate").hidden = false; return; }
    if (!res.ok || !res.body) throw new Error(res.status);
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
        if (!data) continue;
        const obj = JSON.parse(data);
        if (ev === "snapshot") schedule(obj);
        else if (ev === "thought" && S) { S.thoughts = [...(S.thoughts || []), obj].slice(-60); schedule(S); }
      }
    }
  } catch (e) { /* fall through to reconnect */ }
  setStatus("Reconnecting to the engine…", "bad");
  setTimeout(() => connect(attempt + 1), Math.min(10000, 800 * 2 ** attempt));
}

function setStatus(text, cls) {
  $("statusText").textContent = text;
  $("statusPulse").className = "pulse " + (cls || "");
}

// ------------------------------------------------------------------ render
function render() {
  const s = S;
  if (!s) return;
  const a = s.account;

  // header
  const badge = $("modeBadge");
  badge.className = "badge" + (s.mode === "demo" ? " demo" : s.live ? " live" : "");
  const [b1, b2] = s.mode === "demo" ? ["DEMO", "SIMULATED"] : s.live ? ["LIVE", "REAL MONEY"] : ["PAPER", "TRADING"];
  badge.innerHTML = `${b1}<span class="b2"> · ${b2}</span>`;
  const statusWords = {
    watching: "Watching the market", scanning: "Scanning the universe", thinking: "Analyst is reasoning",
    paused: "Paused. Stops still protected", halted: "Halted: " + (s.halted || ""), "market closed": "Market closed. Standing by",
  };
  setStatus(s.killed ? "Kill switch engaged" : statusWords[s.status] || s.status, s.killed || s.status === "halted" ? "bad" : s.status === "market closed" || s.paused ? "" : "on");
  $("statusMeta").textContent = `scan #${s.scans} · ${hhmm(s.t)}`;
  const pause = $("pauseBtn");
  pause.disabled = false;
  pause.textContent = s.paused || s.killed ? "Resume" : "Pause";
  $("killBtn").disabled = s.killed;
  $("killBtn").querySelector(".kill-label").textContent = s.killed ? "Killed" : "Hold to Kill";

  // equity
  tween($("eqValue"), a.equity, (v) => usd(v));
  setChip($("eqTotal"), a.total_return_pct, pct(a.total_return_pct));
  setChip($("eqDay"), a.day_pl, sgnUsd(a.day_pl));
  $("kCash").textContent = usd(a.cash, 0);
  $("kExposure").textContent = (a.exposure_pct * 100).toFixed(0) + "%";
  $("kWin").textContent = s.stats.win_rate == null ? "n/a" : (s.stats.win_rate * 100).toFixed(0) + "%";
  const rz = $("kRealized");
  rz.textContent = sgnUsd(s.stats.realized_pl, 0);
  rz.className = "num " + (s.stats.realized_pl >= 0 ? "up" : "down");
  $("brokerName").textContent = `${s.broker} · ${s.stats.closed} closed trades`;
  drawEquity(s.equity, a.start_equity);

  // radar
  $("regime").textContent = s.regime === "unknown" ? "" : `regime ${s.regime}`;
  RADAR.data = s.signals.slice(0, 8);
  if (REDUCED) drawRadar(0);
  renderRanks(s.signals.slice(0, 7));

  // guardrails + thoughts
  $("analystName").textContent = `analyst: ${s.analyst}`;
  renderGuards(s);
  renderThoughts(s.thoughts);

  renderPositions(s.positions);
  renderDecision(s);
  renderTrades(s.trades);
}

function setChip(el, v, text) {
  el.textContent = text;
  el.className = "chip num " + (v > 0 ? "up" : v < 0 ? "down" : "");
}

const tweens = new WeakMap();
function tween(el, to, fmt) {
  const from = tweens.get(el)?.value ?? to;
  if (REDUCED || from === to) { el.textContent = fmt(to); tweens.set(el, { value: to }); return; }
  const t0 = performance.now(), dur = 700;
  const st = { value: from };
  tweens.set(el, st);
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - (1 - k) ** 3;
    st.value = from + (to - from) * e;
    el.textContent = fmt(st.value);
    if (k < 1 && tweens.get(el) === st) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ------------------------------------------------------------------ canvas helpers
function fit(canvas) {
  const r = canvas.getBoundingClientRect();
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: r.width, h: r.height };
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function drawEquity(points, start) {
  const { ctx, w, h } = fit($("eqChart"));
  ctx.clearRect(0, 0, w, h);
  if (!points || points.length < 2) return;
  const ys = points.map((p) => p[1]);
  let lo = Math.min(...ys, start ?? Infinity), hi = Math.max(...ys, start ?? -Infinity);
  const pad = (hi - lo) * 0.12 || hi * 0.002 || 1;
  lo -= pad; hi += pad;
  const L = 0, R = w - 64, T = 8, B = h - 18;
  const x = (i) => L + (i / (points.length - 1)) * (R - L);
  const y = (v) => B - ((v - lo) / (hi - lo)) * (B - T);
  const up = ys[ys.length - 1] >= (start ?? ys[0]);
  const col = up ? css("--up") : css("--down");

  // grid + labels
  ctx.font = `11px ${css("--mono")}`;
  ctx.fillStyle = css("--text-3");
  ctx.strokeStyle = "rgba(120,220,255,0.07)";
  ctx.lineWidth = 1;
  for (let k = 0; k <= 3; k++) {
    const v = lo + ((hi - lo) * k) / 3, yy = y(v);
    ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(R, yy); ctx.stroke();
    ctx.fillText(usd(v, 0), R + 8, yy + 4);
  }
  if (start) { // start-equity baseline
    ctx.setLineDash([4, 5]);
    ctx.strokeStyle = "rgba(230,238,245,0.25)";
    ctx.beginPath(); ctx.moveTo(L, y(start)); ctx.lineTo(R, y(start)); ctx.stroke();
    ctx.setLineDash([]);
  }
  // area
  const grad = ctx.createLinearGradient(0, T, 0, B);
  grad.addColorStop(0, up ? "rgba(61,220,151,0.28)" : "rgba(255,92,108,0.25)");
  grad.addColorStop(1, "rgba(6,9,13,0)");
  ctx.beginPath();
  points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p[1])) : ctx.moveTo(x(i), y(p[1]))));
  ctx.lineTo(R, B); ctx.lineTo(L, B); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  // line
  ctx.beginPath();
  points.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p[1])) : ctx.moveTo(x(i), y(p[1]))));
  ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
  // head
  const hx = x(points.length - 1), hy = y(ys[ys.length - 1]);
  ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 14;
  ctx.beginPath(); ctx.arc(hx, hy, 4, 0, Math.PI * 2); ctx.fill();
  ctx.shadowBlur = 0;
  // time labels
  ctx.fillStyle = css("--text-3");
  ctx.fillText(hhmm(points[0][0]), L, h - 2);
  const lbl = hhmm(points[points.length - 1][0]);
  ctx.fillText(lbl, R - ctx.measureText(lbl).width, h - 2);
}

// Radar: each spoke is a symbol, radius is its quant score. The sweep shows a live scan.
const RADAR = { data: [], angle: 0 };
function drawRadar(sweep) {
  const { ctx, w, h } = fit($("radarChart"));
  ctx.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 34;
  const accent = css("--accent");
  ctx.strokeStyle = "rgba(120,220,255,0.12)";
  ctx.lineWidth = 1;
  for (const f of [0.25, 0.5, 0.75, 1]) { ctx.beginPath(); ctx.arc(cx, cy, R * f, 0, Math.PI * 2); ctx.stroke(); }
  const d = RADAR.data, n = Math.max(d.length, 3);
  const ang = (i) => -Math.PI / 2 + (i / n) * Math.PI * 2;
  for (let i = 0; i < n; i++) {
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(ang(i)) * R, cy + Math.sin(ang(i)) * R); ctx.stroke();
  }
  // buy threshold ring
  ctx.setLineDash([3, 4]);
  ctx.strokeStyle = "rgba(61,220,151,0.45)";
  ctx.beginPath(); ctx.arc(cx, cy, R * ((45 + 100) / 200), 0, Math.PI * 2); ctx.stroke();
  ctx.setLineDash([]);
  if (!REDUCED) { // sweep
    const g = ctx.createConicGradient ? ctx.createConicGradient(sweep - Math.PI / 2.2, cx, cy) : null;
    if (g) {
      g.addColorStop(0, "rgba(95,216,245,0)");
      g.addColorStop(0.07, "rgba(95,216,245,0.16)");
      g.addColorStop(0.0701, "rgba(95,216,245,0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
    }
  }
  if (!d.length) return;
  const pt = (i) => { const r = R * Math.max(0.04, (d[i].score + 100) / 200); return [cx + Math.cos(ang(i)) * r, cy + Math.sin(ang(i)) * r]; };
  ctx.beginPath();
  d.forEach((_, i) => { const [px, py] = pt(i); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
  ctx.closePath();
  ctx.fillStyle = "rgba(95,216,245,0.12)"; ctx.fill();
  ctx.strokeStyle = accent; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.font = `600 11px ${css("--mono")}`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  d.forEach((s, i) => {
    const [px, py] = pt(i);
    const c = s.action === "buy" ? css("--up") : s.action === "sell" ? css("--down") : accent;
    ctx.fillStyle = c;
    if (s.action === "buy") { ctx.shadowColor = c; ctx.shadowBlur = 12; }
    ctx.beginPath(); ctx.arc(px, py, s.action === "buy" ? 4.5 : 3, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = s.action === "buy" ? css("--up") : css("--text-2");
    ctx.fillText(s.symbol, cx + Math.cos(ang(i)) * (R + 15), cy + Math.sin(ang(i)) * (R + 13));
  });
}
if (!REDUCED) {
  let last = 0;
  const loop = (now) => {
    if (!document.hidden && now - last > 33) { // ~30fps is plenty for a sweep
      RADAR.angle = (RADAR.angle + 0.035) % (Math.PI * 2);
      drawRadar(RADAR.angle);
      last = now;
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}


// Styles go through the CSSOM (allowed by our strict CSP), never style="" attributes.
function hydrate(root) {
  root.querySelectorAll("[data-sx]").forEach((el) => { el.style.transform = `scaleX(${el.dataset.sx})`; });
  root.querySelectorAll("[data-left]").forEach((el) => { el.style.left = el.dataset.left + "%"; });
  root.querySelectorAll("[data-p]").forEach((el) => { el.style.setProperty("--p", el.dataset.p); });
  root.querySelectorAll('[data-still="1"]').forEach((el) => { el.style.animation = "none"; });
  return root;
}

// ------------------------------------------------------------------ lists
function renderRanks(sigs) {
  const max = 100;
  $("ranks").innerHTML = sigs.map((s) => `
    <li class="${s.action}">
      <span class="sym">${esc(s.symbol)}</span>
      <span class="bar ${s.score < 0 ? "neg" : ""}" data-sx="${Math.max(0.02, Math.abs(s.score) / max)}"></span>
      <span class="sc">${s.score > 0 ? "+" : ""}${s.score.toFixed(0)}</span>
    </li>`).join(""); hydrate($("ranks"));
}

function renderGuards(s) {
  const a = s.account, r = s.risk;
  const rows = [
    ["Daily loss", Math.max(0, -a.day_pl_pct) / r.daily_loss_limit, `${(Math.max(0, -a.day_pl_pct) * 100).toFixed(1)}/${(r.daily_loss_limit * 100).toFixed(0)}%`],
    ["Drawdown", a.drawdown_pct / r.max_drawdown, `${(a.drawdown_pct * 100).toFixed(1)}/${(r.max_drawdown * 100).toFixed(0)}%`],
    ["Invested", a.exposure_pct / 0.95, `${(a.exposure_pct * 100).toFixed(0)}%`],
    ["Positions", s.positions.length / r.max_positions, `${s.positions.length}/${r.max_positions}`],
    ["Entries today", r.trades_today / r.max_trades, `${r.trades_today}/${r.max_trades}`],
    ["Risk per trade", r.risk_per_trade / 0.05, `${(r.risk_per_trade * 100).toFixed(1)}%`],
  ];
  $("guards").innerHTML = rows.map(([k, f, v]) => {
    const cls = f >= 1 ? "max" : f >= 0.7 ? "hot" : "";
    return `<div class="guard">${k}<b>${v}</b><div class="meter"><i class="${cls}" data-sx="${Math.min(1, Math.max(0, f))}"></i></div></div>`;
  }).join("");
  hydrate($("guards"));
}

function renderThoughts(list) {
  const ol = $("stream");
  const items = (list || []).slice(-40).reverse();
  const html = items.map((t) => {
    const key = t.t + t.text;
    const fresh = !seenThoughts.has(key);
    seenThoughts.add(key);
    return `<li class="k-${esc(t.kind)}" data-still="${fresh ? 0 : 1}"><time>${hhmm(t.t)}</time>${t.symbol ? `<span class="tag">${esc(t.symbol)}</span>` : ""}${esc(t.text)}</li>`;
  }).join("");
  if (ol.dataset.h !== String(items.length) + (items[0]?.text || "")) {
    ol.innerHTML = html;
    hydrate(ol);
    ol.dataset.h = String(items.length) + (items[0]?.text || "");
  }
}

function renderPositions(pos) {
  $("posCount").textContent = pos.length ? `${pos.length} open` : "";
  $("posEmpty").hidden = pos.length > 0;
  $("posBody").innerHTML = pos.map((p) => {
    const lo = p.stop ?? p.price * 0.95, hi = p.target ?? p.price * 1.08;
    const f = Math.min(1, Math.max(0, (p.price - lo) / (hi - lo || 1)));
    return `<tr>
      <td><span class="sym">${esc(p.symbol)}</span><span class="sub">${p.qty} @ ${usd(p.avg)}</span></td>
      <td class="r num">${usd(p.value, 0)}</td>
      <td class="r num ${p.pl >= 0 ? "up" : "down"}">${pct(p.plpc, 1)}<span class="sub">${sgnUsd(p.pl, 0)}</span></td>
      <td><div class="ladder" title="stop ${p.stop ?? "n/a"} · price ${p.price} · target ${p.target ?? "n/a"}">
        <span class="lo">${p.stop ? usd(p.stop, 0) : ""}</span><span class="hi">${p.target ? usd(p.target, 0) : "trailing"}</span>
        <span class="rail"></span><span class="dot" data-left="${(f * 100).toFixed(1)}"></span></div></td>
    </tr>`;
  }).join("");
  hydrate($("posBody"));
}

function renderDecision(s) {
  // Most recent decision that says something (skip routine cooldown/duplicate vetoes).
  const routine = new Set(["cooldown", "duplicate"]);
  const dec = [...s.decisions].reverse().find((d) => d.thesis && !routine.has(d.verdict.rule)) || null;
  const th = dec?.thesis || s.theses.find((t) => t.action === "buy" || t.action === "sell") || s.theses[0];
  if (!th) return;
  const v = dec?.verdict;
  $("decTime").textContent = dec ? hhmm(dec.t) : "";
  const conv = Math.round(th.conviction * 100);
  $("decBody").innerHTML = `
    <div class="dec-top">
      <div class="ring" data-p="${conv}"><span>${conv}</span></div>
      <div>
        <div><span class="dec-sym">${esc(th.symbol)}</span><span class="dec-act ${th.action === "buy" ? "buy" : th.action === "sell" ? "sell" : "pass"}">${esc(th.action.toUpperCase())}</span></div>
        <div class="dec-src">conviction ${conv}% · ${esc(th.source)}</div>
      </div>
    </div>
    <p class="dec-thesis">${esc(th.thesis)}</p>
    ${th.risks?.length ? `<ul class="dec-risks">${th.risks.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    ${v ? `<div class="verdict ${v.approved ? "ok" : "no"}"><b>${v.approved ? "RISK APPROVED" : "RISK VETO"}</b><span>${esc(v.approved ? `stop ${usd(v.stop)} · target ${v.target ? usd(v.target) : "trailing"}` : v.message)}</span>${v.approved ? `<span class="num">${usd(v.notional, 0)}</span>` : ""}</div>` : ""}`;
  hydrate($("decBody"));
}

function renderTrades(trades) {
  $("tapeCount").textContent = trades.length ? `${trades.length} recent` : "";
  $("trades").innerHTML = trades.slice(0, 30).map((t) => {
    const key = t.t + t.symbol + t.side;
    const fresh = !seenTrades.has(key);
    seenTrades.add(key);
    const amt = t.side === "sell" && t.pnl != null
      ? `<span class="amt ${t.pnl >= 0 ? "up" : "down"}">${sgnUsd(t.pnl, 0)}</span>`
      : `<span class="amt">${usd(t.notional, 0)}</span>`;
    return `<li data-still="${fresh ? 0 : 1}"><span class="side ${t.side}">${t.side.toUpperCase()}</span><span class="sym">${esc(t.symbol)}</span><span class="why" title="${esc(t.reason)}">${esc(t.reason)}</span>${amt}</li>`;
  }).join("") || `<li class="empty none">No executions yet.</li>`;
  hydrate($("trades"));
}

// ------------------------------------------------------------------ controls
$("pauseBtn").addEventListener("click", async () => {
  if (!S) return;
  await api(S.paused || S.killed ? "/api/resume" : "/api/pause", { method: "POST", body: "{}" });
});

(() => { // press-and-hold kill switch: impossible to trigger by a stray click
  const btn = $("killBtn");
  let timer = null;
  const start = (e) => {
    if (btn.disabled) return;
    e.preventDefault();
    btn.classList.add("holding");
    timer = setTimeout(async () => {
      btn.classList.remove("holding");
      btn.classList.add("armed");
      await api("/api/kill", { method: "POST", body: JSON.stringify({ reason: "kill switch pressed on the dashboard", liquidate: true }) });
      setTimeout(() => btn.classList.remove("armed"), 1500);
    }, 1000);
  };
  const cancel = () => { clearTimeout(timer); btn.classList.remove("holding"); };
  btn.addEventListener("pointerdown", start);
  btn.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) start(e); });
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => btn.addEventListener(ev, cancel));
  btn.addEventListener("keyup", cancel);
})();

window.addEventListener("resize", () => S && schedule(S));
connect();
