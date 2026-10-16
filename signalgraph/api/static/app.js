/* SignalGraph dashboard: relationship graph + price/news chart + live feed. */
"use strict";

const IST_OFFSET = 19800; // charts take UTC seconds; shift so the axis reads in IST
const $ = (id) => document.getElementById(id);
const state = {
  ticker: null, meta: null, graph: null, news: [], bars: [], cy: null,
  charts: {}, selected: null, prevClose: null, barStep: 60,
};

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 2) => (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(d);
const inr = (x) => "₹" + x.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
const istTime = (iso) => new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

function mix(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
// Diverging: neutral grey midpoint, red and green poles. Full colour at |score| >= 0.6.
function scoreColor(score) {
  const t = Math.min(Math.abs(score) / 0.6, 1);
  if (t < 0.08) return css("--neutral");
  return mix(css("--neutral"), score > 0 ? css("--good") : css("--bad"), 0.25 + 0.75 * t);
}
function signalClass(score) {
  const m = state.meta;
  return score >= m.green_threshold ? "green" : score <= m.red_threshold ? "red" : "neutral";
}
function chip(impact) {
  const cls = Math.abs(impact) < 0.005 ? "zero" : impact > 0 ? "pos" : "neg";
  const icon = cls === "zero" ? "●" : impact > 0 ? "▲" : "▼";
  return `<span class="chip-impact ${cls}" title="direction × magnitude × confidence">${icon} ${fmt(impact)}</span>`;
}
const api = (path) => fetch(path).then((r) => { if (!r.ok) throw new Error(`${path}: ${r.status}`); return r.json(); });

/* ---------------- graph ---------------- */

const LEVEL = { company: 4, person: 3, parent: 3, project: 3, client: 2, partner: 2, competitor: 1, theme: 1 };
const SHAPE = { company: "round-rectangle", person: "ellipse", parent: "round-rectangle", project: "diamond",
  client: "ellipse", partner: "ellipse", competitor: "round-triangle", theme: "hexagon" };

function nodeName(id) {
  const n = state.graph?.nodes.find((x) => x.id === id);
  return n ? n.name : id;
}
function relation(src, dst) {
  const e = state.graph?.edges.find((x) => x.source === src && x.target === dst);
  return e ? e.relation : "";
}
function pathHtml(path) {
  if (!path || !path.length) return "";
  return path.map((id, i) => (i === 0 ? `<b>${esc(nodeName(id))}</b>`
    : ` <span class="muted">—${esc(relation(path[i - 1], id))}→</span> <b>${esc(nodeName(id))}</b>`)).join("");
}

function buildGraph(data) {
  const els = [
    ...data.nodes.map((n) => ({ data: { ...n, label: n.name, color: scoreColor(n.score), level: n.id === state.ticker ? 5 : LEVEL[n.type] || 1 } })),
    ...data.edges.map((e) => ({ data: { id: `${e.source}->${e.target}`, ...e } })),
  ];
  if (state.cy) state.cy.destroy();
  state.cy = cytoscape({
    container: $("cy"), elements: els, minZoom: 0.3, maxZoom: 2.5,
    style: [
      { selector: "node", style: {
        "background-color": "data(color)", label: "data(label)", color: css("--ink"), "font-size": 14,
        "text-valign": "bottom", "text-margin-y": 5, "text-wrap": "wrap", "text-max-width": 130,
        shape: (n) => SHAPE[n.data("type")] || "ellipse",
        width: "mapData(exposure, 0, 1, 30, 62)", height: "mapData(exposure, 0, 1, 30, 62)",
        "border-color": css("--ink-3"), "border-width": "mapData(news_count, 0, 8, 1, 6)",
        "text-outline-color": css("--surface"), "text-outline-width": 2,
      } },
      { selector: `node[id = "${state.ticker}"]`, style: { width: 96, height: 60, "font-size": 16, "font-weight": 700, "border-width": 3, "border-color": css("--ink") } },
      { selector: "edge", style: {
        width: "mapData(weight, 0, 1, 1, 5)", "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.8,
        "line-color": (e) => { const s = e.source().data("score"); return Math.abs(s) > 0.1 ? scoreColor(s) : css("--border"); },
        "target-arrow-color": (e) => { const s = e.source().data("score"); return Math.abs(s) > 0.1 ? scoreColor(s) : css("--border"); },
        "line-style": (e) => (e.data("sign") < 0 ? "dashed" : "solid"), opacity: 0.85,
      } },
      { selector: "edge.show-label, edge:selected", style: { label: "data(relation)", "font-size": 12, color: css("--ink-2"),
        "text-background-color": css("--surface"), "text-background-opacity": 1, "text-background-padding": 2 } },
      { selector: "node:selected", style: { "border-color": css("--accent"), "border-width": 5 } },
      { selector: ".flash", style: { "overlay-color": css("--accent"), "overlay-opacity": 0.35, "overlay-padding": 10 } },
      { selector: ".dim", style: { opacity: 0.25 } },
    ],
    layout: { name: "concentric", concentric: (n) => n.data("level"), levelWidth: () => 1, minNodeSpacing: 20, padding: 16, animate: false },
  });
  state.cy.on("tap", "node", (ev) => selectNode(ev.target.id()));
  state.cy.on("tap", (ev) => { if (ev.target === state.cy) clearSelection(); });
  state.cy.on("mouseover", "node", (ev) => {
    const n = ev.target;
    state.cy.elements().addClass("dim");
    n.closedNeighborhood().removeClass("dim");
    n.connectedEdges().addClass("show-label");
  });
  state.cy.on("mouseout", "node", () => { state.cy.elements().removeClass("dim"); state.cy.edges().removeClass("show-label"); });
}

function recolorGraph(data) {
  if (!state.cy) return buildGraph(data);
  for (const n of data.nodes) {
    const el = state.cy.getElementById(n.id);
    if (el.empty()) return buildGraph(data);
    el.data({ score: n.score, news_count: n.news_count, color: scoreColor(n.score) });
  }
  state.cy.style().update();
}

function flashPath(path) {
  if (!state.cy || !path) return;
  const els = state.cy.collection();
  path.forEach((id, i) => {
    els.merge(state.cy.getElementById(id));
    if (i > 0) els.merge(state.cy.getElementById(`${path[i - 1]}->${id}`));
  });
  els.addClass("flash");
  setTimeout(() => els.removeClass("flash"), 2500);
}

async function refreshGraph() {
  state.graph = await api(`/api/graph/${state.ticker}`);
  recolorGraph(state.graph);
  if (state.selected) renderNodePanel(state.selected);
}

/* ---------------- side panel ---------------- */

function renderSignal(sig) {
  const el = $("signal");
  const cls = signalClass(sig.score);
  el.className = `signal ${cls}`;
  el.querySelector(".sig-icon").textContent = cls === "green" ? "▲" : cls === "red" ? "▼" : "●";
  el.querySelector(".sig-label").textContent = cls.toUpperCase();
  el.querySelector(".sig-score").textContent = fmt(sig.score);
  state.signal = sig;
  if (!state.selected) renderDrivers();
}

function renderDrivers() {
  const sig = state.signal;
  $("side-title").textContent = "Why this colour";
  $("side-reset").hidden = true;
  if (!sig) return;
  const m = state.meta;
  const body = [`<p class="muted">Score ${fmt(sig.score)} = decayed sum of news impacts through the graph.
    Green ≥ ${m.green_threshold}, red ≤ ${m.red_threshold}. Top current drivers:</p>`];
  if (!sig.drivers.length) body.push(`<p class="empty">No scored news yet. Filings and articles appear here as they arrive.</p>`);
  for (const d of sig.drivers) {
    body.push(`<div class="driver">${chip(d.current)}<div>
      <div>${esc(d.title)}</div>
      <div class="path">${pathHtml(d.path)}</div>
      <div class="reason">${esc(d.reason)} <span class="muted">(at publish ${fmt(d.impact)})</span></div></div></div>`);
  }
  body.push(`<p class="muted">Click any node to see its news.</p>`);
  $("side-body").innerHTML = body.join("");
}

async function selectNode(id) {
  state.selected = id;
  await renderNodePanel(id);
}

async function renderNodePanel(id) {
  const n = state.graph.nodes.find((x) => x.id === id);
  if (!n) return clearSelection();
  $("side-title").textContent = n.id === state.ticker ? "Direct news" : "Node news";
  $("side-reset").hidden = false;
  const items = await api(`/api/news?entity=${encodeURIComponent(id)}&limit=30`);
  const cls = signalClass(n.score);
  const rows = items.map((it) => {
    const imp = it.impacts.filter((t) => t.ticker === state.ticker).reduce((a, t) => a + t.impact, 0);
    const own = it.entity_impacts.find((e) => e.entity === id);
    return `<div class="driver">${chip(imp)}<div>
      <a href="${esc(it.url) || "#"}" target="_blank" rel="noopener">${esc(it.title)}</a>
      <div class="meta"><span>${esc(it.source)}</span><span>${timeAgo(it.published_at)}</span>
      ${own ? `<span>for ${esc(n.name)}: ${fmt(own.sentiment)}</span>` : ""}</div></div></div>`;
  });
  $("side-body").innerHTML = `<div class="node-card"><div class="name">${esc(n.name)}</div>
    <div class="kv"><span>${esc(n.type)}${n.role ? " · " + esc(n.role) : ""}</span></div>
    <div class="kv"><span>News score <b class="${cls === "green" ? "up" : cls === "red" ? "down" : ""}">${fmt(n.score)}</b></span>
    <span>Exposure to ${esc(state.ticker)} ${n.exposure.toFixed(2)}</span><span>${n.news_count} items</span></div></div>
    <div class="muted" style="margin-bottom:4px">Chip = impact on ${esc(state.ticker)}</div>
    ${rows.join("") || `<p class="empty">No news for this node yet.</p>`}`;
}

function clearSelection() {
  state.selected = null;
  state.cy?.$(":selected").unselect();
  renderDrivers();
}

/* ---------------- charts ---------------- */

function chartOptions(height) {
  return {
    height, autoSize: true,
    layout: { background: { color: "transparent" }, textColor: css("--ink-2"), fontSize: 11 },
    grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
    rightPriceScale: { borderColor: css("--border"), minimumWidth: 72 },
    timeScale: { borderColor: css("--border"), timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    localization: { locale: "en-IN" },
  };
}

function buildCharts() {
  const pc = LightweightCharts.createChart($("price-chart"), chartOptions(320));
  const candles = pc.addCandlestickSeries({
    upColor: css("--good"), downColor: css("--bad"), borderVisible: false,
    wickUpColor: css("--good"), wickDownColor: css("--bad"),
  });
  const sc = LightweightCharts.createChart($("score-chart"), chartOptions(140));
  const score = sc.addBaselineSeries({
    baseValue: { type: "price", price: 0 },
    topLineColor: css("--good"), topFillColor1: css("--good") + "55", topFillColor2: css("--good") + "08",
    bottomLineColor: css("--bad"), bottomFillColor1: css("--bad") + "08", bottomFillColor2: css("--bad") + "55",
    lineWidth: 2, priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
  score.createPriceLine({ price: state.meta.green_threshold, color: css("--good"), lineStyle: 2, lineWidth: 1, axisLabelVisible: false });
  score.createPriceLine({ price: state.meta.red_threshold, color: css("--bad"), lineStyle: 2, lineWidth: 1, axisLabelVisible: false });

  let syncing = false;
  const sync = (from, to) => from.timeScale().subscribeVisibleTimeRangeChange((r) => {
    if (syncing || !r) return;
    syncing = true;
    try { to.timeScale().setVisibleRange(r); } catch (_) { /* other chart has no data yet */ }
    syncing = false;
  });
  sync(pc, sc); sync(sc, pc);

  pc.subscribeCrosshairMove((p) => showTip(p));
  state.charts = { pc, candles, sc, score };
}

const shift = (t) => t + IST_OFFSET;

function setBars(bars) {
  state.bars = bars;
  if (bars.length > 1) state.barStep = bars[bars.length - 1].time - bars[bars.length - 2].time || 60;
  state.charts.candles.setData(bars.map((b) => ({ ...b, time: shift(b.time) })));
  computePrevClose();
  if (bars.length) renderQuote(bars[bars.length - 1].close);
}

function computePrevClose() {
  const day = (t) => new Date((t + IST_OFFSET) * 1000).toISOString().slice(0, 10);
  const bars = state.bars;
  if (!bars.length) return;
  const today = day(bars[bars.length - 1].time);
  const prev = [...bars].reverse().find((b) => day(b.time) !== today);
  state.prevClose = prev ? prev.close : bars[0].open;
}

function renderQuote(price) {
  $("price").textContent = inr(price);
  if (state.prevClose) {
    const ch = price - state.prevClose, pct = (ch / state.prevClose) * 100;
    const el = $("change");
    el.textContent = `${ch >= 0 ? "▲" : "▼"} ${Math.abs(ch).toFixed(2)} (${fmt(pct)}%)`;
    el.className = `change ${ch >= 0 ? "up" : "down"}`;
  }
}

function barTimeFor(iso) {
  const t = Math.floor(new Date(iso).getTime() / 1000);
  const bars = state.bars;
  if (!bars.length || t < bars[0].time) return null;
  let lo = 0, hi = bars.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (bars[mid].time <= t) lo = mid; else hi = mid - 1; }
  return bars[lo].time;
}

function tickerImpact(item) {
  return item.impacts.filter((t) => t.ticker === state.ticker).reduce((a, t) => a + t.impact, 0);
}

function renderMarkers() {
  const markers = [];
  for (const it of state.news) {
    const imp = tickerImpact(it);
    if (Math.abs(imp) < 0.03) continue;
    const t = barTimeFor(it.published_at);
    if (t == null) continue;
    markers.push({
      time: shift(t), position: imp > 0 ? "belowBar" : "aboveBar", shape: imp > 0 ? "arrowUp" : "arrowDown",
      color: imp > 0 ? css("--good") : css("--bad"), size: Math.abs(imp) >= 0.2 ? 2 : 1,
    });
  }
  markers.sort((a, b) => a.time - b.time);
  state.charts.candles.setMarkers(markers);
}

function showTip(p) {
  const tip = $("chart-tip");
  if (!p || !p.time || !p.point) { tip.hidden = true; return; }
  const t = p.time - IST_OFFSET;
  const hits = state.news.filter((it) => barTimeFor(it.published_at) === t && Math.abs(tickerImpact(it)) >= 0.03);
  if (!hits.length) { tip.hidden = true; return; }
  tip.innerHTML = hits.slice(0, 4).map((it) => `<div style="margin-bottom:6px">${chip(tickerImpact(it))} ${esc(it.title)}
    <div class="path">${pathHtml(it.impacts.find((x) => x.ticker === state.ticker)?.path)}</div></div>`).join("");
  const box = $("price-chart").getBoundingClientRect(), panel = $("price-chart").parentElement.getBoundingClientRect();
  tip.style.left = Math.min(p.point.x + (box.left - panel.left) + 16, panel.width - 360) + "px";
  tip.style.top = p.point.y + (box.top - panel.top) + 12 + "px";
  tip.hidden = false;
}

async function refreshScoreHistory() {
  const sig = await api(`/api/signal/${state.ticker}?hours=120`);
  state.charts.score.setData(sig.history.map((h) => ({ time: shift(h.time), value: h.value })));
  renderSignal(sig);
}

/* ---------------- feed ---------------- */

function feedItem(it, flash) {
  const imp = tickerImpact(it);
  const main = it.impacts.find((t) => t.ticker === state.ticker);
  const li = document.createElement("li");
  li.className = "item" + (flash ? " flash" : "");
  li.innerHTML = `${chip(imp)}<div>
    <a href="${esc(it.url) || "#"}" target="_blank" rel="noopener">${esc(it.title)}</a>
    <div class="meta"><span class="tag ${it.kind}">${it.kind}</span><span>${esc(it.source)}</span>
      <span title="${esc(it.published_at)}">${istTime(it.published_at)} · ${timeAgo(it.published_at)}</span>
      ${it.event_type && it.event_type !== "other" ? `<span>${esc(it.event_type.replaceAll("_", " "))}</span>` : ""}
      ${main ? `<span>${esc(main.horizon)}</span>` : ""}</div>
    ${main ? `<div class="path">${pathHtml(main.path)}</div><div class="reason">${esc(main.reason)}</div>` : ""}
  </div>`;
  li.addEventListener("mouseenter", () => main && flashPath(main.path));
  return li;
}

function renderFeed() {
  const ol = $("feed");
  ol.innerHTML = "";
  if (!state.news.length) { ol.innerHTML = `<li class="empty">Waiting for news… sources poll every 20s–3min.</li>`; return; }
  for (const it of state.news.slice(0, 150)) ol.appendChild(feedItem(it, false));
}

/* ---------------- status ---------------- */

async function refreshStatus() {
  const [cal, health] = await Promise.all([api("/api/calendar"), api("/api/health")]);
  $("calendar").innerHTML = cal.length
    ? cal.map((c) => `<li><b>${esc(c.date)}</b><span>${esc(nodeName(c.entity))}: ${esc(c.title)}</span></li>`).join("")
    : `<li class="empty">No upcoming board meetings found yet.</li>`;
  $("queue").textContent = `scoring queue: ${health.queue}`;
  const rows = Object.entries(health.sources).sort(([a], [b]) => a.localeCompare(b));
  // Collapse per-entity Google News rows into one line.
  const gn = rows.filter(([k]) => k.startsWith("google_news:"));
  const other = rows.filter(([k]) => !k.startsWith("google_news:"));
  const lines = other.map(([k, v]) => `<li><span class="dot ${v.ok ? "on" : "off"}"></span><span>${esc(k)}</span>
    <span class="${v.ok ? "muted" : "err"}">${v.ok ? timeAgo(v.at) : esc(v.error)}</span></li>`);
  if (gn.length) {
    const bad = gn.filter(([, v]) => !v.ok).length;
    lines.push(`<li><span class="dot ${bad ? "off" : "on"}"></span><span>google_news (${gn.length} queries)</span>
      <span class="${bad ? "err" : "muted"}">${bad ? bad + " failing" : "ok"}</span></li>`);
  }
  $("health").innerHTML = lines.join("") || `<li class="empty">Sources start within a few seconds.</li>`;
}

/* ---------------- live push ---------------- */

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  const dot = $("badge-ws").querySelector(".dot");
  let ping;
  ws.onopen = () => { dot.className = "dot on"; ping = setInterval(() => ws.send("ping"), 25000); };
  ws.onclose = () => { dot.className = "dot off"; clearInterval(ping); setTimeout(connect, 3000); };
  ws.onmessage = (msg) => {
    const { event, data } = JSON.parse(msg.data);
    if (event === "bar" && data.ticker === state.ticker) onBar(data);
    else if (event === "news") onNews(data);
    else if (event === "signal" && data.ticker === state.ticker) {
      renderSignal(data);
      state.charts.score.update({ time: shift(Math.floor(Date.parse(data.at) / 1000)), value: data.score });
    }
  };
}

function onBar(b) {
  const last = state.bars[state.bars.length - 1];
  if (last && b.time < last.time) return;
  if (last && b.time === last.time) state.bars[state.bars.length - 1] = b; else state.bars.push(b);
  state.charts.candles.update({ ...b, time: shift(b.time) });
  renderQuote(b.close);
}

function onNews(it) {
  if (!it.impacts.some((t) => t.ticker === state.ticker)) return;
  state.news.unshift(it);
  const ol = $("feed");
  if (ol.firstElementChild?.classList.contains("empty")) ol.innerHTML = "";
  ol.prepend(feedItem(it, true));
  renderMarkers();
  const main = it.impacts.find((t) => t.ticker === state.ticker);
  refreshGraph().then(() => main && flashPath(main.path));
}

/* ---------------- boot ---------------- */

async function loadTicker(ticker) {
  state.ticker = ticker;
  state.selected = null;
  const [graph, bars, news] = await Promise.all([
    api(`/api/graph/${ticker}`), api(`/api/bars/${ticker}?days=5`), api(`/api/news?ticker=${ticker}&limit=300`),
  ]);
  state.graph = graph;
  state.news = news;
  buildGraph(graph);
  setBars(bars);
  renderFeed();
  renderMarkers();
  await refreshScoreHistory();
  state.charts.pc.timeScale().fitContent();
}

async function boot() {
  state.meta = await api("/api/meta");
  const sel = $("ticker");
  sel.innerHTML = state.meta.tickers.map((t) => `<option>${esc(t)}</option>`).join("");
  sel.onchange = () => loadTicker(sel.value);
  const pb = $("badge-prices");
  pb.textContent = state.meta.price_live ? "Prices: LIVE · Angel One" : "Prices: DEMO (no Angel One login)";
  if (!state.meta.price_live) pb.classList.add("warn");
  const sb = $("badge-scorer");
  sb.textContent = { claude: "Scorer: Claude", lexicon: "Scorer: keywords (no API key)", demo: "Scorer: DEMO samples" }[state.meta.scorer] || `Scorer: ${state.meta.scorer}`;
  if (state.meta.scorer !== "claude") sb.classList.add("warn");
  $("side-reset").onclick = clearSelection;
  buildCharts();
  await loadTicker(state.meta.tickers[0]);
  connect();
  refreshStatus();
  setInterval(refreshStatus, 15000);
  setInterval(refreshScoreHistory, 5 * 60 * 1000); // decay moves the score even without news
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => location.reload());
}

boot().catch((err) => {
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="empty">Failed to load: ${esc(err.message)}</p>`);
});
