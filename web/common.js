// Shared helpers for both dashboards: data fetching, the road map and line charts (plain SVG, works offline).
const NS = "http://www.w3.org/2000/svg";
const LEVEL = { LOW: "var(--free)", MEDIUM: "var(--slow)", HIGH: "var(--jam)" };
const LEVEL_WORD = { LOW: "Free flow", MEDIUM: "Slow", HIGH: "Jam" };
const PLACE_IMG = { School: "img/school.png", Office: "img/office.png", Hospital: "img/hospital.png" };

// Where the live data comes from. Empty = this same server (python dashboard_server.py).
// On Netlify: set it in config.js, or open the page with ?api=https://xxxx.trycloudflare.com
const QS = new URLSearchParams(location.search);
const API_BASE = (QS.get("api") || window.API_BASE || "").replace(/\/$/, "");
const CONTROL_KEY = QS.get("key") || window.CONTROL_KEY || "";
const api = u => API_BASE + u;

async function getJSON(url) {
  const r = await fetch(api(url), { cache: "no-store" });
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(api(url), { method: "POST", headers: { "Content-Type": "application/json", "X-Control-Key": CONTROL_KEY },
                                    body: JSON.stringify(body) });
  if (r.status === 401 && location.pathname === "/control") {
    location.assign("/login");
    throw new Error("login required");
  }
  if (r.status === 403) { alert("Control is locked. Open the control room with ?key=<your key>."); }
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}
function el(tag, attrs = {}, parent) {
  const isSvg = ["svg", "g", "path", "line", "rect", "circle", "text", "image", "polyline", "title"].includes(tag);
  const e = isSvg ? document.createElementNS(NS, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k === "class") e.setAttribute("class", v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  if (parent) parent.appendChild(e);
  return e;
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function fmt(n, d = 0) { return n == null || isNaN(n) ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }); }

// ---------------------------------------------------------------- tooltip
const tip = (() => {
  const t = document.createElement("div");
  t.className = "tip";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(t));
  return {
    show(html, x, y) {
      t.innerHTML = html;
      t.style.opacity = 1;
      const w = t.offsetWidth, h = t.offsetHeight;
      t.style.left = Math.min(window.innerWidth - w - 8, x + 14) + "px";
      t.style.top = Math.max(8, y - h - 12) + "px";
    },
    hide() { t.style.opacity = 0; },
  };
})();

