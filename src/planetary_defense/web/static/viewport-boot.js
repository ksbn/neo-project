// Boots the existing 3D scene (ui/components/orbital_3d/frontend/scene.js, served as /viewport-scene.js)
// with geometry from the API: GET /api/v1/objects/{id}/trajectory?pass=latest|next (openapi.yaml, ADR 0003).
// The payload is written into the page's JSON tag as text (never parsed as HTML), then the scene runs.

const params = new URLSearchParams(location.search);
const id = params.get("id") ?? "", which = params.get("pass") === "next" ? "next" : "latest";
const notice = document.getElementById("notice");
const say = (text) => { notice.hidden = false; notice.textContent = text; };
if (!/^[0-9]{1,20}$/.test(id)) {
  say("Select an object to see it in 3D.");
} else {
  say("Computing the geometry…");
  const opts = { headers: { Accept: "application/json" } };
  try {
    const res = await fetch(`/api/v1/objects/${id}/trajectory?pass=${which}`, opts);
    const body = await res.json().catch(() => null);
    if (!res.ok) say(body?.message ?? `The API answered ${res.status}.`);
    else if (!body.default_mode) say(which === "next"
      ? "No predicted pass to draw: the orbit is not cached, or NASA lists no future Earth pass."
      : "Nothing to draw: no recorded pass and no orbit for this object.");
    else {
      document.getElementById("payload").textContent = JSON.stringify(body);
      notice.hidden = true;
      await import("./viewport-scene.js");
    }
  } catch (err) {
    say("Cannot reach the API. Is neo-api running?");
  }
}
