/* SignalGraph dashboard.
   Views: overview | graph | news | events (bottom nav on phones, one grid on desktop).
   Entity detail opens as a sheet (phones) / drawer (desktop) with Overview, News, Links tabs.
   Routes live in the URL hash so the phone's back button closes sheets: #/graph, #/e/jlr/news */
"use strict";

const IST_OFFSET = 19800; // charts take UTC seconds; shift so the axis reads in IST
const $ = (id) => document.getElementById(id);
const SNAP = window.SG_SNAPSHOT || null; // static demo build: data baked into the page
if (SNAP) shiftSnapshot(SNAP);

// Move every timestamp in a baked snapshot forward so the demo always reads as "now".
function shiftSnapshot(s) {
  const d = Math.floor(Date.now() / 1000) - s.taken_at;
  const iso = (v) => new Date(Date.parse(v) + d * 1000).toISOString();
  const news = (list) => list.forEach((n) => { n.published_at = iso(n.published_at); });
  s.bars.forEach((b) => { b.time += d; });
  s.signal.history.forEach((h) => { h.time += d; });
  s.signal.at = iso(s.signal.at);
  news(s.news); news(s.replay || []);
  Object.values(s.entities).forEach((e) => { news(e.news); e.history.forEach((h) => { h.time += d; }); });
  Object.values(s.health.sources).forEach((v) => { if (v.at) v.at = iso(v.at); });
}
const state = {
  ticker: null, meta: null, graph: null, news: [], bars: [], signal: null, history: [],
  cy: null, charts: {}, prevClose: null, view: "overview", entity: null, entityTab: "overview",
  entityData: null, graphMode: localStorage_get("sg.graphMode") || "map", typeFocus: null,
  newsGroup: "all", tone: "all", unseen: 0, rangeDays: 5,
};

function localStorage_get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
function localStorage_set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } }
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 2) => (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(d);
const inr = (x) => "₹" + x.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const isDesktop = () => matchMedia("(min-width: 1100px)").matches;

function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
const istTime = (iso) => new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

/* ---------------- data access (live API or baked snapshot) ---------------- */

async function api(path) {
  if (SNAP) return snapApi(path);
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function snapApi(path) {
  const u = new URL(path, "http://x");
  const p = u.pathname;
  if (p === "/api/meta") return SNAP.meta;
  if (p.startsWith("/api/graph/")) return SNAP.graph;
  if (p.startsWith("/api/bars/")) return SNAP.bars;
  if (p.startsWith("/api/signal/")) return SNAP.signal;
  if (p === "/api/news") return SNAP.news;
  if (p === "/api/calendar") return SNAP.calendar;
  if (p === "/api/health") return SNAP.health;
  if (p.startsWith("/api/entity/")) return SNAP.entities[decodeURIComponent(p.split("/").pop())];
  throw new Error(`no snapshot for ${path}`);
}

/* ---------------- colour + small renderers ---------------- */

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
const ICON = { green: "▲", red: "▼", neutral: "●" };
function chip(impact, title = "Impact on the stock: direction × magnitude × confidence") {
  const cls = Math.abs(impact) < 0.005 ? "zero" : impact > 0 ? "pos" : "neg";
  const icon = cls === "zero" ? "●" : impact > 0 ? "▲" : "▼";
  return `<span class="chip-impact ${cls}" title="${esc(title)}">${icon} ${fmt(impact)}</span>`;
}

const node = (id) => state.graph?.nodes.find((x) => x.id === id);
const nodeName = (id) => node(id)?.name || id;
function relation(src, dst) {
  const e = state.graph?.edges.find((x) => x.source === src && x.target === dst);
  return e ? e.relation : "";
}
function entChip(id) {
  const n = node(id);
  return `<button class="ent" data-entity="${esc(id)}"><i class="glyph" style="background:${n ? scoreColor(n.score) : css("--neutral")}"></i>${esc(nodeName(id))}</button>`;
}
function pathHtml(path) {
  if (!path || !path.length) return "";
  return `<div class="path">${path.map((id, i) => (i === 0 ? entChip(id)
    : `<span class="arrow">${esc(relation(path[i - 1], id))} →</span>${entChip(id)}`)).join("")}</div>`;
}
const tickerImpact = (it) => it.impacts.filter((t) => t.ticker === state.ticker).reduce((a, t) => a + t.impact, 0);
const mainImpact = (it) => it.impacts.find((t) => t.ticker === state.ticker);

const GROUPS = [
  ["all", "All"], ["direct", "Direct"], ["filings", "Filings"], ["clients", "Clients"], ["people", "People"],
  ["projects", "Projects"], ["partners", "Partners"], ["peers", "Peers"], ["group", "Tata group"], ["macro", "Macro"],
];
const TYPE_GROUP = { company: "direct", person: "people", project: "projects", client: "clients", partner: "partners",
  competitor: "peers", parent: "group", theme: "macro" };
const TYPE_LABEL = { company: "Company", person: "People", parent: "Parent", project: "Projects", client: "Clients",
  partner: "Partners", competitor: "Competitors", theme: "Macro themes" };
function groupsOf(it) {
  const g = new Set();
  const m = mainImpact(it);
  const src = m ? node(m.path[0]) : null;
  if (src) g.add(TYPE_GROUP[src.type] || "macro");
  if (it.kind === "filing") g.add("filings");
  return g;
}

function newsRow(it, flash = false, entityId = null) {
  const imp = tickerImpact(it);
  const m = mainImpact(it);
  const own = entityId ? it.entity_impacts.find((e) => e.entity === entityId) : null;
  const li = document.createElement("li");
  li.className = "row" + (flash ? " flash" : "");
  li.innerHTML = `${chip(imp)}<div>
    ${it.url ? `<a class="row-title" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>` : `<span class="row-title">${esc(it.title)}</span>`}
    <div class="meta"><span class="tag ${it.kind}">${it.kind}</span><span>${esc(it.source)}</span>
      <span title="${esc(istTime(it.published_at))} IST">${timeAgo(it.published_at)}</span>
      ${it.event_type && it.event_type !== "other" ? `<span>· ${esc(it.event_type.replaceAll("_", " "))}</span>` : ""}
      ${m ? `<span>· ${esc(m.horizon)}</span>` : ""}
      ${own && entityId !== state.ticker ? `<span>· for ${esc(nodeName(entityId))}: <b class="${own.sentiment >= 0 ? "up" : "down"}">${fmt(own.sentiment)}</b></span>` : ""}</div>
    ${m ? `${pathHtml(m.path)}<div class="reason">${esc(m.reason)}</div>` : ""}
  </div>`;
  li.addEventListener("mouseenter", () => m && flashPath(m.path));
  return li;
}

/* ---------------- routing ---------------- */

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "e" && parts[1]) return { view: state.view, entity: decodeURIComponent(parts[1]), tab: parts[2] || "overview" };
  return { view: ["overview", "graph", "news", "events"].includes(parts[0]) ? parts[0] : "overview", entity: null };
}
function route() {
  const r = parseHash();
  setView(r.view);
  if (r.entity) showEntity(r.entity, r.tab); else hideEntity();
}
function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }

