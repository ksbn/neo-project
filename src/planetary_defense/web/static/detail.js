// Detail drawer: every attribute of GET /objects/{id}. Nulls are shown as unknown, never as 0.

import { api } from "./api.js";
import { LOCALE, badge, clear, el, ld, metres, na, num, relativeDays, svg, timeAgo } from "./dom.js";

const UNTRACKED = () => na("N/A (untracked)");

function kv(rows) {
  return el("dl", { class: "kv" }, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}

function approachRows(a) {
  return a
    ? [["Date (UTC)", `${a.date} · ${relativeDays(a.date)}`], ["Miss distance", `${ld(a.miss_distance_ld)} · ${Math.round(a.miss_distance_km).toLocaleString(LOCALE)} km`],
       ["Relative velocity", `${a.relative_velocity_km_s.toFixed(2)} km/s`], ["Body", a.orbiting_body]]
    : [["Pass", na("None recorded")]];
}

/** Top-down sketch of the orbit around the Sun with Earth's orbit for scale (inclination not shown). */
function orbitSketch(o) {
  const { semi_major_axis_au: a, eccentricity: e, ascending_node_deg: node, perihelion_argument_deg: peri } = o;
  if ([a, e, node, peri].some((v) => v === null)) return null;
  const R = Math.max(a * (1 + e), 1) * 1.18, b = a * Math.sqrt(1 - e * e), theta = (node + peri) % 360;
  return svg("svg", { class: "orbit-svg", viewBox: `${-R} ${-R} ${2 * R} ${2 * R}`, role: "img",
    "aria-label": `Orbit sketch: semi-major axis ${a.toFixed(2)} AU, eccentricity ${e.toFixed(2)}` },
    svg("circle", { cx: 0, cy: 0, r: 1, fill: "none", stroke: "#56a8f5", "stroke-dasharray": "0.03 0.03",
      "stroke-width": R / 120 }),
    svg("ellipse", { cx: -a * e, cy: 0, rx: a, ry: b, fill: "none", stroke: "#22d3c5", "stroke-width": R / 70,
      transform: `rotate(${-theta})` }),
    svg("circle", { cx: 0, cy: 0, r: R / 28, fill: "#f2c94c" }),
    svg("text", { x: 0, y: -1.06, "text-anchor": "middle", fill: "#56a8f5", "font-size": R / 12 }, "Earth's orbit"),
  );
}

export class DetailDrawer {
  constructor(drawer, scrim, { onWatch, onClose }) {
    Object.assign(this, { drawer, scrim, onWatch, onClose });
    this.controller = null;
    this.currentId = null;
    scrim.addEventListener("click", () => this.close());
  }

  get isOpen() { return this.drawer.classList.contains("open"); }

  async open(id) {
    this.currentId = id;
    this.controller?.abort();
    this.controller = new AbortController();
    this.drawer.classList.add("open");
    this.drawer.setAttribute("aria-hidden", "false");
    this.scrim.hidden = false;
    clear(this.drawer, this.closeButton(), el("div", { class: "skeleton", style: { height: "28px", width: "70%", marginTop: "28px" } }),
      el("div", { class: "skeleton", style: { height: "160px", marginTop: "18px" } }),
      el("div", { class: "skeleton", style: { height: "120px", marginTop: "18px" } }));
    this.drawer.focus();
    try {
      const { data } = await api.object(id, { signal: this.controller.signal });
      this.render(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      const notFound = err.status === 404;
      clear(this.drawer, this.closeButton(), el("div", { class: ["state", "error"] },
        el("div", { class: "icon" }, notFound ? "∅" : "⚠"),
        el("div", {}, err.message),
        err.requestId && el("div", { class: "rid" }, `request ${err.requestId}`),
        !notFound && el("button", { class: "btn", type: "button", onclick: () => this.open(id) }, "Retry")));
    }
  }

  refresh() { if (this.isOpen && this.currentId) this.open(this.currentId); }

  close() {
    this.controller?.abort();
    this.drawer.classList.remove("open");
    this.drawer.setAttribute("aria-hidden", "true");
    this.scrim.hidden = true;
    this.currentId = null;
    this.onClose?.();
  }

  closeButton() {
    return el("button", { class: ["btn", "ghost", "small", "drawer-close"], type: "button", onclick: () => this.close(),
      "aria-label": "Close detail" }, "✕ Esc");
  }

  render(d) {
    const o = d.orbit;
    const orbitBlock = o.tracked
      ? [orbitSketch(o), kv([
          ["Orbit class", o.orbit_class ?? na()], ["Semi-major axis", num(o.semi_major_axis_au, 4, " AU")],
          ["Eccentricity", num(o.eccentricity, 4)], ["Inclination", num(o.inclination_deg, 3, "°")],
          ["Ascending node", num(o.ascending_node_deg, 2, "°")], ["Argument of perihelion", num(o.perihelion_argument_deg, 2, "°")],
          ["Mean anomaly", num(o.mean_anomaly_deg, 2, "°")], ["Epoch (JD)", num(o.epoch_jd, 1)],
          ["Earth MOID", o.moid_ld === null ? na() : `${o.moid_ld.toFixed(2)} LD (${o.moid_au.toFixed(5)} AU)`],
          ["Fetched", `${timeAgo(o.fetched_at)}`],
        ])]
      : [el("p", { class: "callout" }, "This object's orbit is not in the local cache yet, so every orbital element is unknown. ",
          "It is cached when the pipeline next runs over its dates."),
         kv([["Semi-major axis", UNTRACKED()], ["Eccentricity", UNTRACKED()], ["Inclination", UNTRACKED()], ["Earth MOID", UNTRACKED()]])];

    const watch = d.watch
      ? el("div", { class: ["banner", "ok"] }, "★ Watched · ", el("span", { class: `prio prio-${d.watch.priority}` }, d.watch.priority),
          d.watch.alert_threshold_ld !== null ? ` · flag within ${d.watch.alert_threshold_ld} LD` : " · no threshold",
          d.watch.note ? el("div", { class: "muted" }, `“${d.watch.note}”`) : null,
          d.watch.tags.length ? el("div", {}, d.watch.tags.map((t) => el("span", { class: "tag" }, t))) : null)
      : el("button", { class: ["btn", "primary"], type: "button", onclick: () => this.onWatch(d) }, "★ Add to watchlist  (w)");

    clear(this.drawer,
      this.closeButton(),
      el("div", { class: "muted mono" }, `#${d.neo_reference_id}`),
      el("h2", { class: "name" }, d.name),
      el("div", {}, badge(d.alert_level), d.is_potentially_hazardous ? el("span", { class: "pha-tag" }, "POTENTIALLY HAZARDOUS") : null,
        d.is_sentry_object ? el("span", { class: "pha-tag" }, "SENTRY") : null),
      el("section", {}, watch),
      el("section", {}, el("h3", {}, "Latest recorded pass"), kv(approachRows(d.latest_approach))),
      el("section", {}, el("h3", {}, "Next predicted Earth pass (NASA/JPL)"),
        d.next_predicted_approach ? kv(approachRows(d.next_predicted_approach))
          : kv([["Pass", o.tracked ? na("None listed up to 2200") : UNTRACKED()]])),
      el("section", {}, el("h3", {}, "Physical"), kv([
        ["Diameter (est.)", d.estimated_diameter_max_m === null ? na("Unknown")
          : `${metres(d.estimated_diameter_min_m)} – ${metres(d.estimated_diameter_max_m)}`],
        ["Absolute magnitude H", num(d.absolute_magnitude_h, 2)],
      ])),
      el("section", {}, el("h3", {}, "Orbit"), orbitBlock),
      el("section", {}, el("h3", {}, `Alerts (${d.alerts.length})`),
        d.alerts.length ? el("ul", { class: "alerts-list" }, d.alerts.map((a) =>
          el("li", {}, badge(a.severity), el("span", {}, a.message, el("span", { class: "muted" }, ` · ${a.approach_date}`)))))
          : el("p", { class: "muted" }, "No rule fired for this object.")),
      el("section", {}, el("h3", {}, `Recorded passes (${d.approach_history.length})`),
        el("table", { class: "history" }, d.approach_history.map((a) =>
          el("tr", {}, el("td", {}, a.date), el("td", {}, `${a.miss_distance_ld.toFixed(2)} LD`), el("td", {}, `${a.relative_velocity_km_s.toFixed(1)} km/s`))))),
      d.nasa_jpl_url ? el("section", {}, el("a", { href: d.nasa_jpl_url, target: "_blank", rel: "noopener", class: "linkish" }, "Open in NASA JPL ↗")) : null,
    );
  }
}
