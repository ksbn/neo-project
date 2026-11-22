// "Add to watchlist" form, wired to POST /watchlist.
//
// The API is the validator: the form sends what the operator typed (novalidate) and shows the
// contract's field errors under each field. One Idempotency-Key per attempt: automatic retries
// after a network failure or a 503 reuse it, so a retry can never create a second entry.

import { api } from "./api.js";
import { clear, el } from "./dom.js";

const FIELDS = ["neo_reference_id", "priority", "alert_threshold_ld", "note", "tags"];
const PRIORITIES = ["low", "medium", "high", "critical"];
const RETRY_DELAYS_MS = [800, 2000];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(36).slice(2)}`);

export class WatchForm {
  constructor(dialog, { onCreated, onOpenObject, toast }) {
    Object.assign(this, { dialog, onCreated, onOpenObject, toast });
  }

  open({ neoId = "", name = "" } = {}) {
    this.key = newKey();
    this.tags = [];
    this.render(neoId, name);
    this.dialog.showModal();
    (neoId ? this.dialog.querySelector("input[name=priority]:checked") : this.inputs.neo)?.focus();
  }

  render(neoId, name) {
    const field = (key, label, control, extra) => {
      const box = el("div", { class: "field", dataset: { field: key } }, el("div", { class: "label" }, label), control, extra,
        el("p", { class: "field-error", id: `err-${key}`, hidden: true }));
      return box;
    };
    this.inputs = {
      neo: el("input", { type: "text", name: "neo_reference_id", value: neoId, inputmode: "numeric", autocomplete: "off",
        "aria-describedby": "err-neo_reference_id", placeholder: "e.g. 2524522" }),
      thresholdOn: el("input", { type: "checkbox", id: "thr-on", checked: true }),
      thresholdRange: el("input", { type: "range", min: 0.5, max: 100, step: 0.5, value: 10, "aria-label": "Alert threshold" }),
      thresholdNum: el("input", { type: "number", step: "any", value: 10, "aria-label": "Alert threshold in lunar distances",
        "aria-describedby": "err-alert_threshold_ld" }),
      note: el("textarea", { name: "note", "aria-describedby": "err-note", placeholder: "What should the next operator know?" }),
      tag: el("input", { type: "text", placeholder: "add a tag, press Enter", "aria-describedby": "err-tags" }),
    };
    const { thresholdOn, thresholdRange, thresholdNum, note, tag } = this.inputs;
    thresholdRange.addEventListener("input", () => { thresholdNum.value = thresholdRange.value; });
    thresholdNum.addEventListener("input", () => { thresholdRange.value = thresholdNum.value; });
    thresholdOn.addEventListener("change", () => { thresholdRange.disabled = thresholdNum.disabled = !thresholdOn.checked; });
    const counter = el("div", { class: "counter" }, "0 / 500");
    note.addEventListener("input", () => {
      counter.textContent = `${note.value.length} / 500`;
      counter.style.color = note.value.length > 500 ? "var(--critical)" : "";
    });
    this.tagBox = el("div", { class: "tags-input" }, tag);
    tag.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === ",") && tag.value.trim()) {
        e.preventDefault(); this.tags.push(tag.value.trim()); tag.value = ""; this.drawTags();
      } else if (e.key === "Backspace" && !tag.value && this.tags.length) { this.tags.pop(); this.drawTags(); }
    });

    this.banner = el("div", { role: "alert" });
    this.log = el("pre", { class: "idem-log", hidden: true });
    this.submitBtn = el("button", { class: ["btn", "primary"], type: "submit" }, "★ Watch");

    const form = el("form", { class: "form", novalidate: true, onsubmit: (e) => { e.preventDefault(); this.submit(); } },
      field("neo_reference_id", "Object ID", this.inputs.neo, name ? el("div", { class: "muted" }, name) : null),
      field("priority", "Priority", el("div", { class: "segmented", role: "radiogroup", "aria-describedby": "err-priority" },
        PRIORITIES.map((p) => el("label", {}, el("input", { type: "radio", name: "priority", value: p, checked: p === "medium" }), p)))),
      field("alert_threshold_ld", "Flag passes closer than (lunar distances)",
        el("div", { class: "threshold" }, thresholdOn, thresholdRange, thresholdNum)),
      field("note", "Note", note, counter),
      field("tags", "Tags (lower-case, up to 5)", this.tagBox),
      this.banner,
      el("div", { class: "actions" },
        el("button", { class: ["linkish"], type: "button", title: "Sends the same request twice at once with one Idempotency-Key",
          onclick: () => this.doubleSubmit() }, "Test: send twice"),
        el("div", {}, el("button", { class: ["btn", "ghost"], type: "button", onclick: () => this.dialog.close() }, "Cancel"),
          " ", this.submitBtn)),
      this.log,
    );
    clear(this.dialog, el("h2", { id: "watch-dialog-title" }, "Add to watchlist"), form);
  }

  drawTags() {
    clear(this.tagBox, this.tags.map((t, i) => el("span", { class: "chip" }, t,
      el("button", { type: "button", "aria-label": `Remove tag ${t}`, onclick: () => { this.tags.splice(i, 1); this.drawTags(); } }, "×"))),
      this.inputs.tag);
    this.inputs.tag.focus();
  }

  /** Exactly what the operator entered; validation is the API's job. */
  body() {
    const { neo, thresholdOn, thresholdNum, note } = this.inputs;
    const threshold = thresholdNum.value === "" ? NaN : Number(thresholdNum.value);
    return {
      neo_reference_id: neo.value.trim(),
      priority: this.dialog.querySelector("input[name=priority]:checked")?.value,
      alert_threshold_ld: thresholdOn.checked ? (Number.isNaN(threshold) ? thresholdNum.value : threshold) : null,
      note: note.value === "" ? null : note.value,
      tags: [...this.tags],
    };
  }

  showErrors(err) {
    const byField = err?.fieldErrors?.() ?? {};
    for (const name of FIELDS) {
      const box = this.dialog.querySelector(`[data-field=${name}]`), msg = this.dialog.querySelector(`#err-${name}`);
      const reasons = byField[name];
      box.classList.toggle("invalid", Boolean(reasons));
      msg.hidden = !reasons;
      msg.textContent = reasons ? reasons.join(" · ") : "";
    }
    const stray = Object.keys(byField).filter((f) => !FIELDS.includes(f));
    if (stray.length) byField._stray = stray;
    return byField;
  }

  setBanner(kind, ...content) { clear(this.banner, kind ? el("div", { class: ["banner", kind] }, ...content) : null); }

  logLine(text) { this.log.hidden = false; this.log.textContent += `${text}\n`; }

  async send(body) {
    for (let attempt = 0; ; attempt++) {
      try {
        const res = await api.watch(body, this.key);
        this.logLine(`→ ${res.status} ${res.headers.get("Idempotent-Replayed") === "true" ? "replayed" : "created"}  key ${this.key.slice(0, 8)}…`);
        return res;
      } catch (err) {
        const transient = err.status === 0 || err.status === 503;
        this.logLine(`→ ${err.status || "network error"} ${err.code}${transient && attempt < RETRY_DELAYS_MS.length ? " · retrying with the same key" : ""}`);
        if (!transient || attempt >= RETRY_DELAYS_MS.length) throw err;
        await sleep(RETRY_DELAYS_MS[attempt]);
      }
    }
  }

  async submit() {
    this.submitBtn.disabled = true;
    this.showErrors(null);
    this.setBanner(null);
    try {
      const res = await this.send(this.body());
      this.created(res);
    } catch (err) {
      this.failed(err);
    } finally {
      this.submitBtn.disabled = false;
    }
  }

  async doubleSubmit() {
    this.showErrors(null);
    this.setBanner("info", "Sending the same request twice, at the same time, with one Idempotency-Key…");
    const body = this.body();
    const results = await Promise.allSettled([this.send(body), this.send(body)]);
    const ok = results.filter((r) => r.status === "fulfilled");
    if (ok.length === 2) {
      const replayed = ok.filter((r) => r.value.headers.get("Idempotent-Replayed") === "true").length;
      const same = ok[0].value.data.id === ok[1].value.data.id;
      this.created(ok[0].value, `Both answered 201; ${replayed} was a replay; ${same ? "same entry id: one entry created" : "different ids!"}.`);
    } else {
      this.failed(results.find((r) => r.status === "rejected").reason);
    }
  }

  created(res, extra) {
    const entry = res.data;
    this.setBanner("ok", `★ Now watching ${entry.name}.`, extra ? el("div", {}, extra) : null);
    this.toast(`★ Watching ${entry.name}${res.headers.get("Idempotent-Replayed") === "true" ? " (replayed, no duplicate)" : ""}`);
    this.key = newKey();  // the next submission is a new request
    this.onCreated(entry);
    setTimeout(() => this.dialog.open && this.dialog.close(), extra ? 2600 : 900);
  }

  failed(err) {
    const fields = this.showErrors(err);
    const rid = err.requestId ? el("div", { class: "muted mono" }, `request ${err.requestId}`) : null;
    switch (err.code) {
      case "VALIDATION_FAILED":
        this.setBanner("error", "Please fix the highlighted fields.",
          fields._stray ? el("div", {}, `Unexpected fields: ${fields._stray.join(", ")}`) : null, rid);
        this.dialog.querySelector(".field.invalid input, .field.invalid textarea")?.focus();
        break;
      case "UNKNOWN_OBJECT":
        this.setBanner("error", err.message, rid);
        break;
      case "ALREADY_WATCHED": {
        const id = this.inputs.neo.value.trim();
        this.setBanner("error", err.message, " ",
          el("button", { class: "linkish", type: "button", onclick: () => { this.dialog.close(); this.onOpenObject(id); } }, "Open it"), rid);
        break;
      }
      case "IDEMPOTENCY_KEY_REUSED":
        this.key = newKey();
        this.setBanner("error", "That retry key was already used for a different request; a new one was issued. Submit again.", rid);
        break;
      case "RATE_LIMITED":
        this.setBanner("error", `${err.message}`, rid);
        break;
      default:
        this.setBanner("error", err.message, el("div", {}, "Nothing was saved. You can safely submit again: the retry uses the same key."), rid);
    }
  }
}
