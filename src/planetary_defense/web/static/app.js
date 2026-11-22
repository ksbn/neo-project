// ASTRAEA dashboard: everything comes from the REST API (openapi.yaml), nothing from the database directly.
//
// One state object drives the KPI strip, filters, approach map, table, watchlist, drawer and compare tray.
// Filters live in the URL, so a view can be shared or restored with the back button.

import { api } from "./api.js";
import { ApproachMap } from "./chart.js";
import { DetailDrawer } from "./detail.js";
import { $, $$, LOCALE, SEVERITIES, badge, clear, debounce, el, ld, metres, na, relativeDays, svg, timeAgo } from "./dom.js";
import { WatchForm } from "./form.js";
import { Viewport } from "./viewport.js";

const PAGE = 25;
const SUMMARY_REFRESH_MS = 60_000;
const DEFAULT_FILTERS = { q: "", hazardous: null, max_distance_ld: null, min_velocity_km_s: null, min_severity: null,
                          watched: null, approach_from: null, approach_to: null, sort: "-approach_date" };
const PRESETS = [
  { key: "1", label: "All", filters: {} },
  { key: "2", label: "Hazardous", filters: { hazardous: true } },
  { key: "3", label: "Inside 5 LD", filters: { max_distance_ld: 5 } },
  { key: "4", label: "Fast > 25 km/s", filters: { min_velocity_km_s: 25 } },
  { key: "5", label: "Warning+", filters: { min_severity: "warning" } },
  { key: "6", label: "★ Watched", filters: { watched: true } },
];
const COLUMNS = [
  { label: "", sort: null }, { label: "Object", sort: ["name"] }, { label: "Pass (UTC)", sort: ["-approach_date", "approach_date"] },
  { label: "Distance", sort: ["distance", "-distance"], num: true }, { label: "Velocity", sort: ["-velocity", "velocity"], num: true },
  { label: "Diameter", sort: ["-diameter"], num: true }, { label: "Alert", sort: null },
];

const state = { filters: { ...DEFAULT_FILTERS }, offset: 0, items: [], total: 0, cursor: -1, hot: null,
                compare: new Map(), listCtl: null, chartCtl: null, track: null };

// ------------------------------------------------------------------ toasts and API status

function toast(text, kind = "") {
  const t = el("div", { class: ["toast", kind] }, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

function apiStatus(ok, text) {
  const pill = $("#api-status");
  pill.className = `pill ${ok ? "ok" : "bad"}`;
  pill.textContent = text;
}

function errorState(err, retry) {
  return el("div", { class: ["state", "error"] },
    el("div", { class: "icon" }, "⚠"),
    el("div", {}, err.message),
    err.requestId ? el("div", { class: "rid" }, `request ${err.requestId}`) : null,
    err.retryAfter ? el("div", { class: "muted" }, `The server asks to wait ${err.retryAfter} s.`) : null,
    el("button", { class: "btn", type: "button", onclick: retry }, "Retry"));
}

// ------------------------------------------------------------------ URL <-> filters

function readUrl() {
  const p = new URLSearchParams(location.search);
  const f = { ...DEFAULT_FILTERS };
  for (const k of Object.keys(DEFAULT_FILTERS)) {
    if (!p.has(k)) continue;
    const v = p.get(k);
    f[k] = ["hazardous", "watched"].includes(k) ? v === "true"
      : ["max_distance_ld", "min_velocity_km_s"].includes(k) ? Number(v) : v;
  }
  state.filters = f;
  state.offset = Math.max(0, Number(p.get("offset")) || 0);
  state.track = /^[0-9]{1,20}$/.test(p.get("track") ?? "") ? p.get("track") : null;
}

function writeUrl() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(state.filters)) if (v !== null && v !== "" && v !== DEFAULT_FILTERS[k]) p.set(k, v);
  if (state.offset) p.set("offset", state.offset);
  if (state.track) p.set("track", state.track);
  const url = `${location.pathname}${p.size ? `?${p}` : ""}`;
  if (url !== `${location.pathname}${location.search}`) history.pushState(null, "", url);
}

function queryParams(extra = {}) {
  const f = state.filters;
  return { q: f.q || null, hazardous: f.hazardous, max_distance_ld: f.max_distance_ld, min_velocity_km_s: f.min_velocity_km_s,
           min_severity: f.min_severity, watched: f.watched, approach_from: f.approach_from, approach_to: f.approach_to,
           sort: f.sort, ...extra };
}

