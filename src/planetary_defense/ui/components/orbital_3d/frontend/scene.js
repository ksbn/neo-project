// Orbital viewport. Draws what Python computed (see bridge.py); it does no orbital math itself.
//
// Payload positions are in the J2000 ecliptic frame (x towards the March equinox, z towards the
// ecliptic pole). three.js is y-up, so the ecliptic is drawn as the horizontal plane:
// (x, y, z) -> (x, z, -y).

const payload = JSON.parse(document.getElementById("payload").textContent);
const viewport = document.getElementById("viewport");
const legend = document.getElementById("legend");
const notice = document.getElementById("notice");

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

const COLORS = {
  earth: 0x3d8bff,
  moonRing: 0x9aa4b5,
  scaleRing: 0x2b3446,
  sun: 0xffd166,
  asteroid: payload.hazardous ? 0xff5a4f : 0xffb347,
  closest: 0xffffff,
};

const ecl = ([x, y, z]) => new THREE.Vector3(x, z, -y);

// --------------------------------------------------------------------------- //
// Renderer, camera, controls
// --------------------------------------------------------------------------- //

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
} catch (error) {
  showNotice("This browser can't draw WebGL, so the 3D view is unavailable.");
  throw error;
}
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x05070d);
viewport.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, 0.001, 1e6);
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

// --------------------------------------------------------------------------- //
// Drawing helpers
// --------------------------------------------------------------------------- //

function line(points, color, { dashed = false, opacity = 1 } = {}) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = dashed
    ? new THREE.LineDashedMaterial({ color, dashSize: 0.6, gapSize: 0.4, transparent: true, opacity })
    : new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity });
  const object = new THREE.Line(geometry, material);
  if (dashed) object.computeLineDistances();
  return object;
}

function ring(radius, color, opacity = 1, segments = 160) {
  const points = [];
  for (let i = 0; i <= segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    points.push(new THREE.Vector3(radius * Math.cos(a), 0, radius * Math.sin(a)));
  }
  return line(points, color, { opacity });
}

function sphere(radius, color, emissive = false) {
  const material = emissive
    ? new THREE.MeshBasicMaterial({ color })
    : new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.1 });
  return new THREE.Mesh(new THREE.SphereGeometry(radius, 32, 16), material);
}

// A text label that stays the same size on screen. `place` puts it beside, above or below its
// anchor in screen space, so labels of nearby points don't overlap however the view is turned.
function label(text, color = "#d7dde8", place = "right") {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  const font = "500 28px system-ui, sans-serif";
  ctx.font = font;
  canvas.width = Math.ceil(ctx.measureText(text).width) + 16;
  canvas.height = 40;
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textBaseline = "middle";
  ctx.fillText(text, 8, 20);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, sizeAttenuation: false }));
  const height = 0.035;
  sprite.scale.set((height * canvas.width) / canvas.height, height, 1);
  const centers = { right: [0, 0.5], above: [0.5, -0.6], below: [0.5, 1.6], left: [1, 0.5] };
  sprite.center.set(...centers[place]);
  sprite.renderOrder = 10;
  return sprite;
}

function extentOf(points) {
  return points.reduce((max, p) => Math.max(max, p.length()), 0);
}

const fmt = (value, digits = 2) => Number(value).toFixed(digits);
const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// --------------------------------------------------------------------------- //
// Earth-centred close approach (lunar distances)
// --------------------------------------------------------------------------- //

function buildEarthView(e) {
  const group = new THREE.Group();
  const path = e.path.map(ecl);
  const closest = ecl(e.closest);
  const extent = Math.max(extentOf(path), 1.5);

  // Earth is ~0.017 LD across; enlarge it so it stays visible, and say by how much.
  const earthRadius = Math.max(extent * 0.02, 0.12);
  group.add(sphere(earthRadius, COLORS.earth));
  const earthLabel = label("Earth (enlarged)", "#9cc7ff", "above");
  earthLabel.position.set(0, earthRadius, 0);
  group.add(earthLabel);

  group.add(ring(1, COLORS.moonRing, 0.9));
  const moonLabel = label("1 LD (Moon)", "#b8c1d1", "left");
  moonLabel.position.set(-1.05, 0, 0);
  group.add(moonLabel);

  for (const r of [5, 10, 20, 50, 100, 200]) {
    if (r > 1.5 && r < extent * 1.25) {
      group.add(ring(r, COLORS.scaleRing, 0.9));
      const ringLabel = label(`${r} LD`, "#5d687d");
      ringLabel.position.set(r * Math.SQRT1_2, 0, r * Math.SQRT1_2);
      group.add(ringLabel);
    }
  }

  group.add(line(path, COLORS.asteroid));
  const end = path[path.length - 1];
  const before = path[Math.max(path.length - 4, 0)];
  const direction = end.clone().sub(before).normalize();
  group.add(new THREE.ArrowHelper(direction, end, extent * 0.08, COLORS.asteroid, extent * 0.04, extent * 0.025));

  group.add(line([new THREE.Vector3(), closest], COLORS.closest, { dashed: true, opacity: 0.8 }));
  const marker = sphere(Math.max(extent * 0.008, 0.05), COLORS.closest, true);
  marker.position.copy(closest);
  group.add(marker);
  const missLabel = label(`${fmt(e.miss_ld)} LD on ${e.approach_date}`, "#ffffff", "below");
  missLabel.position.copy(closest);
  group.add(missLabel);

  // A body moving along the drawn path, so the direction of motion is obvious.
  const body = sphere(Math.max(extent * 0.012, 0.07), COLORS.asteroid, true);
  group.add(body);

  const enlargement = Math.round(earthRadius / e.earth_radius_ld);
  return {
    group,
    radius: extent,
    animate(t) {
      const i = Math.floor((t / 8000) * path.length) % path.length;
      body.position.copy(path[i]);
    },
    legendHtml: `
      <h3>${escapeHtml(payload.name)}</h3>
      <p><span class="swatch" style="background:#${COLORS.asteroid.toString(16)}"></span>
         Path ${e.window_days} days either side of the pass on ${e.approach_date}</p>
      <p>Closest approach drawn at NASA's ${fmt(e.miss_ld)} LD
         ${payload.approximate ? "" : `<span class="muted">(orbit model alone: ${fmt(e.model_miss_ld)} LD)</span>`}</p>
      ${payload.approximate ? `<p style="color:#f5c542">No orbit available: a straight-line pass at NASA's distance
         and speed. The direction is illustrative.</p>` : ""}
      <p class="muted">Earth-centred, J2000 ecliptic plane, lunar distances (1 LD = 1/389 AU).
         Earth enlarged about ${enlargement}x; the path is to scale and approximate.</p>
      <p class="muted">${escapeHtml(payload.provenance)}</p>`,
  };
}

