// 3D Space Viewport + Orbital Telemetry for the tracked object.
//
// The 3D scene runs in an iframe (/viewport.html), which loads its geometry from
// GET /objects/{id}/trajectory?pass=latest|next; the telemetry card reads GET /objects/{id}.
// Unknown values are shown as unknown, never as 0.

import { api } from "./api.js";
import { badge, clear, el, ld, metres, na, num, relativeDays } from "./dom.js";

const UNTRACKED = () => na("N/A (untracked)");

export class Viewport {
  constructor({ frame, title, telemetry, passGroup, inspectButton, onInspect, onTrack }) {
    Object.assign(this, { frame, title, telemetry, passGroup, onInspect, onTrack });
    this.id = null;
    this.pass = "latest";
    this.details = new Map();
    passGroup.addEventListener("change", (e) => { this.pass = e.target.value; this.load(); });
    inspectButton.addEventListener("click", () => this.id && this.onInspect(this.id));
  }

  /** Show ``id`` (no-op if it is already shown). */
  track(id) {
    if (!id || id === this.id) return;
    this.id = id;
    this.onTrack?.(id);
    this.load();
  }

  load() {
    // A new src replaces the whole scene; the browser keeps three.js and the textures cached.
    this.frame.src = `viewport.html?id=${encodeURIComponent(this.id)}&pass=${this.pass}`;
    this.showTelemetry(this.id);
  }

  async showTelemetry(id) {
    if (!this.details.has(id)) clear(this.telemetry, el("div", { class: "skeleton", style: { height: "300px" } }));
    try {
      const d = this.details.get(id) ?? (await api.object(id)).data;
      this.details.set(id, d);
      if (id === this.id) this.renderTelemetry(d);
    } catch (err) {
      if (id !== this.id) return;
      clear(this.title, `#${id}`);
      clear(this.telemetry, el("div", { class: ["state", "error"] }, el("div", {}, err.message),
        el("button", { class: "btn", type: "button", onclick: () => this.showTelemetry(id) }, "Retry")));
    }
  }

  forget(id) { this.details.delete(id); if (id === this.id) this.showTelemetry(id); }

  renderTelemetry(d) {
    const o = d.orbit, a = d.latest_approach, next = d.next_predicted_approach;
    clear(this.title, d.name, d.watched ? el("span", { class: "star", title: "On the watchlist" }, " ★") : null);
    const rows = [
      ["Object ID", d.neo_reference_id],
      ["Alert", badge(d.alert_level)],
      ["Latest pass", a ? `${a.date} · ${ld(a.miss_distance_ld)}` : na("none recorded")],
      ["Velocity", a ? `${a.relative_velocity_km_s.toFixed(2)} km/s` : na()],
      ["Diameter (est.)", d.estimated_diameter_max_m === null ? na("Unknown")
        : `${metres(d.estimated_diameter_min_m)} – ${metres(d.estimated_diameter_max_m)}`],
      ["Absolute magnitude", num(d.absolute_magnitude_h, 2)],
      ["Next predicted pass", next ? `${next.date} · ${ld(next.miss_distance_ld)} (${relativeDays(next.date)})`
        : o.tracked ? na("none listed to 2200") : UNTRACKED()],
      ["Orbit class", o.tracked ? (o.orbit_class ?? na()) : UNTRACKED()],
      ["Semi-major axis", o.tracked ? num(o.semi_major_axis_au, 4, " AU") : UNTRACKED()],
      ["Eccentricity", o.tracked ? num(o.eccentricity, 4) : UNTRACKED()],
      ["Inclination", o.tracked ? num(o.inclination_deg, 3, "°") : UNTRACKED()],
      ["Earth MOID", o.tracked ? (o.moid_ld === null ? na() : `${o.moid_ld.toFixed(2)} LD`) : UNTRACKED()],
    ];
    clear(this.telemetry,
      el("div", {}, d.is_potentially_hazardous ? el("span", { class: "pha-tag" }, "POTENTIALLY HAZARDOUS") : null),
      el("dl", { class: "kv" }, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      o.tracked ? null : el("p", { class: "callout" }, "No cached orbit: the viewport shows an approximate straight-line pass. ",
        "Opening it asks NASA once and caches the orbit."));
  }
}
