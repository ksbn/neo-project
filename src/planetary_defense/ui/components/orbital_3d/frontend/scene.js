// Orbital viewport. Draws what Python computed (see bridge.py); it does no orbital math itself.
//
// Payload positions are in the J2000 ecliptic frame (x towards the March equinox, z towards the
// ecliptic pole). three.js is y-up, so the ecliptic is drawn as the horizontal plane:
// (x, y, z) -> (x, z, -y).
//
// Earth view: distances from Earth are compressed (logarithmic) so Earth, the Moon's 1 LD ring and
// asteroids tens of LD away fit on one screen; directions are kept. The legend says so.

const payload = JSON.parse(document.getElementById("payload").textContent);
const viewport = document.getElementById("viewport");
const legend = document.getElementById("legend");
const notice = document.getElementById("notice");
const TEXTURES = "__TEXTURE_BASE__";

function showNotice(message) {
  notice.textContent = message;
  notice.hidden = false;
}

let THREE;
let OrbitControls;
try {
  THREE = await import("three");
  ({ OrbitControls } = await import("three/addons/controls/OrbitControls.js"));
} catch (error) {
  showNotice("The 3D view could not load three.js from cdn.jsdelivr.net (offline?). Everything else on the page still works.");
  throw error;
}

const PALETTE = { teal: 0x8ec5ff, ring: 0x9fd0ff, selected: payload.hazardous ? 0xff7a5c : 0xf3c46b };  // ice blue, gold (theme.py)
const CONTEXT_COLORS = [0xf2c94c, 0xf2994a, 0x9b8cff, 0x56ccf2, 0x6fcf97, 0xff7aa2];
const ecl = ([x, y, z]) => new THREE.Vector3(x, z, -y);
const fmt = (value, digits = 2) => Number(value).toFixed(digits);
const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// --------------------------------------------------------------------------- //
// Renderer, camera, controls, background
// --------------------------------------------------------------------------- //

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
} catch (error) {
  showNotice("This browser can't draw WebGL, so the 3D view is unavailable.");
  throw error;
}
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x040a10);
renderer.outputColorSpace = THREE.SRGBColorSpace;
viewport.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 5000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

function resize() {
  const { clientWidth: w, clientHeight: h } = viewport;
  renderer.setSize(w, h, false);
  renderer.domElement.style.width = `${w}px`;
  renderer.domElement.style.height = `${h}px`;
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(viewport);
resize();

function starfield(count = 1800, radius = 900) {
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const v = new THREE.Vector3().randomDirection().multiplyScalar(radius * (0.6 + Math.random() * 0.4));
    positions.set([v.x, v.y, v.z], i * 3);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  return new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0x9fb7c8, size: 1.2, sizeAttenuation: false }));
}
scene.add(starfield());
scene.add(new THREE.AmbientLight(0xffffff, 0.35));
const sunlight = new THREE.DirectionalLight(0xffffff, 2.2);
scene.add(sunlight);

// --------------------------------------------------------------------------- //
// Drawing helpers
// --------------------------------------------------------------------------- //

const loader = new THREE.TextureLoader();
loader.setCrossOrigin("anonymous");
function texture(file, onLoad) {
  loader.load(`${TEXTURES}${file}`, (tex) => { tex.colorSpace = THREE.SRGBColorSpace; onLoad(tex); }, undefined, () => {});
}

function line(points, color, { dashed = false, opacity = 1 } = {}) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = dashed
    ? new THREE.LineDashedMaterial({ color, dashSize: 0.12, gapSize: 0.08, transparent: true, opacity })
    : new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity });
  const object = new THREE.Line(geometry, material);
  if (dashed) object.computeLineDistances();
  return object;
}

function ringPoints(radius, segments = 180) {
  const points = [];
  for (let i = 0; i <= segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    points.push(new THREE.Vector3(radius * Math.cos(a), 0, radius * Math.sin(a)));
  }
  return points;
}

// A glowing tube: a bright core and a soft, additive halo.
function glowTube(points, color, core, halo, haloOpacity) {
  const group = new THREE.Group();
  const curve = new THREE.CatmullRomCurve3(points);
  const segments = Math.min(points.length * 3, 600);
  group.add(new THREE.Mesh(new THREE.TubeGeometry(curve, segments, core, 8, false),
    new THREE.MeshBasicMaterial({ color })));
  group.add(new THREE.Mesh(new THREE.TubeGeometry(curve, segments, halo, 8, false),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: haloOpacity, blending: THREE.AdditiveBlending,
                                  depthWrite: false })));
  return group;
}

