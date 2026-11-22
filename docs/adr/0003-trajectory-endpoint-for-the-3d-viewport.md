# ADR 0003: Trajectory endpoint for the 3D Space Viewport

* Status: Accepted
* Date: 2026-11-22
* Contract change: adds `GET /objects/{neo_reference_id}/trajectory` (with `pass=latest|next`) and the
  `Trajectory`, `EarthView`, `SunView` and `Vec3` schemas.

## Context

The new dashboard should have the 3D Space Viewport that the Streamlit Control Room has: an
Earth-centred view of a pass and a Sun-centred view of the orbits, drawn by the existing three.js
scene (`ui/components/orbital_3d/frontend/scene.js`).

The scene needs geometry, not orbital elements: the approach path placed at NASA's miss distance,
both orbits sampled around the Sun, and the bodies' positions on the pass date. That geometry comes
from the domain's orbital math (Kepler solver, Earth's elements, `approach_trajectory`). The
contract has no operation that returns it, and computing it in the browser would duplicate (and
drift from) the tested Python code.

## Decision

* `GET /objects/{id}/trajectory` returns the document the scene already reads (version 1), so the
  scene is reused unchanged and the Streamlit and REST viewports draw the same numbers.
* The orbit is read from the local cache; on a miss it is fetched once from NASA `/neo/{id}`
  (behind a circuit breaker) and cached. Unlike `GET /objects/{id}`, which never calls NASA, this
  operation may, because there is nothing to draw without an orbit.
* Without an orbit the Earth view is an approximate straight-line pass (`approximate: true`) and
  `sun` is `null`; nothing is invented to fill the gap.
* `pass=next` draws NASA/JPL's next predicted Earth pass (from the cached approach list), so
  operators can see an upcoming pass in 3D, not only past ones.
* `404` for an unknown object; `earth` and `sun` are both `null` (with `default_mode: null`) when
  there is no pass and no orbit to draw.

## Consequences

* The dashboard's viewport is an iframe page that loads its data only through this operation.
* The geometry building moves from the Streamlit component into `application/viewport.py`, shared by
  both UIs.
* For passes years away the two-body model drifts from NASA/JPL's n-body prediction (Zoozve 2034:
  model closest approach about 1.8 days later and 16.28 LD instead of 16.80 LD). The closest point is
  still drawn at NASA's distance and labelled with NASA's date; `model_miss_ld` shows the model's figure,
  so the difference is visible rather than hidden.
* A cache miss can take a NASA round trip (about 2 s); later requests for the object are cache hits.
* Additive change: existing clients are unaffected.