function setFilters(patch, { replace = false } = {}) {
  state.filters = replace ? { ...DEFAULT_FILTERS, sort: state.filters.sort, ...patch } : { ...state.filters, ...patch };
  state.offset = 0;
  state.cursor = -1;
  writeUrl();
  syncControls();
  loadObjects();
}

// ------------------------------------------------------------------ KPI strip (GET /summary)

async function loadSummary() {
  const box = $("#kpis");
  if (!box.children.length) box.append(...Array.from({ length: 5 }, () => el("div", { class: ["kpi", "skeleton"] })));
  try {
    const { data } = await api.summary();
    apiStatus(true, "API online");
    renderSummary(data);
  } catch (err) {
    apiStatus(false, err.status === 0 ? "API offline" : `API ${err.status}`);
    clear(box, el("div", { class: "kpi", style: { gridColumn: "1 / -1" } }, errorState(err, loadSummary)));
  }
}

function renderSummary(s) {
  if (!viewport.id) viewport.track(state.track ?? s.closest_approach?.neo_reference_id);
  const counts = s.alert_counts, alerted = SEVERITIES.reduce((n, k) => n + counts[k], 0);
  const worst = [...SEVERITIES].reverse().find((k) => counts[k] > 0) ?? null;
  const days = s.approaches_per_day, peak = Math.max(1, ...days.map((d) => d.count));
  const spark = svg("svg", { class: "spark", viewBox: `0 0 ${days.length * 10} 40`, preserveAspectRatio: "none",
    role: "img", "aria-label": "Approaches per day, last 14 days" },
    days.map((d, i) => {
      const h = Math.max(1.5, (d.count / peak) * 36);
      const bar = svg("rect", { x: i * 10 + 1, y: 40 - h, width: 8, height: h, rx: 1.5, fill: d.count ? "#22d3c5" : "#1f3f4a" },
        svg("title", {}, `${d.date}: ${d.count} approaches · click to filter`));
      bar.addEventListener("click", () => setFilters({ approach_from: d.date, approach_to: d.date }));
      return bar;
    }));
  const sync = s.last_sync_at ? (Date.now() - new Date(s.last_sync_at)) / 3_600_000 : null;
  const syncColor = sync === null ? "var(--muted)" : sync < 12 ? "var(--ok)" : sync < 30 ? "var(--watch)" : "var(--critical)";

  clear($("#kpis"),
    el("div", { class: "kpi" }, el("h3", {}, "Threat picture"),
      el("div", { class: "big", style: { color: worst ? `var(--${worst})` : "var(--ok)" } }, worst ? worst.toUpperCase() : "QUIET"),
      el("div", { class: "sevbar", role: "group", "aria-label": "Objects by alert level" },
        SEVERITIES.filter((k) => counts[k]).map((k) => el("button", { type: "button", title: `${counts[k]} ${k}: show ${k}+`,
          style: { flex: counts[k], background: `var(--${k})` }, onclick: () => setFilters({ min_severity: k }) }))),
      el("div", { class: "sevlegend" }, SEVERITIES.map((k) => el("button", { type: "button", onclick: () => setFilters({ min_severity: k }) },
        el("i", { style: { background: `var(--${k})` } }), `${k} ${counts[k]}`)))),
    el("div", { class: "kpi" }, el("h3", {}, "Objects tracked"),
      el("div", { class: "big" }, s.total_objects.toLocaleString(LOCALE)),
      el("div", { class: "sub" }, el("button", { class: "kpi-link", type: "button", onclick: () => setFilters({ hazardous: true }) },
        `${s.hazardous_objects} potentially hazardous`), ` · ${alerted} alerted`),
      el("div", { class: "sub" }, el("button", { class: "kpi-link", type: "button", onclick: () => setFilters({ watched: true }) },
        `★ ${s.watched_objects} watched`))),
    el("div", { class: "kpi" }, el("h3", {}, "Closest recent pass"),
      s.closest_approach
        ? [el("div", { class: "big" }, ld(s.closest_approach.approach.miss_distance_ld)),
           el("div", { class: "sub" }, el("button", { class: "kpi-link", type: "button", onclick: () => viewport.track(s.closest_approach.neo_reference_id) },
             s.closest_approach.name), ` · ${s.closest_approach.approach.date}`)]
        : [el("div", { class: "big" }, na("—")), el("div", { class: "sub" }, "No passes stored yet")]),
    el("div", { class: "kpi" }, el("h3", {}, "Approaches · 14 days"),
      el("div", { class: "big" }, days.reduce((n, d) => n + d.count, 0)), spark),
    el("div", { class: "kpi" }, el("h3", {}, "Data freshness"),
      el("div", { class: "big", style: { color: syncColor } }, s.last_sync_at ? timeAgo(s.last_sync_at) : na("never synced")),
      el("div", { class: "sub" }, s.latest_approach_date ? `Newest pass ${s.latest_approach_date}` : "No passes stored"),
      el("div", { class: "sub" }, "Refreshes every minute")),
  );
}