function glowRing(radius, color, core, halo) {
  const group = new THREE.Group();
  const flat = (mesh) => { mesh.rotation.x = Math.PI / 2; return mesh; };
  group.add(flat(new THREE.Mesh(new THREE.TorusGeometry(radius, core, 12, 220), new THREE.MeshBasicMaterial({ color }))));
  group.add(flat(new THREE.Mesh(new THREE.TorusGeometry(radius, halo, 12, 220),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.18, blending: THREE.AdditiveBlending,
                                  depthWrite: false }))));
  return group;
}

function rock(radius) {
  return new THREE.Mesh(new THREE.IcosahedronGeometry(radius, 1),
    new THREE.MeshStandardMaterial({ color: 0x8b8580, roughness: 0.95, flatShading: true }));
}

// A text label that stays the same size on screen; `boxed` draws the outlined callout.
function label(text, color = "#dbe7ee", place = "right", boxed = false) {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  const font = "500 28px system-ui, sans-serif";
  ctx.font = font;
  const pad = boxed ? 18 : 8;
  canvas.width = Math.ceil(ctx.measureText(text).width) + pad * 2;
  canvas.height = boxed ? 52 : 40;
  ctx.font = font;
  if (boxed) {
    ctx.fillStyle = "rgba(8, 20, 30, 0.85)";
    ctx.strokeStyle = "rgba(111, 227, 255, 0.55)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(1, 1, canvas.width - 2, canvas.height - 2, 8);
    ctx.fill();
    ctx.stroke();
  }
  ctx.fillStyle = color;
  ctx.textBaseline = "middle";
  ctx.fillText(text, pad, canvas.height / 2);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map, depthTest: false, sizeAttenuation: false }));
  const height = boxed ? 0.042 : 0.032;
  sprite.scale.set((height * canvas.width) / canvas.height, height, 1);
  const centers = { right: [0, 0.5], above: [0.5, -0.6], below: [0.5, 1.6], left: [1, 0.5] };
  sprite.center.set(...centers[place]);
  sprite.renderOrder = 10;
  return sprite;
}

// --------------------------------------------------------------------------- //
// Earth-centred view (compressed lunar distances)
// --------------------------------------------------------------------------- //

const EARTH_R = 1;
const displayRadius = (ld) => EARTH_R + 0.9 * Math.log1p(1.7 * ld);
function toDisplay(p) {
  const v = ecl(p);
  const d = v.length();
  return d === 0 ? v : v.multiplyScalar(displayRadius(d) / d);
}