// ---------------------------------------------------------------- road map
function createMap(container, net, opts = {}) {
  const [x0, y0, x1, y1] = net.bounds;
  const pad = opts.pad ?? 40;
  const W = x1 - x0 + 2 * pad, H = y1 - y0 + 2 * pad;
  const X = x => x - x0 + pad, Y = y => y1 - y + pad;          // SUMO y goes up, SVG y goes down
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "xMidYMid meet", role: "img",
                          "aria-label": opts.label || "Road network" }, container);
  const gCase = el("g", {}, svg), gRoad = el("g", {}, svg), gRoute = el("g", {}, svg),
        gHit = el("g", {}, svg), gTls = el("g", {}, svg), gPlace = el("g", {}, svg);
  const roads = {};
  const pathOf = e => net.edges[e].shape.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join(" ");
  for (const [e, d] of Object.entries(net.edges)) {
    const w = Math.max(3.2, d.lanes * 3.2);
    const p = pathOf(e);
    el("path", { d: p, class: "casing", "stroke-width": w + 2.4 }, gCase);
    const road = el("path", { d: p, class: "road", "stroke-width": w, stroke: "#c3ccd2" }, gRoad);
    const hit = el("path", { d: p, class: "road-hit", "stroke-width": w + 10 }, gHit);
    roads[e] = { road, w, data: null };
    hit.addEventListener("mousemove", ev => {
      const r = roads[e].data;
      tip.show(`<b>Road ${esc(e)}</b><br>` + (r ? `${LEVEL_WORD[r.level]}<br>${r.halt} waiting, ${r.veh} vehicles<br>${fmt(r.speed_kmh)} km/h, ${fmt(r.tt)} s to drive` : "no data yet"), ev.clientX, ev.clientY);
    });
    hit.addEventListener("mouseleave", tip.hide);
    if (opts.onRoadClick) hit.addEventListener("click", () => opts.onRoadClick(e));
  }
  const tls = {};
  for (const j of net.tls || []) {
    const g = el("g", { transform: `translate(${X(j.x)},${Y(j.y)})` }, gTls);
    el("rect", { x: -5.5, y: -5.5, width: 11, height: 11, rx: 2.5, fill: "#13232e" }, g);
    const lamp = el("circle", { r: 3, fill: "#5b6770" }, g);
    tls[j.id] = { g, lamp };
    g.addEventListener("mousemove", ev => {
      const s = tls[j.id].data;
      tip.show(`<b>Signal ${esc(j.id)}</b><br>` + (s ? `Green for ${esc(s.green.join(", ") || "none (changing)")}<br>${s.green_for} s so far` +
        Object.entries(s.queues).map(([r, q]) => `<br>${esc(r)}: ${q} waiting`).join("") : ""), ev.clientX, ev.clientY);
    });
    g.addEventListener("mouseleave", tip.hide);
  }
  let placeNodes = {};
  return {
    X, Y, svg,
    update(state) {
      const rs = (state && state.roads) || {};
      for (const [e, o] of Object.entries(roads)) {
        o.data = rs[e] || null;
        o.road.setAttribute("stroke", o.data ? LEVEL[o.data.level] : "#c3ccd2");
      }
      const js = (state && state.junctions) || {};
      for (const [id, o] of Object.entries(tls)) {
        o.data = js[id] || null;
        o.lamp.setAttribute("fill", o.data && o.data.green.length ? "#3ddc6a" : "#f5a524");
      }
      for (const p of (state && state.places) || []) {
        let n = placeNodes[p.name];
        if (!n) {
          const g = el("g", { transform: `translate(${X(p.xy[0])},${Y(p.xy[1])})` }, gPlace);
          const halo = el("circle", { r: 17, fill: "none", "stroke-width": 2.5 }, g);
          el("image", { href: PLACE_IMG[p.name] || "", x: -12, y: -12, width: 24, height: 24 }, g);
          n = placeNodes[p.name] = { g, halo };
          g.addEventListener("mousemove", ev => tip.show(`<b>${esc(p.name)}</b> beside road ${esc(p.edge)}<br>` +
            (n.on ? "Priority on: extra green" + (p.name === "School" ? ", 25 km/h" : "") : "Priority off"), ev.clientX, ev.clientY));
          g.addEventListener("mouseleave", tip.hide);
        }
        n.on = p.on;
        n.halo.setAttribute("stroke", p.on ? "var(--sign)" : "transparent");
        n.halo.setAttribute("fill", p.on ? "rgba(11,79,138,.10)" : "transparent");
      }
    },
    highlight(edgesList) {          // outline roads (e.g. a congested road) with a dashed ring
      gCase.querySelectorAll(".hl").forEach(n => n.remove());
      for (const e of edgesList || []) {
        if (!roads[e]) continue;
        el("path", { d: pathOf(e), class: "hl", fill: "none", stroke: "var(--ink)", "stroke-width": roads[e].w + 12,
                     "stroke-linecap": "round", opacity: .16 }, gCase);
      }
    },
    route(edgesList) {
      gRoute.querySelectorAll(".route").forEach(n => n.remove());
      if (!edgesList) return;
      const pts = [];
      for (const e of edgesList) if (net.edges[e]) for (const p of net.edges[e].shape) pts.push(X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1));
      const d = "M" + pts.join(" L");
      el("path", { d, class: "route", stroke: "#fff", "stroke-width": 7 }, gRoute);
      el("path", { d, class: "route", "stroke-width": 4.5, "stroke-dasharray": "9 6" }, gRoute);
    },
  };
}