// ------------------------------------------------------------------ objects (GET /objects)

async function loadObjects() {
  state.listCtl?.abort();
  state.listCtl = new AbortController();
  const signal = state.listCtl.signal;
  const table = $("#table");
  if (!state.items.length) clear(table, el("div", { class: "skeleton", style: { height: "240px" } }));
  table.setAttribute("aria-busy", "true");
  loadChart();
  try {
    const { data } = await api.objects(queryParams({ limit: PAGE, offset: state.offset }), { signal });
    state.items = data.items;
    state.total = data.pagination.total;
    state.pagination = data.pagination;
    if (!viewport.id && !state.track && data.items.length) setTimeout(() => viewport.id || viewport.track(data.items[0].neo_reference_id), 1500);
    if (state.cursor >= state.items.length) state.cursor = state.items.length - 1;
    renderTable();
    apiStatus(true, "API online");
  } catch (err) {
    if (err.name === "AbortError") return;
    state.items = [];
    clear($("#pager"));
    $("#result-count").textContent = "";
    clear(table, errorState(err, loadObjects));
    if (err.status === 0) apiStatus(false, "API offline");
  } finally {
    table.removeAttribute("aria-busy");
  }
}

async function loadChart() {
  state.chartCtl?.abort();
  state.chartCtl = new AbortController();
  try {
    const { data } = await api.objects(queryParams({ limit: 100, offset: 0, sort: "-approach_date" }), { signal: state.chartCtl.signal });
    chart.setItems(data.items);
  } catch (err) {
    if (err.name !== "AbortError") chart.showState(errorState(err, loadChart));
  }
}

function renderTable() {
  const { items, total, pagination } = state;
  $("#result-count").textContent = `${total.toLocaleString(LOCALE)} match${total === 1 ? "" : "es"}`;
  if (!items.length) {
    const filtered = Object.entries(state.filters).some(([k, v]) => k !== "sort" && v !== null && v !== "");
    clear($("#table"), el("div", { class: "state" },
      el("div", { class: "icon" }, "☄"),
      el("div", {}, filtered ? "No objects match these filters." : "No objects stored yet. Run the pipeline to fetch NASA data."),
      filtered ? el("button", { class: "btn", type: "button", onclick: () => setFilters({}, { replace: true }) }, "Clear filters (0)") : null));
    clear($("#pager"));
    return;
  }
  const maxLd = Math.max(...items.map((o) => o.latest_approach?.miss_distance_ld ?? 0), 1);
  const head = el("tr", {}, COLUMNS.map((c) => {
    if (!c.sort) return el("th", { scope: "col" }, c.label);
    const active = c.sort.includes(state.filters.sort);
    const next = active ? c.sort[(c.sort.indexOf(state.filters.sort) + 1) % c.sort.length] : c.sort[0];
    const arrow = active ? (state.filters.sort.startsWith("-") ? " ↓" : " ↑") : "";
    return el("th", { scope: "col", "aria-sort": active ? (state.filters.sort.startsWith("-") ? "descending" : "ascending") : null,
      style: c.num ? { textAlign: "right" } : null },
      el("button", { type: "button", onclick: () => setFilters({ sort: next }) }, c.label + arrow));
  }));
  const body = items.map((o, i) => {
    const a = o.latest_approach;
    const row = el("tr", {
      class: [i === state.cursor && "cursor", o.neo_reference_id === state.hot && "hot", state.compare.has(o.neo_reference_id) && "compared"],
      dataset: { id: o.neo_reference_id, index: i }, tabindex: -1,
      onclick: () => { state.cursor = i; viewport.track(o.neo_reference_id); drawer.open(o.neo_reference_id); },
      onpointerenter: () => highlight(o.neo_reference_id), onpointerleave: () => highlight(null),
    },
      el("td", {}, o.watched ? el("span", { class: "star", title: "On the watchlist" }, "★") : ""),
      el("td", { class: "name-cell", title: o.name }, o.name, o.is_potentially_hazardous ? el("span", { class: "pha-tag" }, "PHA") : null),
      el("td", {}, a ? [a.date, el("span", { class: "muted" }, ` · ${relativeDays(a.date)}`)] : na("—")),
      el("td", { class: "num" }, a ? [el("span", { class: "distbar", style: { width: `${Math.max(3, 46 * Math.log10(1 + a.miss_distance_ld) / Math.log10(1 + maxLd))}px`,
        background: a.miss_distance_ld < 1 ? "var(--critical)" : a.miss_distance_ld <= 5 ? "var(--warning)" : "var(--accent)" } }), ld(a.miss_distance_ld)] : na("—")),
      el("td", { class: "num" }, a ? `${a.relative_velocity_km_s.toFixed(1)} km/s` : na("—")),
      el("td", { class: "num" }, metres(o.estimated_diameter_max_m)),
      el("td", {}, badge(o.alert_level)));
    return row;
  });
  clear($("#table"), el("table", { class: "objects" }, el("thead", {}, head), el("tbody", {}, body)));

  const page = Math.floor(pagination.offset / PAGE) + 1, pages = Math.max(1, Math.ceil(total / PAGE));
  clear($("#pager"),
    el("button", { class: ["btn", "small"], type: "button", disabled: pagination.prev_offset === null, onclick: () => goPage(-1) }, "← Prev  ["),
    el("span", { class: "muted" }, `Page ${page} of ${pages} · ${pagination.offset + 1}–${pagination.offset + items.length} of ${total}`),
    el("button", { class: ["btn", "small"], type: "button", disabled: pagination.next_offset === null, onclick: () => goPage(1) }, "]  Next →"));
}