function buildEarthView(e) {
  const group = new THREE.Group();

  // Earth with clouds and a thin atmosphere glow; plain colours until the textures arrive.
  const earth = new THREE.Mesh(new THREE.SphereGeometry(EARTH_R, 64, 32),
    new THREE.MeshStandardMaterial({ color: 0x2b6cb0, roughness: 0.9 }));
  texture("earth_atmos_2048.jpg", (map) => {
    earth.material.map = map;
    earth.material.color.set(0xffffff);
    earth.material.needsUpdate = true;
  });
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(EARTH_R * 1.012, 64, 32),
    new THREE.MeshStandardMaterial({ transparent: true, opacity: 0, depthWrite: false }));
  texture("earth_clouds_1024.png", (map) => {
    clouds.material.map = map;
    clouds.material.opacity = 0.8;
    clouds.material.needsUpdate = true;
  });
  const atmosphere = new THREE.Mesh(new THREE.SphereGeometry(EARTH_R * 1.08, 48, 24),
    new THREE.MeshBasicMaterial({ color: PALETTE.ring, transparent: true, opacity: 0.12, side: THREE.BackSide,
                                  blending: THREE.AdditiveBlending, depthWrite: false }));
  group.add(earth, clouds, atmosphere);

  // The Moon's distance: a glowing 1 LD ring with the Moon on it (the Moon's real position isn't modelled).
  const moonOrbit = displayRadius(1);
  group.add(glowRing(moonOrbit, PALETTE.ring, 0.018, 0.07));
  const moon = new THREE.Mesh(new THREE.SphereGeometry(0.24, 48, 24),
    new THREE.MeshStandardMaterial({ color: 0xb8b8b8, roughness: 1 }));
  texture("moon_1024.jpg", (map) => {
    moon.material.map = map;
    moon.material.color.set(0xffffff);
    moon.material.needsUpdate = true;
  });
  const moonAngle = -0.35;
  moon.position.set(moonOrbit * Math.cos(moonAngle), 0, moonOrbit * Math.sin(moonAngle));
  group.add(moon);
  const moonLabel = label("Moon", "#e6eef3", "below");
  moonLabel.position.copy(moon.position).add(new THREE.Vector3(0, -0.3, 0));
  group.add(moonLabel);
  const ldLabel = label("1 LUNAR DISTANCE (LD)", "#e6eef3", "right", true);
  ldLabel.position.set(moonOrbit * Math.cos(1.0), 0, moonOrbit * Math.sin(1.0));
  group.add(ldLabel);

  // Faint scale rings so compressed distances can still be read.
  for (const ld of [10, 50]) {
    group.add(line(ringPoints(displayRadius(ld)), 0x2a4a5a, { opacity: 0.6 }));
    const ringLabel = label(`${ld} LD`, "#6f8a99", "right");
    ringLabel.position.set(displayRadius(ld) * Math.cos(2.4), 0, displayRadius(ld) * Math.sin(2.4));
    group.add(ringLabel);
  }

  // Other asteroids with cached orbits: thinner glowing arcs, labelled at their end.
  let extent = moonOrbit + 0.5;
  (payload.context || []).forEach((c, i) => {
    const points = c.path.map(toDisplay);
    extent = Math.max(extent, ...points.map((p) => p.length()));
    const color = CONTEXT_COLORS[i % CONTEXT_COLORS.length];
    group.add(glowTube(points, color, 0.012, 0.04, 0.22));
    const body = rock(0.07);
    body.position.copy(toDisplay(c.closest));
    group.add(body);
    const tag = label(c.name, `#${color.toString(16).padStart(6, "0")}`, "above");
    tag.position.copy(points[points.length - 1]);
    group.add(tag);
  });

  // The tracked asteroid: a brighter, thicker arc, its closest point, and a moving body.
  const path = e.path.map(toDisplay);
  extent = Math.max(extent, ...path.map((p) => p.length()));
  group.add(glowTube(path, PALETTE.selected, 0.025, 0.085, 0.3));
  const closest = toDisplay(e.closest);
  group.add(line([new THREE.Vector3(), closest], 0xffffff, { dashed: true, opacity: 0.7 }));
  const closestMark = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  closestMark.position.copy(closest);
  group.add(closestMark);
  const missLabel = label(`${payload.name} · ${fmt(e.miss_ld)} LD on ${e.approach_date}`, "#ffd2a1", "below");
  missLabel.position.copy(closest);
  group.add(missLabel);
  const body = rock(0.11);
  group.add(body);

  const note = payload.approximate
    ? "No orbit available: straight-line pass at NASA's distance and speed; direction illustrative."
    : `NASA ${fmt(e.miss_ld)} LD · orbit model ${fmt(e.model_miss_ld)} LD.`;
  // Frame Earth, the Moon's ring and the tracked pass; longer context arcs may run off screen.
  const frame = Math.max(moonOrbit + 0.6, closest.length() * 1.05);
  return {
    group,
    radius: frame,
    extent,
    distance: 2.1,
    camera: new THREE.Vector3(0.12, 0.3, 1).normalize(),
    animate(t) {
      earth.rotation.y = t * 0.00004;
      clouds.rotation.y = t * 0.00005;
      body.position.copy(path[Math.floor((t / 9000) * path.length) % path.length]);
    },
    legendHtml: `<b>${escapeHtml(payload.name)}</b> · pass ${e.approach_date} · ${note}
      <span class="muted">Distances from Earth compressed (log scale) for readability; directions are true.
      ${escapeHtml(payload.provenance)}</span>`,
  };
}

// --------------------------------------------------------------------------- //
// Sun-centred orbits (astronomical units)
// --------------------------------------------------------------------------- //