// --------------------------------------------------------------------------- //
// Sun-centred orbits (astronomical units)
// --------------------------------------------------------------------------- //

function buildSunView(s) {
  const group = new THREE.Group();
  const asteroidPath = s.asteroid_path.map(ecl);
  const earthPath = s.earth_path.map(ecl);
  const extent = Math.max(extentOf(asteroidPath), extentOf(earthPath));

  group.add(sphere(extent * 0.03, COLORS.sun, true));
  const gridRadius = Math.ceil(extent * 2) / 2; // next 0.5 AU
  group.add(new THREE.PolarGridHelper(gridRadius, 12, gridRadius * 2, 96, 0x1c2433, 0x1c2433));

  group.add(line(earthPath, COLORS.earth));
  group.add(line(asteroidPath, COLORS.asteroid));

  const earth = sphere(extent * 0.018, COLORS.earth);
  earth.position.copy(ecl(s.earth));
  group.add(earth);
  const earthLabel = label("Earth", "#9cc7ff", "above");
  earthLabel.position.copy(earth.position);
  group.add(earthLabel);

  const asteroid = sphere(extent * 0.014, COLORS.asteroid, true);
  asteroid.position.copy(ecl(s.asteroid));
  group.add(asteroid);
  const asteroidLabel = label(payload.name, "#ffd2a1", "below");
  asteroidLabel.position.copy(asteroid.position);
  group.add(asteroidLabel);

  return {
    group,
    radius: extent,
    animate() {},
    legendHtml: `
      <h3>${escapeHtml(payload.name)}</h3>
      <p><span class="swatch" style="background:#${COLORS.asteroid.toString(16)}"></span>Orbit
         ${s.orbit_class ? `(class ${escapeHtml(s.orbit_class)})` : ""}
         <span class="swatch" style="background:#3d8bff;margin-left:8px"></span>Earth's orbit</p>
      <p>Positions on ${s.date}</p>
      <p class="muted">Sun-centred, J2000 ecliptic, astronomical units. Sun and planets enlarged.</p>
      <p class="muted">${escapeHtml(payload.provenance)}</p>`,
  };
}

// --------------------------------------------------------------------------- //
// Modes, camera per mode, render loop
// --------------------------------------------------------------------------- //

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const sunlight = new THREE.PointLight(0xffffff, 2.5, 0, 0);
scene.add(sunlight);

const views = {};
if (payload.earth) views.earth = buildEarthView(payload.earth);
if (payload.sun) views.sun = buildSunView(payload.sun);
for (const view of Object.values(views)) {
  view.group.visible = false;
  scene.add(view.group);
}

const savedCameras = {};
let mode = null;

// Frame the drawn content (paths, orbits), not helpers such as the grid.
function fitCamera(view) {
  const radius = Math.max(view.radius, 0.5);
  const direction = new THREE.Vector3(0.9, 0.75, 1.1).normalize();
  controls.target.set(0, 0, 0);
  // A 45 degree field of view needs ~2.6 radii to show a sphere; 3 leaves a margin for labels.
  camera.position.copy(direction.multiplyScalar(radius * 3));
  camera.near = radius / 1000;
  camera.far = radius * 100;
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
  // Light from the Sun's direction: the origin in the Sun view, roughly "behind" Earth in the Earth view.
  const awayFromSun = payload.sun ? ecl(payload.sun.earth).negate().normalize() : new THREE.Vector3(1, 0.4, 0.6);
  sunlight.position.copy(mode === "sun" ? new THREE.Vector3() : awayFromSun.multiplyScalar(1e4));
  const saved = savedCameras[mode];
  if (saved) {
    camera.position.copy(saved.position);
    controls.target.copy(saved.target);
    controls.update();
  } else {
    fitCamera(view);
  }
  legend.innerHTML = view.legendHtml;
  for (const button of document.querySelectorAll("#toolbar button")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  }
}

for (const button of document.querySelectorAll("#toolbar button")) {
  button.disabled = !views[button.dataset.mode];
  if (button.disabled) {
    button.title = button.dataset.mode === "earth" ? "No recorded close approach for this asteroid"
                                                   : "No orbit available for this asteroid";
  }
  button.addEventListener("click", () => setMode(button.dataset.mode));
}
setMode(payload.default_mode);

renderer.setAnimationLoop((time) => {
  views[mode].animate(time);
  controls.update();
  renderer.render(scene, camera);
});