function goPage(dir) {
  const p = state.pagination;
  const target = dir > 0 ? p?.next_offset : p?.prev_offset;
  if (target === null || target === undefined) return;
  state.offset = target;
  state.cursor = dir > 0 ? 0 : PAGE - 1;
  writeUrl();
  loadObjects();
}

function highlight(id) {
  state.hot = id;
  chart.setHot(id);
  for (const tr of $$("#table tbody tr")) tr.classList.toggle("hot", tr.dataset.id === id);
}

function moveCursor(delta) {
  if (!state.items.length) return;
  state.cursor = Math.min(state.items.length - 1, Math.max(0, state.cursor + delta));
  for (const tr of $$("#table tbody tr")) tr.classList.toggle("cursor", Number(tr.dataset.index) === state.cursor);
  const row = $(`#table tbody tr[data-index="${state.cursor}"]`);
  row?.scrollIntoView({ block: "nearest" });
  highlight(state.items[state.cursor]?.neo_reference_id ?? null);
  followCursor();
}

const followCursor = debounce(() => viewport.track(state.items[state.cursor]?.neo_reference_id), 600);

// ------------------------------------------------------------------ filters UI

const DIST_STEPS = 100;
const sliderToLd = (v) => (Number(v) >= DIST_STEPS ? null : Number((0.5 * 1000 ** (v / DIST_STEPS)).toPrecision(2)));
const ldToSlider = (x) => (x === null ? DIST_STEPS : Math.round((Math.log10(x / 0.5) / 3) * DIST_STEPS));

function syncControls() {
  const f = state.filters;
  if (document.activeElement !== $("#q")) $("#q").value = f.q ?? "";
  $("#dist").value = ldToSlider(f.max_distance_ld);
  $("#dist-out").textContent = f.max_distance_ld === null ? "any" : `${f.max_distance_ld} LD`;
  $("#vel").value = f.min_velocity_km_s ?? 0;
  $("#vel-out").textContent = f.min_velocity_km_s ? `${f.min_velocity_km_s} km/s` : "any";
  $("#sev").value = f.min_severity ?? "";
  for (const btn of $$("#presets .preset")) {
    const preset = PRESETS[Number(btn.dataset.index)];
    const wanted = { ...DEFAULT_FILTERS, sort: f.sort, ...preset.filters };
    btn.setAttribute("aria-pressed", String(Object.keys(DEFAULT_FILTERS).every((k) => wanted[k] === f[k])));
  }
  const labels = {
    q: (v) => `name ~ “${v}”`, hazardous: (v) => (v ? "hazardous" : "not hazardous"), max_distance_ld: (v) => `≤ ${v} LD`,
    min_velocity_km_s: (v) => `≥ ${v} km/s`, min_severity: (v) => `${v}+`, watched: (v) => (v ? "★ watched" : "not watched"),
    approach_from: (v) => `from ${v}`, approach_to: (v) => `to ${v}`,
  };
  clear($("#active-filters"), Object.entries(labels).filter(([k]) => f[k] !== null && f[k] !== "").map(([k, label]) =>
    el("span", { class: "chip" }, label(f[k]),
      el("button", { type: "button", "aria-label": `Remove filter ${label(f[k])}`, onclick: () => setFilters({ [k]: DEFAULT_FILTERS[k] }) }, "×"))));
}