// ---------------------------------------------------------------- line chart (one y axis, crosshair + tooltip)
function lineChart(container, cfg) {
  container.innerHTML = "";
  const W = Math.max(280, container.clientWidth), H = cfg.height || 240;
  const m = { l: 44, r: cfg.endLabels ? 96 : 14, t: 12, b: 30 };
  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": cfg.label || "chart" }, container);
  const all = cfg.series.flatMap(s => s.points);
  if (!all.length) {
    el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", fill: "var(--ink-3)", "font-size": 13, text: cfg.empty || "No data yet" }, svg);
    return;
  }
  const xmin = cfg.xmin ?? Math.min(...all.map(p => p[0])), xmax = cfg.xmax ?? Math.max(...all.map(p => p[0]));
  let ymax = Math.max(1, ...all.map(p => p[1]));
  const step = niceStep(ymax / 4); ymax = Math.ceil(ymax / step) * step;
  const sx = x => m.l + (x - xmin) / Math.max(1e-9, xmax - xmin) * (W - m.l - m.r);
  const sy = y => H - m.b - y / ymax * (H - m.t - m.b);
  for (let v = 0; v <= ymax + 1e-9; v += step) {
    el("line", { x1: m.l, x2: W - m.r, y1: sy(v), y2: sy(v), stroke: v ? "var(--rule-soft)" : "var(--rule)" }, svg);
    el("text", { x: m.l - 8, y: sy(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--ink-3)", class: "num", text: fmt(v) }, svg);
  }
  const ticks = cfg.xTicks ? cfg.xTicks(xmin, xmax) : [];
  for (const [x, lab] of ticks) el("text", { x: sx(x), y: H - 10, "text-anchor": "middle", "font-size": 11, fill: "var(--ink-3)", class: "num", text: lab }, svg);
  if (cfg.nowX != null && cfg.nowX >= xmin && cfg.nowX <= xmax) {
    el("line", { x1: sx(cfg.nowX), x2: sx(cfg.nowX), y1: m.t, y2: H - m.b, stroke: "var(--ink)", "stroke-dasharray": "3 3" }, svg);
    el("text", { x: sx(cfg.nowX) + 4, y: m.t + 10, "font-size": 11, fill: "var(--ink-2)", text: "now" }, svg);
  }
  for (const s of cfg.series) {
    if (!s.points.length) continue;
    el("path", { d: s.points.map((p, i) => (i ? "L" : "M") + sx(p[0]).toFixed(1) + " " + sy(p[1]).toFixed(1)).join(" "),
                 fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    if (cfg.endLabels) {
      const last = s.points[s.points.length - 1];
      el("circle", { cx: sx(last[0]), cy: sy(last[1]), r: 4, fill: s.color, stroke: "var(--panel)", "stroke-width": 2 }, svg);
      el("text", { x: sx(last[0]) + 8, y: sy(last[1]) + 4 + (s.labelDy || 0), "font-size": 12, fill: "var(--ink-2)", text: s.short || s.name }, svg);
    }
  }
  // hover layer
  const cross = el("line", { y1: m.t, y2: H - m.b, stroke: "var(--ink-3)", opacity: 0 }, svg);
  const dots = cfg.series.map(s => el("circle", { r: 4.5, fill: s.color, stroke: "var(--panel)", "stroke-width": 2, opacity: 0 }, svg));
  const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", ev => {
    const box = svg.getBoundingClientRect();
    const x = xmin + (ev.clientX - box.left - m.l) / (W - m.l - m.r) * (xmax - xmin);
    let html = "", cx = null;
    cfg.series.forEach((s, i) => {
      if (!s.points.length) return;
      let best = s.points[0];
      for (const p of s.points) if (Math.abs(p[0] - x) < Math.abs(best[0] - x)) best = p;
      cx = sx(best[0]);
      dots[i].setAttribute("cx", sx(best[0])); dots[i].setAttribute("cy", sy(best[1])); dots[i].setAttribute("opacity", 1);
      if (!html) html = `<b>${esc(cfg.xFmt ? cfg.xFmt(best) : best[0])}</b>`;
      html += `<br><span style="color:${s.tipColor || "#fff"}">●</span> ${esc(s.name)}: <b>${fmt(best[1], cfg.decimals || 0)}</b>${cfg.unit ? " " + cfg.unit : ""}`;
    });
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("opacity", 1);
    tip.show(html, ev.clientX, ev.clientY);
  });
  hit.addEventListener("mouseleave", () => { tip.hide(); cross.setAttribute("opacity", 0); dots.forEach(d => d.setAttribute("opacity", 0)); });
}
function niceStep(v) {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(v, 1e-9))));
  for (const k of [1, 2, 2.5, 5, 10]) if (v <= k * p) return k * p;
  return 10 * p;
}
