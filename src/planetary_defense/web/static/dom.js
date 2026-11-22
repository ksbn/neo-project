// Small DOM helpers. API strings only ever go into textContent, never innerHTML.

const SVG_NS = "http://www.w3.org/2000/svg";
// One number format everywhere (toFixed elsewhere uses "." too), whatever the browser locale.
export const LOCALE = "en-US";

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  return assemble(node, attrs, children);
}

export function svg(tag, attrs = {}, ...children) {
  return assemble(document.createElementNS(SVG_NS, tag), attrs, children);
}

function assemble(node, attrs, children) {
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") node.setAttribute("class", Array.isArray(v) ? v.filter(Boolean).join(" ") : v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function clear(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ------------------------------------------------------------------ formatting
// null means unknown: say so, never print 0.

export function na(text = "N/A") {
  return el("span", { class: "na", title: "Not provided by NASA, or not tracked yet" }, text);
}
export const num = (v, digits = 2, unit = "") =>
  v === null || v === undefined ? na() : `${Number(v).toLocaleString(LOCALE, { maximumFractionDigits: digits, minimumFractionDigits: digits })}${unit}`;
export const metres = (v) => (v === null || v === undefined ? na("Unknown") : `${Math.round(v).toLocaleString(LOCALE)} m`);
export const ld = (v) => (v === null || v === undefined ? na() : `${v < 10 ? v.toFixed(2) : v.toFixed(1)} LD`);

export function relativeDays(isoDate, today = new Date()) {
  const d = new Date(`${isoDate}T00:00:00Z`);
  const t = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  const days = Math.round((d - t) / 86_400_000);
  if (days === 0) return "today";
  const abs = Math.abs(days);
  const span = abs < 60 ? `${abs} d` : abs < 730 ? `${Math.round(abs / 30.44)} mo` : `${(abs / 365.25).toFixed(1)} y`;
  return days > 0 ? `in ${span}` : `${span} ago`;
}

export function timeAgo(iso) {
  if (!iso) return null;
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export const SEVERITIES = ["info", "watch", "warning", "critical"];

export function badge(level) {
  return level ? el("span", { class: `badge sev-${level}` }, level) : el("span", { class: "sev-none", title: "No alert" }, "—");
}
