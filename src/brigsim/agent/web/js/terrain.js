/**
 * brigsim Three.js 地形渲染模块（live 模式） — v2 修复版
 * ====================================================
 * 修复：世界尺寸对齐后端 10km、绝对高度着色、FBM freq 调对、camera 看全图、单位从 poll 拉位置
 */

// ---------- FBM 噪声（value noise + smoothstep + 多八度） ----------
const perm = new Uint8Array(512);
(function _seed256() {
  for (let i = 0; i < 256; i++) perm[i] = i;
  for (let i = 255; i > 0; i--) {
    const j = (Math.random() * (i + 1)) | 0;
    [perm[i], perm[j]] = [perm[j], perm[i]];
  }
  for (let i = 0; i < 256; i++) perm[256 + i] = perm[i];
})();
function fade(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
function valueNoise2D(x, y) {
  const X = Math.floor(x) & 255, Y = Math.floor(y) & 255;
  x -= Math.floor(x); y -= Math.floor(y);
  const u = fade(x), v = fade(y);
  const A = perm[X] + Y, B = perm[X + 1] + Y;
  const A1 = perm[A + 1], B1 = perm[B + 1];
  const g = (h, tx, ty) => {
    const hh = h & 3; const u2 = hh < 2 ? tx : ty; const v2 = hh < 2 ? ty : tx;
    return ((h & 1) ? -u2 : u2) + ((h & 2) ? -v2 : v2);
  };
  return lerp(lerp(g(perm[A], x, y), g(perm[B], x - 1, y), u),
             lerp(g(A1, x, y - 1), g(B1, x - 1, y - 1), u), v);
}
function lerp(a, b, t) { return a + t * (b - a); }
function fbm2D(x, y, oct, lac, gain) {
  let amp = 1, freq = 1, s = 0, n = 0;
  for (let i = 0; i < oct; i++) { s += amp * valueNoise2D(x * freq, y * freq); n += amp; amp *= gain; freq *= lac; }
  return s / n;
}

// ---------- 7 种 preset（世界 10km × 10km，高度单位：米，对齐后端 terrain_presets.py） ----------
// 关键：freq 是 noise 空间频率（每米周期数）。世界 10000m，所以 freq=0.001 → 10 个周期
const PRESETS = {
  canyon:    { amp: 160, base: 40,  water: -10, oct: 5, lac: 2.2, gain: 0.5, freq: 0.0008 },
  plain:     { amp: 40,  base: 50,  water: 5,  oct: 3, lac: 2.0, gain: 0.5, freq: 0.0020 },
  rolling:   { amp: 90,  base: 60,  water: 10, oct: 5, lac: 2.1, gain: 0.5, freq: 0.0010 },
  paddy:     { amp: 15,  base: 40,  water: 35, oct: 2, lac: 2.0, gain: 0.5, freq: 0.0025 },
  mountain:  { amp: 260, base: 90,  water: -30, oct: 6, lac: 2.3, gain: 0.5, freq: 0.0006 },
  desert:    { amp: 60,  base: 55,  water: -5,  oct: 4, lac: 2.0, gain: 0.5, freq: 0.0015 },
  snowfield: { amp: 160, base: 70,  water: 0,   oct: 5, lac: 2.1, gain: 0.5, freq: 0.0009 },
};

// 绝对高度阈值着色（不是归一化！plain 的小起伏不会被硬分成岩石/雪）
const PALETTES = {
  //                 water   sand       grass      forest     rock       snow
  canyon:    { water:[60,75,95],  sand:[130,100,70], grass:[115,120,70], forest:[80,90,55],  rock:[95,80,65],  snow:[140,125,110], sandMax:3, forestMin:6, rockMin:12, snowMin:220 },
  plain:     { water:[50,90,120], sand:[210,195,145], grass:[130,160,80], forest:[80,110,60], rock:[160,150,130], snow:[240,240,230],  sandMax:3, forestMin:20, rockMin:200, snowMin:999 },
  rolling:   { water:[50,90,120], sand:[195,180,130], grass:[110,140,70], forest:[60,90,50],  rock:[130,120,105], snow:[230,230,220],  sandMax:3, forestMin:25, rockMin:100, snowMin:300 },
  paddy:     { water:[55,100,130], sand:[200,185,135], grass:[145,170,80], forest:[70,100,55], rock:[170,160,140], snow:[240,240,230],  sandMax:2, forestMin:35, rockMin:200, snowMin:999 },
  mountain:  { water:[45,80,110], sand:[120,95,65],   grass:[100,120,60], forest:[50,75,40],  rock:[75,60,45],   snow:[220,220,210],  sandMax:3, forestMin:20, rockMin:110, snowMin:240 },
  desert:    { water:[70,80,100], sand:[230,200,140], grass:[175,160,95], forest:[150,130,85],rock:[170,145,100], snow:[240,235,220],  sandMax:4, forestMin:30, rockMin:200, snowMin:999 },
  snowfield: { water:[80,110,140],sand:[210,215,220], grass:[180,195,185],forest:[120,140,120],rock:[155,155,160], snow:[235,240,245],  sandMax:3, forestMin:40, rockMin:180, snowMin:100 },
};

// 坡度阈值着色（用中心差分算 slope，陡坡 → rock）
function colorForHeightAndSlope(h, slope, p, pal) {
  const wl = p.water;
  if (h < wl) return pal.water;
  if (h < wl + pal.sandMax) return pal.sand;
  if (slope > 0.5) return pal.rock;            // 陡坡直接岩石（canyon 关键！）
  if (h >= pal.snowMin) return pal.snow;
  if (h >= pal.rockMin) return pal.rock;
  if (h >= pal.forestMin) return pal.forest;
  return pal.grass;
}

const WEATHER = {
  clear:      { fog: 0, sky: 0x1a2a4a },
  light_fog:  { fog: 0.3, sky: 0x3a4a5a },
  heavy_fog:  { fog: 0.7, sky: 0x5a5a6a },
  light_rain: { fog: 0.2, sky: 0x2a3a4a },
  heavy_rain: { fog: 0.45, sky: 0x333a45 },
  snow:       { fog: 0.25, sky: 0x4a5a6a },
};

// =========================================================
class ThreeScene {
  constructor(canvasEl, opts = {}) {
    this.canvas = canvasEl;
    this.worldSize = opts.worldSize || 10000;
    this.resolution = opts.resolution || 128;
    this._units = new Map();
    this._preset = 'rolling';
    this._weather = 'clear';
    this._running = false;
    this._initRenderer();
    this._initSceneCamera();
    this._initControls();
    this._animate = this._animate.bind(this);
  }

  _initRenderer() {
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(this.canvas.clientWidth, this.canvas.clientHeight, false);
    window.addEventListener('resize', () => this._onResize());
  }

  _initSceneCamera() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x1a2a4a);
    this.scene.fog = new THREE.FogExp2(0x1a2a4a, 0.00008);

    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    this.camera = new THREE.PerspectiveCamera(55, w / h, 0.5, 50000);

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const dir = new THREE.DirectionalLight(0xfff0d0, 0.85);
    dir.position.set(5000, 10000, 3000);
    this.scene.add(dir);
  }

  _initControls() {
    const c = this.canvas;
    let drag = null, pan = null;
    c.addEventListener('pointerdown', (e) => {
      c.setPointerCapture(e.pointerId);
      if (e.button === 0) drag = { x: e.clientX, y: e.clientY, ry: this._ry, rx: this._rx };
      if (e.button === 2) pan  = { x: e.clientX, y: e.clientY, tx: this._tx, tz: this._tz };
    });
    c.addEventListener('pointermove', (e) => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        this._ry = drag.ry - dx * 0.005;
        this._rx = Math.max(-1.2, Math.min(0.05, drag.rx - dy * 0.005));
        this._updateCam();
      }
      if (pan) {
        const dx = e.clientX - pan.x, dy = e.clientY - pan.y;
        this._tx = pan.tx - dx * 20;   // 10km 世界平移幅度要大
        this._tz = pan.tz + dy * 20;
        this._updateCam();
      }
    });
    c.addEventListener('pointerup', () => { drag = pan = null; });
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const f = Math.exp(e.deltaY * 0.0012);
      this._dist = Math.max(800, Math.min(25000, this._dist * f));
      this._updateCam();
    }, { passive: false });
    // 默认俯视斜角看全 10km 世界
    this._rx = -0.9; this._ry = 0.0; this._dist = 16000; this._tx = 0; this._tz = 0;
    this._updateCam();
  }

  _updateCam() {
    const { _rx: rx, _ry: ry, _dist: d, _tx: tx, _tz: tz } = this;
    const x = d * Math.cos(rx) * Math.cos(ry);
    const z = d * Math.cos(rx) * Math.sin(ry);
    const y = d * Math.sin(-rx) + 50;
    this.camera.position.set(x + tx, y, z + tz);
    this.camera.lookAt(tx, 50, tz);
  }

  _onResize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (w === 0 || h === 0) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  setScenario(scenarioName) {
    const n = scenarioName.toLowerCase();
    const family =
      n.includes('canyon')   ? 'canyon'    :
      n.includes('plain')    ? 'plain'     :
      n.includes('rolling')  ? 'rolling'   :
      n.includes('paddy')    ? 'paddy'     :
      n.includes('mountain') ? 'mountain'  :
      n.includes('desert')   ? 'desert'    :
      n.includes('snow')     ? 'snowfield' : 'rolling';
    this._preset = family;
    this._buildTerrain();
    const weather =
      n.includes('heavy_fog') || n.includes('plain_fog') ? 'heavy_fog' :
      n.includes('fog')  ? 'light_fog'  :
      n.includes('rain') ? 'heavy_rain' :
      n.includes('snow') ? 'snow'       : 'clear';
    this._setWeather(weather);
  }

  _buildTerrain() {
    for (const name of ['__terrain', '__water', '__rain', '__snow']) {
      const o = this.scene.getObjectByName(name); if (o) this.scene.remove(o);
    }
    for (const [, m] of this._units) this.scene.remove(m);
    this._units.clear();

    const p = PRESETS[this._preset] || PRESETS.rolling;
    const pal = PALETTES[this._preset] || PALETTES.rolling;
    const N = this.resolution, S = this.worldSize;

    // 1) Heightmap
    const heights = new Float32Array(N * N);
    for (let iy = 0; iy < N; iy++) {
      for (let ix = 0; ix < N; ix++) {
        const fx = (ix / N - 0.5) * S, fy = (iy / N - 0.5) * S;
        const n = fbm2D(fx * p.freq, fy * p.freq, p.oct, p.lac, p.gain);
        heights[iy * N + ix] = p.base + p.amp * n;
      }
    }

    // 2) Geometry + 绝对高度着色 + 坡度感知
    const geom = new THREE.PlaneGeometry(S, S, N - 1, N - 1);
    geom.rotateX(-Math.PI / 2);
    const pos = geom.attributes.position;
    const colors = new Float32Array(N * N * 3);
    const wl = p.water;

    for (let i = 0; i < N * N; i++) pos.setY(i, heights[i]);
    geom.computeVertexNormals();
    const norm = geom.attributes.normal;

    for (let i = 0; i < N * N; i++) {
      const h = heights[i];
      // slope = |normal.y| 越小越陡
      const ny = norm.getY(i);
      const slope = 1 - Math.abs(ny);
      const c = colorForHeightAndSlope(h, slope, p, pal);
      colors[i * 3]     = c[0] / 255;
      colors[i * 3 + 1] = c[1] / 255;
      colors[i * 3 + 2] = c[2] / 255;
    }
    geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.95, metalness: 0.0 });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.name = '__terrain';
    this.scene.add(mesh);

    // 3) 水面（按 water_level 高度）
    if (wl > -500) {
      const wgeom = new THREE.PlaneGeometry(S * 1.02, S * 1.02);
      wgeom.rotateX(-Math.PI / 2);
      const wmat = new THREE.MeshStandardMaterial({
        color: 0x3a6090, transparent: true, opacity: 0.55, roughness: 0.25, metalness: 0.15,
      });
      const wmesh = new THREE.Mesh(wgeom, wmat);
      wmesh.position.y = wl; wmesh.name = '__water';
      this.scene.add(wmesh);
    }
  }

  _setWeather(name) {
    this._weather = name;
    const W = WEATHER[name] || WEATHER.clear;
    this.scene.fog.density = W.fog * 0.00012;
    this.scene.background = new THREE.Color(W.sky);
    this.renderer.setClearColor(W.sky, 1);

    for (const n of ['__rain', '__snow']) {
      const o = this.scene.getObjectByName(n); if (o) this.scene.remove(o);
    }

    // 雨 / 雪（覆盖 10km 世界）
    if (name.includes('rain')) {
      const count = 3500;
      const pos = new Float32Array(count * 3);
      const vel = new Float32Array(count);
      for (let i = 0; i < count; i++) {
        pos[i*3]   = (Math.random() - 0.5) * 10000;
        pos[i*3+1] = 2000 + Math.random() * 1500;
        pos[i*3+2] = (Math.random() - 0.5) * 10000;
        vel[i] = 8 + Math.random() * 6;
      }
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      geom.userData.vel = vel;
      const mat = new THREE.PointsMaterial({ color: 0x7090b0, size: 25, transparent: true, opacity: 0.55 });
      const pts = new THREE.Points(geom, mat);
      pts.name = '__rain'; this.scene.add(pts); this._rain = pts;
    } else this._rain = null;

    if (name === 'snow') {
      const count = 2500;
      const pos = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        pos[i*3]   = (Math.random() - 0.5) * 10000;
        pos[i*3+1] = 2000 + Math.random() * 1500;
        pos[i*3+2] = (Math.random() - 0.5) * 10000;
      }
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      const mat = new THREE.PointsMaterial({ color: 0xffffff, size: 40, transparent: true, opacity: 0.85 });
      const pts = new THREE.Points(geom, mat);
      pts.name = '__snow'; this.scene.add(pts); this._snow = pts;
    } else this._snow = null;
  }

  /**
   * units: [{id, x, y, team:'red'|'blue', state}]
   * 后端返回的 x/y 单位：米（400→7500 范围，直接在 worldSize=10000 世界里用）
   */
  updateUnits(units) {
    if (!units) return;
    const alive = new Set();
    const teamColors = { red: 0xd64537, blue: 0x4084f0 };
    for (const u of units) {
      alive.add(u.id);
      let m = this._units.get(u.id);
      if (!m) {
        // 坦克方块：30m × 40m × 15m（10km 世界里可见）
        const g = new THREE.BoxGeometry(30, 15, 40);
        const body = teamColors[u.team] || 0x888888;
        const mat = new THREE.MeshStandardMaterial({
          color: body, flatShading: true, emissive: body, emissiveIntensity: 0.2,
        });
        m = new THREE.Mesh(g, mat);
        m.name = `__unit_${u.id}`;
        this.scene.add(m);
        this._units.set(u.id, m);
      }
      m.position.set(u.x, 25, u.y);
      if (u.state === 'dead') {
        m.scale.set(0.6, 0.3, 0.6);
        m.material.emissive.setHex(0x333333);
      } else if (u.state === 'damaged') {
        m.material.emissive.setHex(0xff8020);
      }
    }
    for (const [id, m] of this._units) {
      if (!alive.has(id)) { this.scene.remove(m); this._units.delete(id); }
    }
  }

  start() {
    if (this._running) return;
    this._running = true;
    this._buildTerrain();
    this._setWeather('clear');
    requestAnimationFrame(this._animate);
  }
  stop() { this._running = false; }

  _animate() {
    if (!this._running) return;
    if (this._rain) {
      const p = this._rain.geometry.attributes.position.array;
      const v = this._rain.geometry.userData.vel;
      for (let i = 0; i < p.length / 3; i++) {
        p[i * 3 + 1] -= v[i];
        if (p[i * 3 + 1] < 0) {
          p[i * 3 + 1] = 2500;
          p[i * 3] = (Math.random() - 0.5) * 10000;
          p[i * 3 + 2] = (Math.random() - 0.5) * 10000;
        }
      }
      this._rain.geometry.attributes.position.needsUpdate = true;
    }
    if (this._snow) {
      const p = this._snow.geometry.attributes.position.array;
      for (let i = 0; i < p.length / 3; i++) {
        p[i * 3 + 1] -= 2;
        p[i * 3] += Math.sin(i + performance.now() * 0.001) * 2;
        if (p[i * 3 + 1] < 0) {
          p[i * 3 + 1] = 2500;
          p[i * 3] = (Math.random() - 0.5) * 10000;
          p[i * 3 + 2] = (Math.random() - 0.5) * 10000;
        }
      }
      this._snow.geometry.attributes.position.needsUpdate = true;
    }
    this.renderer.render(this.scene, this.camera);
    requestAnimationFrame(this._animate);
  }
}

if (typeof window !== 'undefined') {
  window.BrigsimThree = { ThreeScene, PRESETS, PALETTES, WEATHER };
}