function buildFilters() {
  clear($("#presets"), PRESETS.map((p, i) => el("button", { class: "preset", type: "button", dataset: { index: i },
    "aria-pressed": "false", onclick: () => setFilters(p.filters, { replace: true }) }, p.label, el("kbd", {}, p.key))));
  const search = debounce((v) => setFilters({ q: v.trim() }), 250);  // live search, one request per pause
  $("#q").addEventListener("input", (e) => search(e.target.value));
  const dist = debounce((v) => setFilters({ max_distance_ld: sliderToLd(v) }), 200);
  $("#dist").addEventListener("input", (e) => {
    const x = sliderToLd(e.target.value);
    $("#dist-out").textContent = x === null ? "any" : `${x} LD`;
    dist(e.target.value);
  });
  const vel = debounce((v) => setFilters({ min_velocity_km_s: Number(v) || null }), 200);
  $("#vel").addEventListener("input", (e) => {
    $("#vel-out").textContent = Number(e.target.value) ? `${e.target.value} km/s` : "any";
    vel(e.target.value);
  });
  $("#sev").addEventListener("change", (e) => setFilters({ min_severity: e.target.value || null }));
}

// ------------------------------------------------------------------ watchlist (GET /watchlist)

async function loadWatchlist() {
  const box = $("#watchlist");
  try {
    const { data } = await api.watchlist({ limit: 50 });
    if (!data.items.length) {
      clear(box, el("div", { class: "state" }, el("div", { class: "icon" }, "☆"), "Nothing watched yet.",
        el("div", { class: "muted" }, "Select a row and press ", el("kbd", {}, "w"), ", or use + Add.")));
      return;
    }
    clear(box, data.items.map((w) => el("div", { class: "wl-item", tabindex: 0, role: "button",
      onclick: () => viewport.track(w.neo_reference_id),
      onkeydown: (e) => e.key === "Enter" && viewport.track(w.neo_reference_id) },
      el("span", {}, w.name), el("span", { class: `prio prio-${w.priority}` }, w.priority),
      el("div", { class: "meta" }, w.alert_threshold_ld !== null ? `flag ≤ ${w.alert_threshold_ld} LD · ` : "",
        `added ${timeAgo(w.created_at)} `, w.tags.map((t) => el("span", { class: "tag" }, t))))),
      data.pagination.total > data.items.length ? el("div", { class: "muted" }, `+ ${data.pagination.total - data.items.length} more`) : null);
  } catch (err) {
    clear(box, errorState(err, loadWatchlist));
  }
}

// ------------------------------------------------------------------ compare tray

async function toggleCompare(item) {
  if (!item) return;
  const id = item.neo_reference_id;
  if (state.compare.has(id)) state.compare.delete(id);
  else {
    if (state.compare.size >= 3) { toast("Compare up to 3 objects. Press x on one to remove it."); return; }
    state.compare.set(id, { item, detail: null });
    api.object(id).then(({ data }) => { if (state.compare.has(id)) { state.compare.get(id).detail = data; renderCompare(); } }).catch(() => {});
  }
  renderCompare();
  for (const tr of $$("#table tbody tr")) tr.classList.toggle("compared", state.compare.has(tr.dataset.id));
}