function buildSunView(s) {
  const group = new THREE.Group();
  const asteroidPath = s.asteroid_path.map(ecl);
  const earthPath = s.earth_path.map(ecl);
  const extent = Math.max(...asteroidPath.map((p) => p.length()), ...earthPath.map((p) => p.length()));

  const sun = new THREE.Mesh(new THREE.SphereGeometry(extent * 0.035, 32, 16), new THREE.MeshBasicMaterial({ color: 0xffd166 }));
  const corona = new THREE.Mesh(new THREE.SphereGeometry(extent * 0.07, 32, 16),
    new THREE.MeshBasicMaterial({ color: 0xffb703, transparent: true, opacity: 0.25, blending: THREE.AdditiveBlending,
                                  depthWrite: false }));
  group.add(sun, corona);
  const gridRadius = Math.ceil(extent * 2) / 2;
  group.add(new THREE.PolarGridHelper(gridRadius, 12, gridRadius * 2, 96, 0x173040, 0x173040));
  group.add(glowTube(earthPath, 0x56a8f5, extent * 0.004, extent * 0.012, 0.25));
  group.add(glowTube(asteroidPath, PALETTE.selected, extent * 0.004, extent * 0.012, 0.25));

  const earth = new THREE.Mesh(new THREE.SphereGeometry(extent * 0.02, 32, 16), new THREE.MeshStandardMaterial({ color: 0x3d8bff }));
  earth.position.copy(ecl(s.earth));
  const asteroid = rock(extent * 0.016);
  asteroid.position.copy(ecl(s.asteroid));
  group.add(earth, asteroid);
  const earthLabel = label("Earth", "#9cc7ff", "above");
  earthLabel.position.copy(earth.position);
  const asteroidLabel = label(payload.name, "#ffd2a1", "below");
  asteroidLabel.position.copy(asteroid.position);
  group.add(earthLabel, asteroidLabel);

  return {
    group,
    radius: extent,
    extent,
    distance: 2.35,
    camera: new THREE.Vector3(0.9, 0.75, 1.1).normalize(),
    animate() {},
    legendHtml: `<b>${escapeHtml(payload.name)}</b> · orbit${s.orbit_class ? ` (class ${escapeHtml(s.orbit_class)})` : ""}
      and Earth's, positions on ${s.date} · <span class="muted">Sun-centred, astronomical units, to scale;
      Sun and planets enlarged. ${escapeHtml(payload.provenance)}</span>`,
  };
}

// --------------------------------------------------------------------------- //
// Modes, camera per mode, toolbar, render loop
// --------------------------------------------------------------------------- //

const views = {};
if (payload.earth) views.earth = buildEarthView(payload.earth);
if (payload.sun) views.sun = buildSunView(payload.sun);
for (const view of Object.values(views)) {
  view.group.visible = false;
  scene.add(view.group);
}

const savedCameras = {};
let mode = null;

function fitCamera(view) {
  const radius = Math.max(view.radius, 0.5);
  controls.target.set(0, 0, 0);
  camera.position.copy(view.camera.clone().multiplyScalar(radius * view.distance));
  camera.near = radius / 500;
  camera.far = Math.max(radius, view.extent) * 400;
  camera.updateProjectionMatrix();
  controls.update();
}

function setMode(next) {
  if (!views[next] || next === mode) return;
  if (mode) {
    savedCameras[mode] = { position: camera.position.clone(), target: controls.target.clone() };
    views[mode].group.visible = false;
  }
  mode = next;
  const view = views[mode];
  view.group.visible = true;
  // Sun view: light from the Sun. Earth view: light from the viewer's side so the globe reads clearly
  // (the legend says the Earth view isn't to scale; it doesn't model day and night either).
  sunlight.position.copy(mode === "sun" ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(-4, 3, 8));
  const saved = savedCameras[mode];
  if (saved) {
    camera.position.copy(saved.position);
    controls.target.copy(saved.target);
    controls.update();
  } else {
    fitCamera(view);
  }
  legend.innerHTML = view.legendHtml;
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  }
}

function zoom(factor) {
  const offset = camera.position.clone().sub(controls.target).multiplyScalar(factor);
  camera.position.copy(controls.target.clone().add(offset));
  controls.update();
}

for (const button of document.querySelectorAll("[data-mode]")) {
  button.disabled = !views[button.dataset.mode];
  if (button.disabled) {
    button.title = button.dataset.mode === "earth" ? "No recorded close approach for this asteroid"
                                                   : "No orbit available for this asteroid";
  }
  button.addEventListener("click", () => setMode(button.dataset.mode));
}
document.querySelector("[data-zoom='in']").addEventListener("click", () => zoom(0.8));
document.querySelector("[data-zoom='out']").addEventListener("click", () => zoom(1.25));
document.querySelector("[data-zoom='reset']").addEventListener("click", () => fitCamera(views[mode]));
setMode(payload.default_mode);

renderer.setAnimationLoop((time) => {
  views[mode].animate(time);
  controls.update();
  renderer.render(scene, camera);
});