function setView(view) {
  state.view = view;
  document.body.dataset.view = view;
  document.querySelectorAll(".bottom-nav button").forEach((b) => {
    if (b.dataset.go === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  if (view === "news") { state.unseen = 0; renderBadge(); }
  if (view === "graph" || isDesktop()) requestAnimationFrame(ensureGraph);
}

/* ---------------- header + overview ---------------- */

function renderSignal(sig) {
  state.signal = sig;
  const cls = signalClass(sig.score);
  const pill = $("signal");
  pill.className = `signal ${cls}`;
  pill.querySelector(".sig-icon").textContent = ICON[cls];
  pill.querySelector(".sig-label").textContent = cls.toUpperCase();
  pill.querySelector(".sig-score").textContent = fmt(sig.score);
  const hero = document.querySelector(".hero-value");
  hero.className = `hero-value ${cls}`;
  $("hero-icon").textContent = ICON[cls];
  $("hero-label").textContent = cls.toUpperCase();
  $("hero-score").textContent = fmt(sig.score);
  const pos = (v) => `${((Math.max(-1, Math.min(1, v)) + 1) / 2) * 100}%`;
  $("gauge-marker").style.left = pos(sig.score);
  $("tick-red").style.left = pos(state.meta.red_threshold);
  $("tick-green").style.left = pos(state.meta.green_threshold);
  $("gauge").setAttribute("aria-label", `Signal ${cls} at ${fmt(sig.score)} on a scale from −1 to +1`);
  const dayAgo = Date.now() / 1000 - 86400;
  const prev = [...state.history].reverse().find((h) => h.time <= dayAgo);
  $("hero-prev").innerHTML = prev ? `${fmt(prev.value)} <span class="${sig.score >= prev.value ? "up" : "down"}">${sig.score >= prev.value ? "▲" : "▼"}${Math.abs(sig.score - prev.value).toFixed(2)}</span>` : "—";
  const recent = state.news.filter((n) => Date.now() - Date.parse(n.published_at) < 86400e3);
  const g = recent.filter((n) => tickerImpact(n) > 0.005).length, r = recent.filter((n) => tickerImpact(n) < -0.005).length;
  $("hero-count").innerHTML = `<span class="up">▲${g}</span> <span class="down">▼${r}</span>`;
  renderDrivers();
}

function renderDrivers() {
  const sig = state.signal;
  const ol = $("drivers");
  if (!sig.drivers.length) { ol.innerHTML = `<li class="empty">No scored news yet. Filings and articles appear here as they arrive.</li>`; return; }
  ol.innerHTML = sig.drivers.map((d) => `<li class="row">${chip(d.current, "Current pull on the score (after time decay)")}<div>
    <span class="row-title">${esc(d.title)}</span>
    ${pathHtml(d.path)}
    <div class="reason">${esc(d.reason)} <span class="muted">· ${fmt(d.impact)} when published</span></div></div></li>`).join("");
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

/* ---------------- charts ---------------- */

function chartOptions() {
  return {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: css("--ink-2"), fontSize: 11 },
    grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
    rightPriceScale: { borderColor: css("--border"), minimumWidth: 64 },
    timeScale: { borderColor: css("--border"), timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    localization: { locale: "en-IN" },
    handleScroll: { vertTouchDrag: false },
  };
}

function buildCharts() {
  const pc = LightweightCharts.createChart($("price-chart"), chartOptions());
  const candles = pc.addCandlestickSeries({
    upColor: css("--good"), downColor: css("--bad"), borderVisible: false, wickUpColor: css("--good"), wickDownColor: css("--bad"),
  });
  const sc = LightweightCharts.createChart($("score-chart"), chartOptions());
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
  pc.subscribeCrosshairMove(showTip);
  state.charts = { pc, candles, sc, score };
  document.querySelectorAll("#range button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#range button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    state.rangeDays = +b.dataset.days;
    applyRange();
  }));
}

function applyRange() {
  const last = state.bars[state.bars.length - 1];
  if (!last) return;
  const to = last.time + IST_OFFSET;
  state.charts.pc.timeScale().setVisibleRange({ from: to - state.rangeDays * 86400, to });
}

const shift = (t) => t + IST_OFFSET;

function setBars(bars) {
  state.bars = bars;
  state.charts.candles.setData(bars.map((b) => ({ ...b, time: shift(b.time) })));
  const day = (t) => new Date((t + IST_OFFSET) * 1000).toISOString().slice(0, 10);
  if (bars.length) {
    const today = day(bars[bars.length - 1].time);
    const prev = [...bars].reverse().find((b) => day(b.time) !== today);
    state.prevClose = prev ? prev.close : bars[0].open;
    renderQuote(bars[bars.length - 1].close);
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

function renderMarkers() {
  const markers = [];
  for (const it of state.news) {
    const imp = tickerImpact(it);
    if (Math.abs(imp) < 0.03) continue;
    const t = barTimeFor(it.published_at);
    if (t == null) continue;
    markers.push({ time: shift(t), position: imp > 0 ? "belowBar" : "aboveBar", shape: imp > 0 ? "arrowUp" : "arrowDown",
      color: imp > 0 ? css("--good") : css("--bad"), size: Math.abs(imp) >= 0.2 ? 2 : 1 });
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
  tip.innerHTML = hits.slice(0, 3).map((it) => `<div class="tip-row">${chip(tickerImpact(it))}<span>${esc(it.title)}</span></div>`).join("");
  const w = $("price-chart").clientWidth;
  tip.style.left = Math.max(4, Math.min(p.point.x + 14, w - 330)) + "px";
  tip.style.top = Math.min(p.point.y + 12, 180) + "px";
  tip.hidden = false;
}

/* ---------------- graph ---------------- */

const LEVEL = { company: 4, person: 3, parent: 3, project: 3, client: 2, partner: 2, competitor: 1, theme: 1 };
const SHAPE = { company: "round-rectangle", person: "ellipse", parent: "round-rectangle", project: "diamond",
  client: "ellipse", partner: "ellipse", competitor: "round-triangle", theme: "hexagon" };
const SHAPE_CLASS = { company: "sq", parent: "sq", project: "dia", competitor: "tri", theme: "hex" };

function ensureGraph() {
  // build only once the box is on screen: layout and the portrait stretch need real dimensions
  if (state.graphMode === "list" || !$("cy").clientWidth) return;
  if (!state.cy) buildGraph();
  else { state.cy.resize(); state.cy.fit(undefined, 24); }
}

function buildGraph() {
  const data = state.graph;
  const els = [
    ...data.nodes.map((n) => ({ data: { ...n, label: n.id === state.ticker ? n.ticker || n.id : n.name, color: scoreColor(n.score), level: n.id === state.ticker ? 5 : LEVEL[n.type] || 1 } })),
    ...data.edges.map((e) => ({ data: { id: `${e.source}->${e.target}`, ...e } })),
  ];
  if (state.cy) state.cy.destroy();
  const phone = !isDesktop() && $("cy").clientWidth < 600;
  const fs = phone ? 17 : 13;
  const edgeColor = (e) => { const s = e.source().data("score"); return Math.abs(s) > 0.1 ? scoreColor(s) : css("--border"); };
  state.cy = cytoscape({
    container: $("cy"), elements: els, minZoom: 0.3, maxZoom: 2.5,
    style: [
      { selector: "node", style: {
        "background-color": "data(color)", label: "data(label)", color: css("--ink"), "font-size": fs,
        "text-valign": "bottom", "text-margin-y": 5, "text-wrap": "wrap", "text-max-width": 120,
        shape: (n) => SHAPE[n.data("type")] || "ellipse",
        width: "mapData(exposure, 0, 1, 30, 60)", height: "mapData(exposure, 0, 1, 30, 60)",
        "border-color": css("--ink-3"), "border-width": "mapData(news_count, 0, 8, 1, 6)",
        "text-outline-color": css("--sunken"), "text-outline-width": 2,
      } },
      { selector: `node[id = "${state.ticker}"]`, style: { width: 92, height: 58, "font-size": fs + 4, "font-weight": 700, "border-width": 3, "border-color": css("--ink") } },
      { selector: "edge", style: {
        width: "mapData(weight, 0, 1, 1, 5)", "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.8,
        "line-color": edgeColor, "target-arrow-color": edgeColor,
        "line-style": (e) => (e.data("sign") < 0 ? "dashed" : "solid"), opacity: 0.85,
      } },
      { selector: "edge.show-label", style: { label: "data(relation)", "font-size": 11, color: css("--ink-2"),
        "text-background-color": css("--sunken"), "text-background-opacity": 1, "text-background-padding": 2 } },
      { selector: ".flash", style: { "overlay-color": css("--accent"), "overlay-opacity": 0.35, "overlay-padding": 10 } },
      { selector: ".dim", style: { opacity: 0.18 } },
    ],
    layout: { name: "concentric", concentric: (n) => n.data("level"), levelWidth: () => 1, minNodeSpacing: 18, padding: 20, animate: false },
  });
  const box = $("cy");
  const aspect = box.clientHeight / Math.max(box.clientWidth, 1);
  if (aspect > 1.15) { // portrait: stretch the rings into tall ellipses so labels stay readable
    const c = state.cy.getElementById(state.ticker).position();
    const sx = 0.62, sy = Math.min(aspect * 0.85, 1.5);
    state.cy.nodes().positions((n) => ({ x: c.x + (n.position("x") - c.x) * sx, y: c.y + (n.position("y") - c.y) * sy }));
    state.cy.fit(undefined, 12);
  }
  state.cy.on("tap", "node", (ev) => openEntity(ev.target.id()));
  state.cy.on("mouseover", "node", (ev) => {
    const n = ev.target;
    state.cy.elements().addClass("dim");
    n.closedNeighborhood().removeClass("dim");
    n.connectedEdges().addClass("show-label");
  });
  state.cy.on("mouseout", "node", () => { state.cy.edges().removeClass("show-label"); applyTypeFocus(); });
  applyTypeFocus();
}

function recolorGraph() {
  if (!state.cy) return;
  for (const n of state.graph.nodes) {
    const el = state.cy.getElementById(n.id);
    if (!el.empty()) el.data({ score: n.score, news_count: n.news_count, color: scoreColor(n.score) });
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

function renderTypeChips() {
  const types = [...new Set(state.graph.nodes.map((n) => n.type))].filter((t) => t !== "company");
  const order = ["client", "person", "project", "partner", "parent", "competitor", "theme"];
  types.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  $("type-chips").innerHTML = [`<button class="chip" data-type="" aria-pressed="${!state.typeFocus}">All</button>`,
    ...types.map((t) => `<button class="chip" data-type="${t}" aria-pressed="${state.typeFocus === t}">${TYPE_LABEL[t]}
      <span class="n">${state.graph.nodes.filter((n) => n.type === t).length}</span></button>`)].join("");
}

function applyTypeFocus() {
  if (state.cy) {
    state.cy.elements().removeClass("dim");
    if (state.typeFocus) {
      const keep = state.cy.nodes().filter((n) => n.data("type") === state.typeFocus || n.id() === state.ticker);
      state.cy.elements().not(keep).not(keep.edgesWith(keep)).addClass("dim");
    }
  }
  renderEntityList();
}

function renderEntityList() {
  const box = $("entity-list");
  const order = ["company", "client", "person", "project", "partner", "parent", "competitor", "theme"];
  const groups = order.filter((t) => !state.typeFocus || t === state.typeFocus || t === "company")
    .map((t) => [t, state.graph.nodes.filter((n) => n.type === t).sort((a, b) => Math.abs(b.score) - Math.abs(a.score))])
    .filter(([, ns]) => ns.length);
  box.innerHTML = groups.map(([t, ns]) => `<div class="group"><h3>${TYPE_LABEL[t]}</h3>${ns.map((n) => `
    <button class="ent-row" data-entity="${esc(n.id)}">
      <span class="shape ${SHAPE_CLASS[n.type] || ""}" style="background:${scoreColor(n.score)}"></span>
      <span><span class="name">${esc(n.name)}</span><span class="sub">${esc(n.role || n.type)} · ${n.news_count} news</span></span>
      ${chip(n.score, "News sentiment for this entity")}<span class="chev" aria-hidden="true">›</span>
    </button>`).join("")}</div>`).join("");
}

function setGraphMode(mode) {
  state.graphMode = mode;
  localStorage_set("sg.graphMode", mode);
  document.querySelectorAll("#graph-mode button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
  document.querySelector(".graph-wrap").hidden = mode === "list";
  $("entity-list").hidden = mode !== "list";
  if (mode === "map") requestAnimationFrame(ensureGraph);
}

async function refreshGraph() {
  state.graph = await api(`/api/graph/${state.ticker}`);
  recolorGraph();
  renderEntityList();
}

/* ---------------- news view ---------------- */

function renderNewsChips() {
  const counts = Object.fromEntries(GROUPS.map(([k]) => [k, 0]));
  for (const it of state.news) { counts.all++; for (const g of groupsOf(it)) counts[g] = (counts[g] || 0) + 1; }
  $("news-chips").innerHTML = GROUPS.filter(([k]) => k === "all" || counts[k])
    .map(([k, label]) => `<button class="chip" data-group="${k}" aria-pressed="${state.newsGroup === k}">${label} <span class="n">${counts[k]}</span></button>`).join("");
}

function renderFeed() {
  renderNewsChips();
  const ol = $("feed");
  ol.innerHTML = "";
  const items = state.news.filter((it) => {
    if (state.newsGroup !== "all" && !groupsOf(it).has(state.newsGroup)) return false;
    const imp = tickerImpact(it);
    if (state.tone === "good" && imp <= 0.005) return false;
    if (state.tone === "bad" && imp >= -0.005) return false;
    return true;
  });
  if (!items.length) { ol.innerHTML = `<li class="empty">${state.news.length ? "Nothing matches these filters." : "Waiting for news… sources poll every 20s–3min."}</li>`; return; }
  for (const it of items.slice(0, 150)) ol.appendChild(newsRow(it));
}

function renderBadge() {
  const b = $("news-badge");
  b.hidden = !state.unseen;
  b.textContent = state.unseen > 9 ? "9+" : String(state.unseen);
}

let toastTimer;
function toast(it) {
  const el = $("toast");
  el.innerHTML = `${chip(tickerImpact(it))}<span class="t">${esc(it.title)}</span>`;
  el.hidden = false;
  el.onclick = () => { el.hidden = true; go("#/news"); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 6000);
}

/* ---------------- events view ---------------- */

async function refreshStatus() {
  const [cal, health] = await Promise.all([api("/api/calendar"), api("/api/health")]);
  $("calendar").innerHTML = cal.length
    ? cal.map((c) => `<li class="ev-row"><span class="ev-date">${esc(new Date(c.date).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }))}</span>
        <span>${entChip(c.entity)} ${esc(c.title)}</span></li>`).join("")
    : `<li class="empty">No upcoming board meetings found yet. They come from NSE every hour.</li>`;
  $("queue").textContent = `scoring queue: ${health.queue}`;
  const rows = Object.entries(health.sources).sort(([a], [b]) => a.localeCompare(b));
  const gn = rows.filter(([k]) => k.startsWith("google_news:"));
  const lines = rows.filter(([k]) => !k.startsWith("google_news:")).map(([k, v]) => `<li class="src-row"><span class="dot ${v.ok ? "on" : "off"}"></span>
    <span>${esc(k)}</span><span class="${v.ok ? "ok" : "err"}">${v.ok ? timeAgo(v.at) : esc(v.error)}</span></li>`);
  if (gn.length) {
    const bad = gn.filter(([, v]) => !v.ok).length;
    lines.push(`<li class="src-row"><span class="dot ${bad ? "off" : "on"}"></span><span>google_news (${gn.length} queries)</span>
      <span class="${bad ? "err" : "ok"}">${bad ? bad + " failing" : "ok"}</span></li>`);
  }
  $("health").innerHTML = lines.join("") || `<li class="empty">Sources start within a few seconds.</li>`;
  const m = state.meta;
  $("about").innerHTML = [
    ["Prices", m.price_live ? "LIVE · Angel One" : "DEMO · synthetic (no Angel One login)"],
    ["Scoring", { claude: "Claude (reads each item against the graph)", lexicon: "Keywords only (no API key)", demo: "DEMO · preset sample scores" }[m.scorer] || m.scorer],
    ["Thresholds", `green ≥ ${m.green_threshold}, red ≤ ${m.red_threshold}`],
  ].map(([k, v]) => `<li class="src-row"><span class="muted" style="min-width:84px">${k}</span><span>${esc(v)}</span></li>`).join("")
    + (SNAP ? `<li class="src-row"><span class="muted" style="min-width:84px">Build</span><span>Static demo snapshot with invented [Sample] headlines</span></li>` : "");
}

/* ---------------- entity sheet ---------------- */

function openEntity(id, tab = "overview") { go(`#/e/${encodeURIComponent(id)}/${tab}`); }
function closeEntity() { go(`#/${state.view}`); }

async function showEntity(id, tab) {
  const sheet = $("sheet");
  const first = sheet.hidden || state.entity !== id;
  state.entity = id;
  state.entityTab = ["overview", "news", "links"].includes(tab) ? tab : "overview";
  sheet.hidden = false;
  document.body.style.overflow = "hidden";
  document.querySelectorAll("#sheet-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === state.entityTab)));
  if (first) {
    const n = node(id);
    $("sheet-title").textContent = n ? n.name : id;
    $("sheet-type").textContent = n ? (TYPE_LABEL[n.type] || n.type).replace(/s$/, "") + (n.role ? ` · ${n.role}` : "") : "";
    $("sheet-body").innerHTML = `<p class="empty">Loading…</p>`;
    state.entityData = null;
    try { state.entityData = await api(`/api/entity/${encodeURIComponent(id)}?ticker=${state.ticker}`); } catch (err) {
      $("sheet-body").innerHTML = `<p class="empty">Could not load: ${esc(err.message)}</p>`; return;
    }
    if (state.entity !== id) return;
  }
  renderEntity();
  if (state.cy) { state.cy.$(":selected").unselect(); state.cy.getElementById(id).select(); }
}

function hideEntity() {
  $("sheet").hidden = true;
  document.body.style.overflow = "";
  state.entity = null;
}

function renderEntity() {
  const d = state.entityData;
  if (!d) return;
  const e = d.entity;
  const cls = signalClass(e.score);
  const pill = $("sheet-score");
  pill.className = `pill ${cls}`;
  pill.textContent = `${ICON[cls]} ${fmt(e.score)}`;
  pill.title = e.id === state.ticker ? "Stock signal" : "News sentiment for this entity (decayed)";
  $("sheet-news-count").textContent = d.news.length;
  $("sheet-links-count").textContent = d.links.length;
  const body = $("sheet-body");
  body.scrollTop = 0;
  if (state.entityTab === "news") {
    body.innerHTML = `<p class="muted" style="margin:0 0 4px">Chip = impact on ${esc(state.ticker)}${e.id !== state.ticker ? `; “for ${esc(e.name)}” = effect on ${esc(e.name)} itself` : ""}.</p><ol class="list" id="ent-news"></ol>`;
    const ol = $("ent-news");
    if (!d.news.length) ol.innerHTML = `<li class="empty">No news for ${esc(e.name)} yet. New items land here as soon as they're scored.</li>`;
    for (const it of d.news) ol.appendChild(newsRow(it, false, e.id));
    return;
  }
  if (state.entityTab === "links") {
    body.innerHTML = `<p class="muted" style="margin:0 0 4px">Who ${esc(e.name)} is linked to, and which way news travels. Higher exposure means a bigger effect.</p>` +
      (d.links.map((l) => `<button class="link-row" data-entity="${esc(l.id)}">
        <span class="shape" style="width:14px;height:14px;border-radius:50%;background:${scoreColor(l.score)}"></span>
        <span><b>${esc(l.name)}</b><span class="rel">${esc(l.relation)} · ${l.direction === "out" ? `news flows to ${esc(l.name)}` : `news flows in from ${esc(l.name)}`}${l.sign < 0 ? " (inverse)" : ""} · exposure ${l.weight.toFixed(2)}</span></span>
        ${chip(l.score, "News sentiment for this entity")}<span class="chev" aria-hidden="true">›</span></button>`).join("")
        || `<p class="empty">No links.</p>`);
    return;
  }
  // overview
  const isTicker = e.id === state.ticker;
  const best = d.effect.paths.slice().sort((a, b) => b.weight - a.weight)[0];
  const facts = Object.entries(e.facts || {});
  body.innerHTML = `
    ${e.description ? `<p class="desc">${esc(e.description)}</p>` : ""}
    <div class="effect">
      <div class="eyebrow">${isTicker ? "Direct news" : `Effect on ${esc(state.ticker)}`}</div>
      ${!isTicker && best ? pathHtml(best.nodes) : ""}
      <div class="effect-grid">
        <div class="stat"><span class="k">Pull now</span><span class="v ${d.effect.contribution > 0.005 ? "up" : d.effect.contribution < -0.005 ? "down" : ""}">${fmt(d.effect.contribution)}</span></div>
        <div class="stat"><span class="k">Exposure</span><span class="v">${isTicker ? "1.00" : best ? best.weight.toFixed(2) : "—"}</span>
          <span class="meter"><i style="width:${(isTicker ? 1 : best ? best.weight : 0) * 100}%"></i></span></div>
        <div class="stat"><span class="k">7 days</span><span class="v"><span class="up">▲${d.effect.green_7d}</span> <span class="down">▼${d.effect.red_7d}</span></span></div>
      </div>
    </div>
    <div class="spark-wrap">
      <div class="card-head" style="margin-bottom:4px"><h2>${isTicker ? "Own news sentiment" : "News sentiment"} · 7 days</h2><span class="muted">${fmt(e.score)} now</span></div>
      ${sparkline(d.history)}
    </div>
    ${facts.length ? `<h2 style="margin-bottom:4px">Key facts</h2><dl class="facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
    ${d.news.length ? `<h3 class="section-title">Latest</h3><ol class="list" id="ent-latest"></ol>
      <button class="chip" style="margin-top:10px" data-tab-go="news">All ${d.news.length} news items ›</button>` : `<p class="empty">No news yet for ${esc(e.name)}.</p>`}`;
  const ol = $("ent-latest");
  if (ol) for (const it of d.news.slice(0, 3)) ol.appendChild(newsRow(it, false, e.id));
}

function sparkline(history) {
  const W = 320, H = 70, pad = 4;
  if (!history.length) return "";
  const t0 = history[0].time, t1 = history[history.length - 1].time || t0 + 1;
  const max = Math.max(0.25, ...history.map((h) => Math.abs(h.value)));
  const x = (t) => pad + ((t - t0) / (t1 - t0 || 1)) * (W - 2 * pad);
  const y = (v) => H / 2 - (v / max) * (H / 2 - pad);
  const line = history.map((h, i) => `${i ? "L" : "M"}${x(h.time).toFixed(1)},${y(h.value).toFixed(1)}`).join("");
  const area = `${line}L${x(t1).toFixed(1)},${H / 2}L${x(t0).toFixed(1)},${H / 2}Z`;
  const id = "c" + Math.random().toString(36).slice(2, 7);
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="News sentiment over 7 days">
    <defs><clipPath id="${id}a"><rect x="0" y="0" width="${W}" height="${H / 2}"/></clipPath><clipPath id="${id}b"><rect x="0" y="${H / 2}" width="${W}" height="${H / 2}"/></clipPath></defs>
    <line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="${css("--border")}" stroke-width="1"/>
    <path d="${area}" fill="${css("--good")}" opacity=".25" clip-path="url(#${id}a)"/>
    <path d="${area}" fill="${css("--bad")}" opacity=".25" clip-path="url(#${id}b)"/>
    <path d="${line}" fill="none" stroke="${css("--good")}" stroke-width="2" clip-path="url(#${id}a)" vector-effect="non-scaling-stroke"/>
    <path d="${line}" fill="none" stroke="${css("--bad")}" stroke-width="2" clip-path="url(#${id}b)" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

/* ---------------- live updates ---------------- */

function connect() {
  const dot = $("ws-dot");
  if (SNAP) { dot.className = "dot on"; return simulateLive(); }
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  let ping;
  ws.onopen = () => { dot.className = "dot on"; ping = setInterval(() => ws.send("ping"), 25000); };
  ws.onclose = () => { dot.className = "dot off"; clearInterval(ping); setTimeout(connect, 3000); };
  ws.onmessage = (msg) => { const { event, data } = JSON.parse(msg.data); handle(event, data); };
}

function handle(event, data) {
  if (event === "bar" && data.ticker === state.ticker) onBar(data);
  else if (event === "news") onNews(data);
  else if (event === "signal" && data.ticker === state.ticker) {
    const point = { time: Math.floor(Date.parse(data.at) / 1000), value: data.score };
    state.history.push(point);
    state.charts.score.update({ time: shift(point.time), value: point.value });
    renderSignal(data);
  }
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
  renderFeed();
  $("feed").firstElementChild?.classList.add("flash");
  renderMarkers();
  if (state.view !== "news" && !isDesktop()) { state.unseen++; renderBadge(); toast(it); }
  const m = mainImpact(it);
  refreshGraph().then(() => m && flashPath(m.path));
  if (state.entity && (it.entities.includes(state.entity) || m?.path.includes(state.entity))) {
    api(`/api/entity/${encodeURIComponent(state.entity)}?ticker=${state.ticker}`).then((d) => { state.entityData = d; renderEntity(); });
  }
}

// Static demo: random-walk ticks and replay of held-back sample items.
function simulateLive() {
  setInterval(() => {
    const last = state.bars[state.bars.length - 1];
    if (!last) return;
    const now = Math.floor(Date.now() / 60000) * 60;
    const price = +(last.close * Math.exp((Math.random() - 0.5) * 0.0014)).toFixed(2);
    const b = now > last.time ? { ...last, time: now, open: last.close, high: Math.max(last.close, price), low: Math.min(last.close, price), close: price }
      : { ...last, high: Math.max(last.high, price), low: Math.min(last.low, price), close: price };
    onBar(b);
  }, 1500);
  (SNAP.replay || []).forEach((it, i) => setTimeout(() => {
    onNews({ ...it, published_at: new Date().toISOString() });
    if (it.signal) handle("signal", { ...it.signal, at: new Date().toISOString() });
  }, 9000 + i * 22000));
}

/* ---------------- boot ---------------- */

async function loadTicker(ticker) {
  state.ticker = ticker;
  document.querySelectorAll(".js-ticker").forEach((el) => { el.textContent = ticker; });
  const [graph, bars, news, sig] = await Promise.all([
    api(`/api/graph/${ticker}`), api(`/api/bars/${ticker}?days=5`), api(`/api/news?ticker=${ticker}&limit=300`), api(`/api/signal/${ticker}?hours=120`),
  ]);
  state.graph = graph;
  state.news = news;
  state.history = sig.history;
  if (state.cy) { state.cy.destroy(); state.cy = null; }
  setBars(bars);
  state.charts.score.setData(sig.history.map((h) => ({ time: shift(h.time), value: h.value })));
  renderSignal(sig);
  renderTypeChips();
  renderFeed();
  renderMarkers();
  setGraphMode(state.graphMode);
  applyRange();
}

function wire() {
  document.querySelectorAll(".bottom-nav button").forEach((b) => b.addEventListener("click", () => go(`#/${b.dataset.go}`)));
  $("signal").addEventListener("click", () => (isDesktop() ? $("sec-signal").scrollIntoView({ behavior: "smooth" }) : go("#/overview")));
  $("status-btn").addEventListener("click", () => (isDesktop() ? $("sec-events").scrollIntoView({ behavior: "smooth" }) : go("#/events")));
  document.querySelectorAll("#graph-mode button").forEach((b) => b.addEventListener("click", () => setGraphMode(b.dataset.mode)));
  document.querySelectorAll("#tone button").forEach((b) => b.addEventListener("click", () => {
    state.tone = b.dataset.tone;
    document.querySelectorAll("#tone button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    renderFeed();
  }));
  $("news-chips").addEventListener("click", (ev) => {
    const c = ev.target.closest("[data-group]"); if (!c) return;
    state.newsGroup = c.dataset.group; renderFeed();
  });
  $("type-chips").addEventListener("click", (ev) => {
    const c = ev.target.closest("[data-type]"); if (!c) return;
    state.typeFocus = c.dataset.type || null; renderTypeChips(); applyTypeFocus();
  });
  document.querySelectorAll("#sheet-tabs button").forEach((b) => b.addEventListener("click", () =>
    go(`#/e/${encodeURIComponent(state.entity)}/${b.dataset.tab}`)));
  $("sheet").addEventListener("click", (ev) => {
    if (ev.target.closest("[data-close]")) { closeEntity(); return; }
    const t = ev.target.closest("[data-tab-go]");
    if (t) go(`#/e/${encodeURIComponent(state.entity)}/${t.dataset.tabGo}`);
  });
  // Any entity chip anywhere opens that entity (inside the sheet it navigates to it).
  document.addEventListener("click", (ev) => {
    const el = ev.target.closest("[data-entity]");
    if (el && !ev.target.closest("a")) { ev.preventDefault(); openEntity(el.dataset.entity); }
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && state.entity) closeEntity(); });
  window.addEventListener("hashchange", route);
  matchMedia("(min-width: 1100px)").addEventListener("change", () => setView(state.view));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => location.reload());
}

async function boot() {
  state.meta = await api("/api/meta");
  const sel = $("ticker");
  sel.innerHTML = state.meta.tickers.map((t) => `<option>${esc(t)}</option>`).join("");
  sel.onchange = () => loadTicker(sel.value);
  const badge = $("mode-badge");
  if (!state.meta.price_live || state.meta.scorer !== "claude") { badge.textContent = "DEMO"; badge.classList.add("show"); }
  buildCharts();
  wire();
  await loadTicker(state.meta.tickers[0]);
  route();
  connect();
  refreshStatus();
  if (!SNAP) {
    setInterval(refreshStatus, 15000);
    setInterval(async () => { // decay moves the score even without news
      const sig = await api(`/api/signal/${state.ticker}?hours=120`);
      state.history = sig.history;
      state.charts.score.setData(sig.history.map((h) => ({ time: shift(h.time), value: h.value })));
      renderSignal(sig);
    }, 5 * 60 * 1000);
  }
}

boot().catch((err) => {
  document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="empty">Failed to load: ${esc(err.message)}</p>`);
});