function renderCompare() {
  const tray = $("#compare");
  const entries = [...state.compare.values()];
  tray.hidden = !entries.length;
  if (!entries.length) return;
  const best = (f, pick) => {
    const vals = entries.map((e) => f(e)).filter((v) => v !== null && v !== undefined);
    return vals.length ? pick(...vals) : null;
  };
  const closest = best((e) => e.item.latest_approach?.miss_distance_ld, Math.min);
  const fastest = best((e) => e.item.latest_approach?.relative_velocity_km_s, Math.max);
  const biggest = best((e) => e.item.estimated_diameter_max_m, Math.max);
  clear(tray, entries.map(({ item, detail }) => {
    const a = item.latest_approach, next = detail?.next_predicted_approach;
    const mark = (v, b) => (v !== null && v !== undefined && v === b ? "best" : null);
    return el("div", { class: "cmp-card" },
      el("b", {}, item.name, " ", badge(item.alert_level)),
      el("div", { class: mark(a?.miss_distance_ld, closest) }, "Distance ", a ? ld(a.miss_distance_ld) : na()),
      el("div", { class: mark(a?.relative_velocity_km_s, fastest) }, "Velocity ", a ? `${a.relative_velocity_km_s.toFixed(1)} km/s` : na()),
      el("div", { class: mark(item.estimated_diameter_max_m, biggest) }, "Diameter ", metres(item.estimated_diameter_max_m)),
      el("div", {}, "Next pass ", detail === null ? "…" : next ? `${next.date} · ${next.miss_distance_ld.toFixed(1)} LD` : na(detail.orbit.tracked ? "none listed" : "N/A (untracked)")),
      el("button", { class: "linkish", type: "button", onclick: () => toggleCompare(item) }, "remove"));
  }), el("button", { class: ["btn", "small"], type: "button", onclick: () => { state.compare.clear(); renderCompare(); renderTable(); } }, "Clear ⇆"));
}

// ------------------------------------------------------------------ keyboard

function onKey(e) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName ?? "") || $("dialog[open]");
  if (e.key === "Escape") {
    if (drawer.isOpen) { drawer.close(); e.preventDefault(); }
    else if (document.activeElement === $("#q")) $("#q").blur();
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const item = state.items[state.cursor];
  const actions = {
    "/": () => $("#q").focus(),
    j: () => moveCursor(1), ArrowDown: () => moveCursor(1),
    k: () => moveCursor(-1), ArrowUp: () => moveCursor(-1),
    Enter: () => item && drawer.open(item.neo_reference_id),
    w: () => (drawer.isOpen ? null : item ? form.open({ neoId: item.neo_reference_id, name: item.name }) : form.open()),
    x: () => toggleCompare(item),
    v: () => { if (item) { viewport.track(item.neo_reference_id); $(".stage").scrollIntoView({ behavior: "smooth" }); } },
    "[": () => goPage(-1), "]": () => goPage(1),
    "?": () => $("#help-dialog").showModal(),
    0: () => setFilters({}, { replace: true }),
  };
  const preset = PRESETS.find((p) => p.key === e.key);
  const action = preset ? () => setFilters(preset.filters, { replace: true }) : actions[e.key];
  if (action) { e.preventDefault(); action(); }
}

// ------------------------------------------------------------------ wiring

const chart = new ApproachMap($("#chart"), {
  onHover: (id) => highlight(id),
  onSelect: (id) => viewport.track(id),
  onBrush: (from, to) => setFilters({ approach_from: from, approach_to: to }),
});
const drawer = new DetailDrawer($("#drawer"), $("#scrim"), {
  onWatch: (d) => form.open({ neoId: d.neo_reference_id, name: d.name }),
  onView: (id) => { drawer.close(); viewport.track(id); $(".stage").scrollIntoView({ behavior: "smooth" }); },
});
const viewport = new Viewport({
  frame: $("#vp-frame"), title: $("#vp-title"), telemetry: $("#telemetry"), passGroup: $("#vp-pass"),
  inspectButton: $("#vp-inspect"), onInspect: (id) => drawer.open(id),
  onTrack: (id) => { state.track = id; writeUrl(); highlight(id); },
});
const form = new WatchForm($("#watch-dialog"), {
  toast,
  onCreated: (entry) => { loadWatchlist(); loadSummary(); loadObjects(); drawer.refresh(); viewport.forget(entry.neo_reference_id); },
  onOpenObject: (id) => drawer.open(id),
});

function tick() { $("#clock").textContent = `${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC`; }

window.addEventListener("popstate", () => { readUrl(); syncControls(); loadObjects(); if (state.track) viewport.track(state.track); });
document.addEventListener("keydown", onKey);
$("#help-btn").addEventListener("click", () => $("#help-dialog").showModal());
$("#watch-new").addEventListener("click", () => form.open());

readUrl();
buildFilters();
syncControls();
tick();
setInterval(tick, 30_000);
loadSummary();
loadObjects();
loadWatchlist();
setInterval(() => { if (!document.hidden) loadSummary(); }, SUMMARY_REFRESH_MS);
