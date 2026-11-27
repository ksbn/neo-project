// Approach map: miss distance (log scale) against pass date, one dot per object.
// Shares the table's filters and highlight; dragging across the plot sets a date-range filter.

import { el, svg, clear } from "./dom.js";

// The glass palette (app.css :root): ice blue, warm yellow, amber, coral; quiet objects are soft white.
const COLORS = { info: "#8ec5ff", watch: "#ffd66b", warning: "#ffad5c", critical: "#ff6b7a" };
const QUIET = "#dbe7ff";
const PAD = { left: 46, right: 12, top: 10, bottom: 24 };
const DAY = 86_400_000;

export class ApproachMap {
  constructor(container, { onHover, onSelect, onBrush }) {
    this.container = container;
    this.handlers = { onHover, onSelect, onBrush };
    this.items = [];
    this.hotId = null;
    new ResizeObserver(() => this.render()).observe(container);
  }

  setItems(items) { this.items = items.filter((o) => o.latest_approach); this.render(); }

  /** Dim everything but the highlighted object, only if it is actually plotted. */
  dims() { return Boolean(this.hotId) && this.items.some((o) => o.neo_reference_id === this.hotId); }

  setHot(id) {
    this.hotId = id;
    this.container.classList.toggle("dimmed", this.dims());
    for (const dot of this.container.querySelectorAll(".dot")) dot.classList.toggle("hot", dot.dataset.id === id);
  }

  showState(node) { clear(this.container, node); }

  render() {
    const { container, items } = this;
    const width = container.clientWidth, height = container.clientHeight;
    if (!width || !height) return;
    if (!items.length) {
      clear(container, el("div", { class: "state" }, "No passes to plot for these filters."));
      return;
    }
    const dates = items.map((o) => Date.parse(o.latest_approach.date));
    const lds = items.map((o) => o.latest_approach.miss_distance_ld);
    const x0 = Math.min(...dates) - DAY, x1 = Math.max(...dates) + DAY;
    const y0 = Math.log10(Math.max(0.05, Math.min(...lds) * 0.6)), y1 = Math.log10(Math.max(...lds) * 1.6);
    const sx = (t) => PAD.left + ((t - x0) / (x1 - x0)) * (width - PAD.left - PAD.right);
    const sy = (v) => height - PAD.bottom - ((Math.log10(v) - y0) / (y1 - y0)) * (height - PAD.top - PAD.bottom);
    const tx = (px) => x0 + ((px - PAD.left) / (width - PAD.left - PAD.right)) * (x1 - x0);

    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
      "aria-label": `Approach map of ${items.length} objects` });

    // grid and axes
    const grid = svg("g", { class: "grid" }), axis = svg("g", { class: "axis" });
    for (let p = Math.ceil(y0); p <= Math.floor(y1); p++) {
      const y = sy(10 ** p);
      grid.append(svg("line", { x1: PAD.left, x2: width - PAD.right, y1: y, y2: y }));
      axis.append(svg("text", { x: PAD.left - 6, y: y + 3, "text-anchor": "end" }, `${10 ** p >= 1 ? 10 ** p : (10 ** p).toFixed(1)} LD`));
    }
    const span = (x1 - x0) / DAY, stepDays = span > 120 ? 30 : span > 40 ? 7 : span > 12 ? 2 : 1;
    for (let t = Math.ceil(x0 / DAY) * DAY; t <= x1; t += stepDays * DAY) {
      const d = new Date(t);
      axis.append(svg("text", { x: sx(t), y: height - 6, "text-anchor": "middle" },
        `${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`));
    }
    root.append(grid, axis);
    if (1 >= 10 ** y0 && 1 <= 10 ** y1) {  // the Moon's distance
      root.append(svg("line", { class: "ld1", x1: PAD.left, x2: width - PAD.right, y1: sy(1), y2: sy(1) }),
        svg("text", { x: width - PAD.right - 2, y: sy(1) - 3, "text-anchor": "end", fill: "#ff6b7a", "font-size": 10 }, "Moon · 1 LD"));
    }

    // brush layer (behind dots)
    const brush = svg("rect", { class: "brush", y: PAD.top, height: height - PAD.top - PAD.bottom, width: 0, visibility: "hidden" });
    const hit = svg("rect", { x: PAD.left, y: PAD.top, width: width - PAD.left - PAD.right,
      height: height - PAD.top - PAD.bottom, fill: "transparent", style: "cursor: crosshair" });
    root.append(hit, brush);
    let start = null;
    const localX = (e) => e.clientX - root.getBoundingClientRect().left;
    hit.addEventListener("pointerdown", (e) => {
      start = localX(e); hit.setPointerCapture(e.pointerId);
      brush.setAttribute("x", start); brush.setAttribute("width", 0); brush.setAttribute("visibility", "visible");
    });
    hit.addEventListener("pointermove", (e) => {
      if (start === null) return;
      const x = localX(e);
      brush.setAttribute("x", Math.min(start, x)); brush.setAttribute("width", Math.abs(x - start));
    });
    hit.addEventListener("pointerup", (e) => {
      if (start === null) return;
      const x = localX(e), a = Math.min(start, x), b = Math.max(start, x);
      start = null; brush.setAttribute("visibility", "hidden");
      if (b - a < 8) return;
      const iso = (t) => new Date(t).toISOString().slice(0, 10);
      this.handlers.onBrush?.(iso(tx(a)), iso(tx(b)));
    });

    // dots, largest first so small ones stay clickable
    const tooltip = el("div", { class: "tooltip", hidden: true });
    const sorted = [...items].sort((a, b) => (b.estimated_diameter_max_m ?? 0) - (a.estimated_diameter_max_m ?? 0));
    for (const o of sorted) {
      const a = o.latest_approach;
      const r = o.estimated_diameter_max_m ? Math.min(12, 3 + Math.sqrt(o.estimated_diameter_max_m) / 6) : 3;
      const dot = svg("circle", {
        class: ["dot", o.is_potentially_hazardous && "pha", o.neo_reference_id === this.hotId && "hot"],
        cx: sx(Date.parse(a.date)), cy: sy(a.miss_distance_ld), r,
        fill: COLORS[o.alert_level] ?? QUIET, dataset: { id: o.neo_reference_id },
        tabindex: 0, role: "button", "aria-label": `${o.name}, ${a.miss_distance_ld.toFixed(2)} lunar distances on ${a.date}`,
      });
      const show = () => {
        clear(tooltip, el("b", {}, o.name), el("br"), `${a.date} · ${a.miss_distance_ld.toFixed(2)} LD · ${a.relative_velocity_km_s.toFixed(1)} km/s`);
        tooltip.hidden = false;
        tooltip.style.left = `${dot.cx.baseVal.value / width * 100}%`;
        tooltip.style.top = `${dot.cy.baseVal.value}px`;
        this.handlers.onHover?.(o.neo_reference_id);
      };
      const hide = () => { tooltip.hidden = true; this.handlers.onHover?.(null); };
      dot.addEventListener("pointerenter", show);
      dot.addEventListener("focus", show);
      dot.addEventListener("pointerleave", hide);
      dot.addEventListener("blur", hide);
      dot.addEventListener("click", () => this.handlers.onSelect?.(o.neo_reference_id));
      dot.addEventListener("keydown", (e) => { if (e.key === "Enter") this.handlers.onSelect?.(o.neo_reference_id); });
      root.append(dot);
    }
    clear(container, root, tooltip);
    this.container.classList.toggle("dimmed", this.dims());
  }
}
