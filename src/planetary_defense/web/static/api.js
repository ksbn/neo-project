// The only way the dashboard talks to the server: the REST API in openapi.yaml.
// Every failure becomes an ApiError carrying the contract's ErrorResponse fields.

export const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, body, headers) {
    super(body?.message ?? `HTTP ${status}`);
    this.status = status;                       // 0 = the API could not be reached
    this.code = body?.code ?? "NETWORK";
    this.details = Array.isArray(body?.details) ? body.details : [];
    this.requestId = body?.request_id ?? headers?.get?.("X-Request-ID") ?? null;
    this.retryAfter = Number(headers?.get?.("Retry-After")) || null;
  }
  /** Field errors keyed by the form field name: "body.tags.1" -> "tags". */
  fieldErrors() {
    const out = {};
    for (const d of this.details) {
      const field = (d.field ?? "").replace(/^body\./, "").split(".")[0];
      if (field && !field.startsWith("header")) (out[field] ??= []).push(d.reason);
    }
    return out;
  }
}

/** fetch() against the API. Resolves { data, status, headers }; rejects with ApiError (or AbortError). */
export async function request(path, { method = "GET", params, body, headers = {}, signal } = {}) {
  const url = new URL(BASE + path, location.origin);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  let res;
  try {
    res = await fetch(url, {
      method, signal,
      headers: { Accept: "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...headers },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, { message: "Cannot reach the API. Is neo-api running?" });
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new ApiError(res.status, data ?? { message: `HTTP ${res.status}` }, res.headers);
  return { data, status: res.status, headers: res.headers };
}

export const api = {
  summary: (opts) => request("/summary", opts),
  objects: (params, opts) => request("/objects", { ...opts, params }),
  object: (id, opts) => request(`/objects/${encodeURIComponent(id)}`, opts),
  watchlist: (params, opts) => request("/watchlist", { ...opts, params }),
  watch: (body, idempotencyKey, opts) =>
    request("/watchlist", { ...opts, method: "POST", body, headers: { "Idempotency-Key": idempotencyKey } }),
};
