// One fixture's 3D view: its model mesh, slightly transparent, with every LED
// drawn as a point at its marker's true position and coloured live from
// room_frame channels. Display only. Positions come from the server's
// layout route (control/model_layout.py is the one marker parser); the .glb
// is loaded only for the visible mesh.
//
// Units: the layout is in mm, Z up (x_mm = x * 1000, y_mm = -z * 1000,
// z_mm = y * 1000 from the glTF's metres, Y up). The scene is mm 1:1 in
// three's own Y-up frame, so the mesh is scaled by 1000 and an LED sits at
// (x_mm, z_mm, -y_mm), which puts it back on its marker.
//
// A failure rejects with an Error whose message is the tile's fallback
// reason; the caller shows the flat strip with that line.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { decodeRgbw } from "./pixels.js";

const SIZE_PX = { small: 6, medium: 9, large: 12 };
const UNLIT_SUM = 12;           // r+g+b+w below this is unlit, as on the strip
const RING_PX = 8;              // extra diameter for the selected LED's ring
const MESH_OPACITY = 0.45;
const PICK_SLOP_PX = 6;
const CLICK_MOVE_PX = 4;        // a drag further than this orbits, not picks
const FALLBACK_W = 300;
const FALLBACK_H = 260;

const VERTEX = `
uniform float uScale;
attribute vec3 aColor;
attribute float aSize;
attribute float aLit;
attribute float aSel;
varying vec3 vColor;
varying float vLit;
varying float vSel;
varying float vSize;
void main() {
  vColor = aColor;
  vLit = aLit;
  vSel = aSel;
  vSize = aSize + aSel * ${RING_PX.toFixed(1)};
  gl_PointSize = vSize * uScale;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

// d runs 0 at the centre to 1 at the sprite's edge; one CSS pixel is
// 2.0 / vSize in those units.
const FRAGMENT = `
uniform vec3 uRing;
uniform vec3 uDim;
varying vec3 vColor;
varying float vLit;
varying float vSel;
varying float vSize;
void main() {
  float d = length(gl_PointCoord * 2.0 - 1.0);
  if (d > 1.0) discard;
  float px = 2.0 / vSize;
  if (vSel > 0.5 && d > 1.0 - 2.0 * px) { gl_FragColor = vec4(uRing, 1.0); return; }
  float core = (vSize - vSel * ${RING_PX.toFixed(1)}) / vSize;
  if (d > core) discard;
  if (vLit > 0.5) { gl_FragColor = vec4(vColor, 1.0); return; }
  if (d < core - 1.5 * px) discard;
  gl_FragColor = vec4(uDim, 0.6);
}`;

function hasWebGL2() {
  return typeof WebGL2RenderingContext !== "undefined";
}

async function fetchLayout(fixture, sha) {
  let res;
  try {
    res = await fetch(`/fixture-layout/${encodeURIComponent(fixture)}.json?sha=${encodeURIComponent(sha)}`);
  } catch (e) {
    throw new Error(`the LED layout could not be fetched (${e.message})`);
  }
  if (!res.ok) throw new Error(`no LED layout for ${fixture} (HTTP ${res.status})`);
  try {
    return await res.json();
  } catch (e) {
    throw new Error(`the LED layout for ${fixture} is not valid JSON`);
  }
}

async function loadModel(fixture, sha) {
  const url = `/fixture-model/${encodeURIComponent(fixture)}.glb?sha=${encodeURIComponent(sha)}`;
  try {
    return await new GLTFLoader().loadAsync(url);
  } catch (e) {
    throw new Error(`the model failed to load (${(e && e.message) || e})`);
  }
}

function makeTranslucent(root) {
  root.traverse((obj) => {
    if (!obj.isMesh) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const m of mats) {
      if (!m) continue;
      m.transparent = true;
      m.opacity = MESH_OPACITY;
      m.depthWrite = false;
    }
  });
}

function disposeTree(root) {
  root.traverse((obj) => {
    if (obj.geometry) obj.geometry.dispose();
    const mats = obj.material ? (Array.isArray(obj.material) ? obj.material : [obj.material]) : [];
    for (const m of mats) {
      for (const v of Object.values(m)) if (v && v.isTexture) v.dispose();
      m.dispose();
    }
  });
}

export async function createFixtureView(container, { fixture, sha256, onPick }) {
  if (!hasWebGL2()) throw new Error("this browser has no WebGL2");
  const [layout, gltf] = await Promise.all([fetchLayout(fixture, sha256), loadModel(fixture, sha256)]);
  const pixels = layout.pixels || [];

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (e) {
    throw new Error("this browser has no WebGL2");
  }
  const dpr = Math.min((typeof window !== "undefined" && window.devicePixelRatio) || 1, 2);
  renderer.setPixelRatio(dpr);
  const canvas = renderer.domElement;
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffeedd, 0x332211, 2.0));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(1, 2, 1.5);
  scene.add(sun);

  const holder = new THREE.Group();
  holder.scale.setScalar(1000);
  holder.add(gltf.scene);
  makeTranslucent(gltf.scene);
  scene.add(holder);

  // LEDs: one Points object, attributes indexed by position in `pixels`.
  const n = pixels.length;
  const positions = new Float32Array(n * 3);
  const colors = new Float32Array(n * 3);
  const sizes = new Float32Array(n);
  const lit = new Float32Array(n);
  const sel = new Float32Array(n);
  const slotByIndex = new Map();
  pixels.forEach((p, i) => {
    positions.set([p.x_mm, p.z_mm, -p.y_mm], i * 3);
    sizes[i] = SIZE_PX[p.size] || SIZE_PX.medium;
    slotByIndex.set(p.index, i);
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const colorAttr = new THREE.BufferAttribute(colors, 3);
  const litAttr = new THREE.BufferAttribute(lit, 1);
  const selAttr = new THREE.BufferAttribute(sel, 1);
  geometry.setAttribute("aColor", colorAttr);
  geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
  geometry.setAttribute("aLit", litAttr);
  geometry.setAttribute("aSel", selAttr);
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uScale: { value: dpr },
      uRing: { value: new THREE.Color(0xf2c35b) },      // --gold
      uDim: { value: new THREE.Color(0xffdbcc) },       // --ink
    },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
  const points = new THREE.Points(geometry, material);
  points.renderOrder = 10;
  points.frustumCulled = false;
  scene.add(points);

  // Camera framed from the bounding box of the mesh and the LEDs.
  const box = new THREE.Box3().setFromObject(holder);
  for (let i = 0; i < n; i++) {
    box.expandByPoint(new THREE.Vector3(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]));
  }
  const center = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
  const radius = box.isEmpty() ? 100 : Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1);
  const fov = 40;
  const camera = new THREE.PerspectiveCamera(fov, 1, radius / 100, radius * 20);
  const distance = (radius / Math.sin((fov * Math.PI) / 360)) * 1.05;
  camera.position.copy(center).add(new THREE.Vector3(1, 0.5, 1).normalize().multiplyScalar(distance));
  camera.lookAt(center);

  const controls = new OrbitControls(camera, canvas);
  controls.target.copy(center);
  controls.update();

  let disposed = false;
  let frameRequested = false;
  const requestRender = () => {
    if (frameRequested || disposed) return;
    frameRequested = true;
    requestAnimationFrame(() => {
      frameRequested = false;
      if (!disposed) renderer.render(scene, camera);
    });
  };
  controls.addEventListener("change", requestRender);

  const resize = () => {
    const w = container.clientWidth || FALLBACK_W;
    const h = container.clientHeight || FALLBACK_H;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    requestRender();
  };
  const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
  if (observer) observer.observe(container);
  resize();

  // Picking: the nearest LED on screen within its own radius plus a little
  // slop. Points are drawn without depth test, so screen distance is what
  // the developer sees, whichever side of the mesh the LED is on.
  let downAt = null;
  const onDown = (e) => { downAt = { x: e.clientX, y: e.clientY }; };
  const onUp = (e) => {
    if (!downAt) return;
    const moved = Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y);
    downAt = null;
    if (moved > CLICK_MOVE_PX || !onPick) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const v = new THREE.Vector3();
    let best = null;
    let bestD = Infinity;
    for (let i = 0; i < n; i++) {
      v.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]).project(camera);
      if (v.z > 1) continue;
      const sx = ((v.x + 1) / 2) * rect.width;
      const sy = ((1 - v.y) / 2) * rect.height;
      const d = Math.hypot(sx - mx, sy - my);
      if (d <= sizes[i] / 2 + PICK_SLOP_PX && d < bestD) { best = i; bestD = d; }
    }
    if (best !== null) onPick(pixels[best].index, layout);
  };
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointerup", onUp);

  return {
    paint(channels) {
      const rgb = decodeRgbw(channels);
      for (const [index, i] of slotByIndex) {
        const c = rgb[index] || [0, 0, 0];
        const sum = (channels[index * 4] || 0) + (channels[index * 4 + 1] || 0)
          + (channels[index * 4 + 2] || 0) + (channels[index * 4 + 3] || 0);
        lit[i] = sum < UNLIT_SUM ? 0 : 1;
        colors[i * 3] = c[0] / 255;
        colors[i * 3 + 1] = c[1] / 255;
        colors[i * 3 + 2] = c[2] / 255;
      }
      colorAttr.needsUpdate = true;
      litAttr.needsUpdate = true;
      requestRender();
    },
    select(index) {
      sel.fill(0);
      const i = index === null || index === undefined ? undefined : slotByIndex.get(index);
      if (i !== undefined) sel[i] = 1;
      selAttr.needsUpdate = true;
      requestRender();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (observer) observer.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      controls.removeEventListener("change", requestRender);
      controls.dispose();
      disposeTree(scene);
      renderer.dispose();
      // Browsers cap live WebGL contexts; give this one back now.
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}
