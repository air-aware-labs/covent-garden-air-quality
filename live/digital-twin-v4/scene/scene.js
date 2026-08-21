/* Covent Garden Plants - evening scene.
 *
 * Local metres map to three.js as x = east, y = height, z = -north. That is
 * right-handed with north running into -z, which is the convention that stops
 * the scene mirroring east-for-west - the bug that cost a day on v3.1.
 */
(function () {
  "use strict";

  var GEO = window.__GEO__, TRAFFIC = window.__TRAFFIC__, OBS = window.__OBS__,
      CURRENT_AQ = window.__CURRENT_AQ__, WEATHER = window.__WEATHER__;

  /* The scene keeps three honest five-minute episodes. OBS is the fixed 5-10
   * August raw-AQ record with an Open-Meteo 10 m estimate. CURRENT_AQ is the
   * sanitized post-historic API record when available. WEATHER is roof-level
   * Tempest. AQ frames use only an exact timestamp-matched Tempest bucket;
   * Open-Meteo remains a fallback solely for records that explicitly carry it. */
  var OBS_N = (OBS && OBS.n) || 0;
  var CURRENT_AQ_N = (CURRENT_AQ && CURRENT_AQ.n) || 0;
  var WEATHER_N = (WEATHER && WEATHER.n) || 0;
  function recordForMode(mode) {
    if (mode === "current-aq" && CURRENT_AQ) return CURRENT_AQ;
    if (mode === "tempest" && WEATHER) return WEATHER;
    return OBS;
  }
  function activeRecord() {
    return recordForMode(state ? state.recordMode : "historic-aq");
  }
  function activeCount() {
    if (state && state.recordMode === "current-aq" && CURRENT_AQ) return CURRENT_AQ_N;
    if (state && state.recordMode === "tempest" && WEATHER) return WEATHER_N;
    return OBS_N;
  }
  function recordStepMs(record) {
    return record && record.step_seconds ? record.step_seconds * 1000 :
           (((record && record.step_minutes) || 5) * 60000);
  }
  function recordTimeMs(record, frame) {
    return Date.parse(record.start_utc) + frame * recordStepMs(record);
  }
  function recordDate(f) {
    var record = activeRecord();
    if (!record) return new Date();
    return new Date(recordTimeMs(record, f));
  }
  function arrayAt(record, name, frame) {
    var values = record && record[name];
    if (!values || !values.length) return null;
    var value = values[clamp(frame, 0, values.length - 1)];
    return value === null || value === undefined || !Number.isFinite(+value) ? null : +value;
  }
  function tempestFrameWeather(i) {
    return {
      source: "tempest",
      quality_code: WEATHER.quality_code[i],
      coverage_fraction: arrayAt(WEATHER, "coverage_fraction", i),
      temperature_c: arrayAt(WEATHER, "temperature_c", i),
      relative_humidity_pct: arrayAt(WEATHER, "relative_humidity_pct", i),
      wind_lull_m_s: arrayAt(WEATHER, "wind_lull_m_s", i),
      wind_average_m_s: arrayAt(WEATHER, "wind_average_m_s", i),
      wind_gust_m_s: arrayAt(WEATHER, "wind_gust_m_s", i),
      wind_from_deg: arrayAt(WEATHER, "wind_from_deg", i),
      rain_5min_mm: arrayAt(WEATHER, "rain_display_5min_mm", i),
      local_day_rain_mm: arrayAt(WEATHER, "local_day_display_rain_mm", i)
    };
  }
  function matchingTempestIndex(aqRecord, aqFrame) {
    if (!aqRecord || !WEATHER || !WEATHER_N) return -1;
    if (aqRecord === CURRENT_AQ &&
        (!aqRecord.timestamp_alignment || aqRecord.timestamp_alignment.verified !== true)) {
      return -1;
    }
    var observedAt = recordTimeMs(aqRecord, aqFrame);
    var weatherStart = Date.parse(WEATHER.start_utc);
    var weatherStep = recordStepMs(WEATHER);
    var index = Math.round((observedAt - weatherStart) / weatherStep);
    if (index < 0 || index >= WEATHER_N ||
        Math.abs(observedAt - (weatherStart + index * weatherStep)) > 1000) return -1;
    var source = WEATHER.source_code && WEATHER.source_code[index];
    var quality = WEATHER.quality_code && WEATHER.quality_code[index];
    return source === "tempest" && quality !== "missing" ? index : -1;
  }
  function frameWeather(frame) {
    if (state && state.recordMode === "tempest" && WEATHER) {
      return tempestFrameWeather(clamp(frame, 0, WEATHER_N - 1));
    }
    var aqRecord = activeRecord();
    var tempestIndex = matchingTempestIndex(aqRecord, frame);
    if (tempestIndex >= 0) return tempestFrameWeather(tempestIndex);
    if (!aqRecord || !aqRecord.wind_speed || !aqRecord.wind_from_deg) {
      var alignmentUnverified = aqRecord === CURRENT_AQ &&
        (!aqRecord.timestamp_alignment || aqRecord.timestamp_alignment.verified !== true);
      return {
        source: "unavailable",
        quality_code: alignmentUnverified
          ? "AQ timestamp alignment unverified"
          : "no exact Tempest match",
        coverage_fraction: null,
        temperature_c: null,
        relative_humidity_pct: null,
        wind_lull_m_s: null,
        wind_average_m_s: null,
        wind_gust_m_s: null,
        wind_from_deg: null,
        rain_5min_mm: null,
        local_day_rain_mm: null
      };
    }
    return {
      source: "openmeteo_proxy",
      quality_code: "regional estimate",
      coverage_fraction: 1,
      temperature_c: arrayAt(aqRecord, "temp_c", frame),
      relative_humidity_pct: arrayAt(aqRecord, "rh_pct", frame),
      wind_lull_m_s: null,
      wind_average_m_s: arrayAt(aqRecord, "wind_speed", frame),
      wind_gust_m_s: null,
      wind_from_deg: arrayAt(aqRecord, "wind_from_deg", frame),
      rain_5min_mm: null,
      local_day_rain_mm: null
    };
  }
  function obsPM(unit, f) {
    if (state && state.recordMode === "tempest") return null;
    var aqRecord = activeRecord();
    if (!aqRecord || !aqRecord.pm25 || !aqRecord.pm25[unit]) return null;
    var v = aqRecord.pm25[unit][clamp(f, 0, activeCount() - 1)];
    return (v === null || v === undefined) ? null : v;
  }
  // Concentration ramp. Deliberately not a health-band scale - it is a relative
  // read of this record, whose 5-minute peaks reach about 110 ug/m3.
  var _pmc = new THREE.Color();
  function pmColour(v) {
    if (v === null) return _pmc.setHex(0x6c7580);
    var t = clamp(Math.pow(v / 60, 0.62), 0, 1);
    return _pmc.setHSL(lerp(0.52, 0.02, t), lerp(0.28, 0.86, t), lerp(0.78, 0.52, t));
  }
  var LID = window.__LIDAR__;
  var TAU = Math.PI * 2;

  /* ---------------------------------------------------------------- helpers */
  function toV(e, h, n) { return new THREE.Vector3(e, h, -n); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function smooth(t) { return t * t * (3 - 2 * t); }
  // deterministic jitter so a building looks the same on every reload
  function hash(s) {
    var h = 2166136261, i;
    s = String(s);
    for (i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 10000) / 10000;
  }
  // Shape space is (east, north). ExtrudeGeometry extrudes along +z and the
  // caller then applies rotateX(-PI/2), which maps (x, y, z) -> (x, z, -y):
  // shape-y becomes world -z, i.e. exactly the z = -north convention that the
  // roads, monitors, sources and LiDAR shell all use.
  //
  // This used to pass -north into the shape, so the two negations cancelled and
  // every extruded surface landed at z = +north. The whole OSM roofscape - all
  // 43 buildings, the home outline, the garden, the greenhouse and the planters
  // - was mirrored north-south against everything else in the scene, which is
  // why the flue stacks appeared to stand in open ground with no parade around
  // them. Everything placed directly with -north was always right; only the
  // extrusions were flipped.
  //
  // OSM ways are digitised in either direction and ExtrudeGeometry takes its
  // side-wall normals from the winding, so the contour is forced anticlockwise
  // (positive signed area in shape space) or the walls face inward.
  function orientRing(ring) {
    var a = 0, i, j;
    for (i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      a += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
    }
    return a < 0 ? ring.slice().reverse() : ring;
  }
  function shapeFrom(ring) {
    ring = orientRing(ring);
    var s = new THREE.Shape();
    s.moveTo(ring[0][0], ring[0][1]);
    for (var i = 1; i < ring.length; i++) s.lineTo(ring[i][0], ring[i][1]);
    s.closePath();
    return s;
  }
  function pointInRing(ring, e, nn) {
    if (!ring) return false;
    var inside = false, i, j;
    for (i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
      if ((yi > nn) !== (yj > nn) &&
          e < ((xj - xi) * (nn - yi)) / ((yj - yi) || 1e-9) + xi) inside = !inside;
    }
    return inside;
  }
  function inReceptorDetail(e, nn) {
    var homes = GEO.receptor_homes || [];
    for (var i = 0; i < homes.length; i++) {
      if (pointInRing(homes[i].suppress_lidar_ring || homes[i].ring, e, nn)) return true;
    }
    return false;
  }
  function ringArea(r) {
    var a = 0, i, j;
    for (i = 0, j = r.length - 1; i < r.length; j = i++) a += r[j][0] * r[i][1] - r[i][0] * r[j][1];
    return Math.abs(a) / 2;
  }
  function onCarriageway(e, nn, y) {
    if (y > 2.4 || !GEO.roads) return false;
    var ri, i;
    for (ri = 0; ri < GEO.roads.length; ri++) {
      var road = GEO.roads[ri], line = road.centreline;
      var hw = road.half_width_m + 0.35;
      for (i = 0; i < line.length - 1; i++) {
        var ax = line[i][0], ay = line[i][1], bx = line[i + 1][0], by = line[i + 1][1];
        var abx = bx - ax, aby = by - ay, ab2 = abx * abx + aby * aby || 1;
        var t = clamp(((e - ax) * abx + (nn - ay) * aby) / ab2, 0, 1);
        var dx = e - (ax + abx * t), dy = nn - (ay + aby * t);
        if (dx * dx + dy * dy < hw * hw) return true;
      }
    }
    return false;
  }

  /* ------------------------------------------------------------------ scene */
  var canvas = document.getElementById("scene");
  var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.02;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(38, 1, 0.4, 2600);

  var hemi = new THREE.HemisphereLight(0xccd7e0, 0x6a5843, 1.02);
  scene.add(hemi);
  var sun = new THREE.DirectionalLight(0xffe9c4, 1.95);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  var sc = sun.shadow.camera;
  sc.left = -90; sc.right = 90; sc.top = 90; sc.bottom = -90; sc.near = 1; sc.far = 400;
  sun.shadow.bias = -0.0009;
  sun.shadow.normalBias = 0.5;
  scene.add(sun);
  scene.add(sun.target);
  // Warm fill from the south-west standing in for ground and facade bounce, so
  // the shadowed rear elevations keep their form instead of going flat navy.
  var bounce = new THREE.DirectionalLight(0xe8c9a4, 0.55);
  bounce.position.set(-55, 26, 95);
  scene.add(bounce);
  var rim = new THREE.DirectionalLight(0xaec6dd, 0.20);
  rim.position.set(70, 30, -60);
  scene.add(rim);

  /* --------------------------------------------------------------------- sky */
  var skyUniforms = {
    top: { value: new THREE.Color(0x1a3252) },
    mid: { value: new THREE.Color(0x7d93ad) },
    bot: { value: new THREE.Color(0xe8c9a4) },
    sunDir: { value: new THREE.Vector3(0, 1, 0) },
    sunTint: { value: new THREE.Color(0xffb264) },
    glow: { value: 0.5 },
    night: { value: 0.0 }
  };
  var sky = new THREE.Mesh(
    new THREE.SphereGeometry(1500, 48, 32),
    new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false, uniforms: skyUniforms,
      vertexShader:
        "varying vec3 vW; void main(){ vW = normalize(position); " +
        "gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
      fragmentShader:
        "uniform vec3 top,mid,bot,sunTint; uniform vec3 sunDir; uniform float glow, night;" +
        "varying vec3 vW;" +
        "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }" +
        "void main(){ vec3 w = normalize(vW); float h = w.y;" +
        "vec3 c = h > 0.0 ? mix(mid, top, pow(clamp(h,0.0,1.0), 0.55))" +
        "                 : mix(mid, bot, pow(clamp(-h*2.4,0.0,1.0), 0.62));" +
        // warm horizon haze, strongest at dusk
        "c += vec3(1.0,0.62,0.32) * glow * 0.18 * exp(-abs(h)*8.0);" +
        "float d = max(dot(w, normalize(sunDir)), 0.0);" +
        "c += sunTint * pow(d, 8.0) * glow * 1.65;" +
        "c += sunTint * pow(d, 2.2) * glow * 0.28;" +
        "c += sunTint * smoothstep(0.9984, 0.9994, d) * glow * 2.8;" +
        // stars only after dark, above the horizon
        "float n = hash(floor(w.xz * 220.0));" +
        "float star = smoothstep(0.9965, 1.0, n) * night * smoothstep(-0.02, 0.18, h);" +
        "c += vec3(star);" +
        "gl_FragColor = vec4(c, 1.0); }"
    })
  );
  sky.frustumCulled = false;
  scene.add(sky);
  // Aerial perspective, updated with the sky each frame rather than rebuilt.
  scene.fog = new THREE.FogExp2(0x8ea2b8, 0.0026);

  /* ------------------------------------------------------------------ ground */
  var ground = new THREE.Mesh(
    new THREE.PlaneGeometry(1600, 1600),
    new THREE.MeshStandardMaterial({ color: 0x1a1c20, roughness: 0.98, metalness: 0.0 })
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.05;
  ground.receiveShadow = true;
  scene.add(ground);

  /* ------------------------------------------------------------------- roads */
  function ribbon(line, half, y) {
    var pos = [], uv = [], idx = [], i, a, b, dx, dz, L, nx, nz, run = 0;
    for (i = 0; i < line.length; i++) {
      a = line[i]; b = line[Math.min(i + 1, line.length - 1)];
      if (i === line.length - 1) { a = line[i - 1]; b = line[i]; }
      dx = b[0] - a[0]; dz = -(b[1] - a[1]); L = Math.hypot(dx, dz) || 1;
      nx = -dz / L; nz = dx / L;
      var p = line[i];
      if (i > 0) run += Math.hypot(p[0] - line[i - 1][0], p[1] - line[i - 1][1]);
      pos.push(p[0] + nx * half, y, -p[1] + nz * half);
      pos.push(p[0] - nx * half, y, -p[1] - nz * half);
      // Without UVs a mapped material samples nothing and the surface renders
      // white - which is how the carriageway came out as a pale sheet.
      uv.push(0, run / 8, 1, run / 8);
    }
    for (i = 0; i < line.length - 1; i++) {
      var k = i * 2;
      idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
    }
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }

  // A road is not one flat quad. Kerb line, carriageway, edge lines and a
  // dashed centre line each read at a glance and cost almost nothing.
  function paintedStrip(line, offset, half, y, dash, gap) {
    var pos = [], idx = [], i, vi = 0, run = 0, drawing = true;
    for (i = 0; i < line.length - 1; i++) {
      var a = line[i], b = line[i + 1];
      var dx = b[0] - a[0], dn = b[1] - a[1], L = Math.hypot(dx, dn) || 1;
      var nx = -dn / L, nn = dx / L;                       // left normal in (e,n)
      var step = dash ? 0.5 : L;                           // sample finely if dashed
      for (var t = 0; t < L; t += step) {
        var t2 = Math.min(t + step, L);
        if (dash) {
          run += step;
          if (run > (drawing ? dash : gap)) { drawing = !drawing; run = 0; }
          if (!drawing) continue;
        }
        var p0e = a[0] + (dx / L) * t + nx * offset, p0n = a[1] + (dn / L) * t + nn * offset;
        var p1e = a[0] + (dx / L) * t2 + nx * offset, p1n = a[1] + (dn / L) * t2 + nn * offset;
        pos.push(p0e + nx * half, y, -(p0n + nn * half));
        pos.push(p0e - nx * half, y, -(p0n - nn * half));
        pos.push(p1e + nx * half, y, -(p1n + nn * half));
        pos.push(p1e - nx * half, y, -(p1n - nn * half));
        idx.push(vi, vi + 1, vi + 2, vi + 1, vi + 3, vi + 2);
        vi += 4;
      }
    }
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setIndex(idx); g.computeVertexNormals();
    return g;
  }

  function asphaltTexture() {
    var c = document.createElement("canvas"); c.width = c.height = 128;
    var x = c.getContext("2d");
    x.fillStyle = "#141618"; x.fillRect(0, 0, 128, 128);
    for (var i = 0; i < 3200; i++) {
      var g = 14 + Math.random() * 18;
      x.fillStyle = "rgba(" + g + "," + g + "," + (g + 3) + ",0.7)";
      x.fillRect(Math.random() * 128, Math.random() * 128, 1.6, 1.6);
    }
    var t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(2, 1);
    return t;
  }
  var ASPHALT = asphaltTexture();

  var roadGroup = new THREE.Group();
  scene.add(roadGroup);
  var nkr = null;
  var ROAD_Y = LID ? 0.18 : 0.02;
  GEO.roads.forEach(function (r) {
    // The footway must be drawn as two strips OUTSIDE the carriageway. A single
    // full-width ribbon spans the carriageway too, and being higher it simply
    // hid the road underneath it.
    var footY = ROAD_Y + 0.12;
    var paveMat = new THREE.MeshStandardMaterial({ color: 0x4a4c50, roughness: 0.97 });
    [1, -1].forEach(function (side) {
      var m = new THREE.Mesh(
        paintedStrip(r.centreline, side * (r.half_width_m + r.footway_m / 2),
                     r.footway_m / 2, footY), paveMat);
      m.receiveShadow = true; roadGroup.add(m);
    });

    var carriage = new THREE.Mesh(
      ribbon(r.centreline, r.half_width_m, ROAD_Y),
      new THREE.MeshStandardMaterial({ color: 0x4c5056, map: ASPHALT, roughness: 0.96 })
    );
    carriage.receiveShadow = true; roadGroup.add(carriage);

    var kerbMat = new THREE.MeshStandardMaterial({ color: 0x7a7a76, roughness: 0.9 });
    var lineMat = new THREE.MeshBasicMaterial({ color: 0xe6dfc8, transparent: true, opacity: 0.88 });
    [1, -1].forEach(function (side) {
      roadGroup.add(new THREE.Mesh(
        paintedStrip(r.centreline, side * r.half_width_m, 0.16, footY + 0.005), kerbMat));
      roadGroup.add(new THREE.Mesh(
        paintedStrip(r.centreline, side * (r.half_width_m - 0.55), 0.09, ROAD_Y + 0.012), lineMat));
    });
    if (r.name === "NEW KENT ROAD") {
      // dashed centre line, 4 m mark to 6 m gap
      roadGroup.add(new THREE.Mesh(
        paintedStrip(r.centreline, 0, 0.10, ROAD_Y + 0.012, 4.0, 6.0), lineMat));
      nkr = r;
    }
  });

  /* ------------------------------------------------------------- LIDAR shell
   * EA composite normalised surface model: 1 m DSM minus 1 m DTM.
   *
   * Triangulating a DSM as a smooth heightfield turns every wall into a 45
   * degree ramp and the whole block into a mountain range. So each cell is
   * emitted as its own flat 1 m roof patch, with a vertical skirt dropped
   * between it and any neighbour more than a step below. That is what gives
   * buildings vertical walls and crisp parapets.
   *
   * Cells are classified as roof, ground or vegetation from local roughness:
   * a tree canopy in a last-return surface is high and rough, a roof is high
   * and flat. That single test does most of the work of making it read as a
   * city rather than as terrain.
   */
    if (LID) (function () {
    var cols = LID.cols, rows = LID.rows, n = cols * rows;
    var bin = atob(LID.data), h = new Float32Array(n), i, r, q;
    for (i = 0; i < n; i++) {
      h[i] = ((bin.charCodeAt(i * 2) | (bin.charCodeAt(i * 2 + 1) << 8)) / 100) - 0.5;
    }
    var at = function (rr, qq) {
      rr = rr < 0 ? 0 : rr >= rows ? rows - 1 : rr;
      qq = qq < 0 ? 0 : qq >= cols ? cols - 1 : qq;
      return h[rr * cols + qq];
    };
    // Vegetation test. A single roughness threshold fires on every roof edge,
    // because a parapet is a big step too. What separates a tree canopy from a
    // wall is that the canopy is rough over an *area* while the wall is rough
    // along a one-cell line, so the rough mask is opened: a cell only counts as
    // vegetation if most of its 5x5 neighbourhood is rough as well.
    // Separating canopy from masonry in a 1 m last-return surface.
    //
    // The previous test was per-cell roughness opened with a 15-of-25 majority.
    // That erodes a street tree - a single crown is only 4-7 cells across, so
    // most of its 5x5 neighbourhoods fail the majority - and anything it erodes
    // gets extruded as a building with vertical walls. The site review spotted the result:
    // a row of trees on the far side of County Street rendered as terraces,
    // where in reality there is nothing until the school.
    //
    // Buildings are now separated from vegetation by CONNECTED COMPONENT rather
    // than by cell. The discriminator is flatness: a roof has large smooth
    // areas, a canopy has almost none at 1 m sampling.
    var relief = new Float32Array(n);
    for (r = 0; r < rows; r++) {
      for (q = 0; q < cols; q++) {
        var z0 = at(r, q);
        relief[r * cols + q] =
          (Math.abs(at(r - 1, q) - z0) + Math.abs(at(r + 1, q) - z0) +
           Math.abs(at(r, q - 1) - z0) + Math.abs(at(r, q + 1) - z0)) / 4;
      }
    }
    var veg = new Uint8Array(n);
    var visited = new Uint8Array(n);
    for (i = 0; i < n; i++) {
      if (visited[i] || h[i] <= 1.8) continue;
      var stack = [i], member = [], flat = 0, relSum = 0;
      visited[i] = 1;
      while (stack.length) {
        var kk = stack.pop();
        member.push(kk);
        relSum += relief[kk];
        if (relief[kk] < 0.25) flat++;
        var rr3 = (kk / cols) | 0, qq3 = kk % cols;
        var nbr = [
          rr3 > 0 ? kk - cols : -1, rr3 < rows - 1 ? kk + cols : -1,
          qq3 > 0 ? kk - 1 : -1, qq3 < cols - 1 ? kk + 1 : -1
        ];
        for (var ni2 = 0; ni2 < 4; ni2++) {
          var nk2 = nbr[ni2];
          if (nk2 < 0 || visited[nk2] || h[nk2] <= 1.8) continue;
          visited[nk2] = 1; stack.push(nk2);
        }
      }
      var isVegComp = member.length >= 3 &&
                      relSum / member.length > 0.45 &&
                      flat / member.length < 0.34;
      if (isVegComp) for (var mi = 0; mi < member.length; mi++) veg[member[mi]] = 1;
    }
    // A per-cell roughness test was tried on top of this and dropped. Measured
    // against the classification map it added nothing north of County Street -
    // the component test already finds every tree there - while outlining every
    // roof edge and parapet in false canopy, taking the vegetated fraction from
    // 26% of above-ground cells to 44%. The component test stands alone.

    // Cells beside a genuine height break keep a flat half-cell cap; roof and
    // ground interiors are joined as a continuous triangular surface below.
    // This keeps walls crisp without rendering every 1 m sample as a box.
    var edge = new Uint8Array(n), BREAK = 1.25;
    for (r = 0; r < rows; r++) {
      for (q = 0; q < cols; q++) {
        var ze = at(r, q);
        edge[r * cols + q] = (
          Math.abs(at(r - 1, q) - ze) > BREAK || Math.abs(at(r + 1, q) - ze) > BREAK ||
          Math.abs(at(r, q - 1) - ze) > BREAK || Math.abs(at(r, q + 1) - ze) > BREAK
        ) ? 1 : 0;
      }
    }

    // Baked sky-visibility. There is no SSAO in the core three.js build, but a
    // last-return surface already contains everything needed to fake it: a cell
    // overlooked by taller neighbours sees less sky. Multiplying it into the
    // vertex colour is what makes the massing read as a city with streets and
    // courtyards rather than a flat grey relief map.
    var ao = new Float32Array(n);
    for (r = 0; r < rows; r++) {
      for (q = 0; q < cols; q++) {
        var z0a = at(r, q), occ = 0, samples = 0;
        for (var dr2 = -3; dr2 <= 3; dr2++) {
          for (var dq2 = -3; dq2 <= 3; dq2++) {
            if (!dr2 && !dq2) continue;
            var d2 = Math.sqrt(dr2 * dr2 + dq2 * dq2);
            if (d2 > 3.2) continue;
            samples++;
            var rise = at(r + dr2, q + dq2) - z0a;
            if (rise > 0.4) occ += clamp(rise / (d2 * 2.2), 0, 1);
          }
        }
        ao[r * cols + q] = 1 - 0.60 * clamp(occ / Math.max(samples, 1) * 2.6, 0, 1);
      }
    }

    var W = LID.west_offset_m, N = LID.north_offset_m;
    var pos = [], col = [], nor = [];
    // Roofs must not share a cream with the ground or the block reads as
    // terrain. Slate tops, brick skirts, dark yards.
    var SLATE = new THREE.Color(0x3d434a), SLATE_EDGE = new THREE.Color(0x2f343a);
    var WALL = new THREE.Color(0x6d5646), GROUND = new THREE.Color(0x2a2c30);
    var YARD = new THREE.Color(0x3a3c40);
    var VEG_LO = new THREE.Color(0x3a5630), VEG_HI = new THREE.Color(0x668246);
    var c = new THREE.Color(), wc = new THREE.Color();

    function push(x1, y1, z1, x2, y2, z2, x3, y3, z3, cr, cg, cb, nx, ny, nz) {
      pos.push(x1, y1, z1, x2, y2, z2, x3, y3, z3);
      col.push(cr, cg, cb, cr, cg, cb, cr, cg, cb);
      nor.push(nx, ny, nz, nx, ny, nz, nx, ny, nz);
    }

    for (r = 0; r < rows; r++) {
      for (q = 0; q < cols; q++) {
        var k = r * cols + q, y = h[k];
        var x0 = W + q, x1 = x0 + 1, northCell = N - r,
            zz0 = -northCell, zz1 = zz0 + 1;
        // Keep the useful block around the site. The previous full 300 x 260 m
        // shell made the subject disappear into a noisy field of distant cells.
        if (x1 < -105 || x0 > 85 || northCell < -82 || northCell > 74) continue;
        // In the central block the 1 m samples are excellent height evidence
        // but poor architecture: dormers, roof edges and chimneys become a
        // field of square cells.  The reviewed scene uses photo/OSM house
        // geometry here and retains nDSM massing only as the outer context.
        // The measured surface used to be suppressed across the whole central
        // block so the OSM extrusions could own it. That cost every real roof
        // form in the scene: the western block reads as one 15.3 m mass when
        // the LiDAR shows a 12.6 m setback terrace on it - the terrace the
        // neighbouring monitor actually stands on. Now the surface is drawn
        // everywhere except over
        // the home, which comes from the 1:50 plan and is better than LiDAR.
        if (pointInRing(GEO.home.ring, x0 + 0.5, northCell) ||
            pointInRing(GEO.home.garden_ring, x0 + 0.5, northCell) ||
            inReceptorDetail(x0 + 0.5, northCell) ||
            onCarriageway(x0 + 0.5, northCell, y)) continue;
        var isVeg = veg[k] === 1;
        // Vegetation is rendered as clustered crowns below. Treating every
        // LiDAR canopy cell as a roof with vertical skirts produced tall green
        // columns, particularly beside the A201.
        if (isVeg) continue;
        var grain = (hash(k) - 0.5) * 0.03;
        if (y < 0.9) c.copy(GROUND);
        else if (y < 1.8) c.copy(YARD);
        else c.copy(edge[k] ? SLATE_EDGE : SLATE);
        var a0 = ao[k];
        var cr = clamp((c.r + grain) * a0, 0, 1), cg = clamp((c.g + grain) * a0, 0, 1),
            cb = clamp((c.b + grain * 0.7) * a0, 0, 1);
        if (edge[k]) {
          push(x0, y, zz0, x0, y, zz1, x1, y, zz0, cr, cg, cb, 0, 1, 0);
          push(x1, y, zz0, x0, y, zz1, x1, y, zz1, cr, cg, cb, 0, 1, 0);
        }

        // vertical skirts down to any lower neighbour
        wc.copy(isVeg ? VEG_LO : WALL);
        var wr = clamp(wc.r + grain, 0, 1), wg = clamp(wc.g + grain, 0, 1),
            wb = clamp(wc.b + grain, 0, 1);
        var e = at(r, q + 1);
        if (q < cols - 1 && y - e > BREAK) {
          push(x1, y, zz0, x1, y, zz1, x1, e, zz0, wr, wg, wb, 1, 0, 0);
          push(x1, e, zz0, x1, y, zz1, x1, e, zz1, wr, wg, wb, 1, 0, 0);
        }
        var w2 = at(r, q - 1);
        if (q > 0 && y - w2 > BREAK) {
          push(x0, y, zz1, x0, y, zz0, x0, w2, zz1, wr, wg, wb, -1, 0, 0);
          push(x0, w2, zz1, x0, y, zz0, x0, w2, zz0, wr, wg, wb, -1, 0, 0);
        }
        var s = at(r + 1, q);
        if (r < rows - 1 && y - s > BREAK) {
          push(x0, y, zz1, x1, y, zz1, x0, s, zz1, wr, wg, wb, 0, 0, 1);
          push(x0, s, zz1, x1, y, zz1, x1, s, zz1, wr, wg, wb, 0, 0, 1);
        }
        var nn = at(r - 1, q);
        if (r > 0 && y - nn > BREAK) {
          push(x1, y, zz0, x0, y, zz0, x1, nn, zz0, wr, wg, wb, 0, 0, -1);
          push(x1, nn, zz0, x0, y, zz0, x0, nn, zz0, wr, wg, wb, 0, 0, -1);
        }
      }
    }

    // Continuous roof/ground interiors. Quads spanning a wall are skipped;
    // the edge caps and vertical faces above fill those discontinuities.
    for (r = 0; r < rows - 1; r++) {
      for (q = 0; q < cols - 1; q++) {
        var xe = W + q + 0.5, xw = xe + 1, nc = N - r - 0.5,
            zn = -nc, zs = zn + 1;
        if (xw < -105 || xe > 85 || nc < -82 || nc > 74) continue;
        if (pointInRing(GEO.home.ring, xe, nc) ||
            pointInRing(GEO.home.garden_ring, xe, nc) ||
            inReceptorDetail(xe, nc)) continue;
        var h00 = at(r, q), h10 = at(r, q + 1), h01 = at(r + 1, q), h11 = at(r + 1, q + 1);
        if (veg[r * cols + q] || veg[r * cols + q + 1] ||
            veg[(r + 1) * cols + q] || veg[(r + 1) * cols + q + 1]) continue;
        var hi = Math.max(h00, h10, h01, h11), lo = Math.min(h00, h10, h01, h11);
        if (hi - lo > BREAK) continue;
        var av = (h00 + h10 + h01 + h11) / 4;
        if (onCarriageway(xe, nc, av)) continue;
        if (av < 0.9) c.copy(GROUND);
        else if (av < 1.8) c.copy(YARD);
        else c.copy(SLATE).offsetHSL(0, 0, (hash(r + ":" + q) - 0.5) * 0.04);
        var aI = (ao[r * cols + q] + ao[r * cols + q + 1] +
                  ao[(r + 1) * cols + q] + ao[(r + 1) * cols + q + 1]) / 4;
        var sr = clamp(c.r * aI, 0, 1), sg = clamp(c.g * aI, 0, 1), sb = clamp(c.b * aI, 0, 1);
        push(xe, h00, zn, xe, h01, zs, xw, h10, zn, sr, sg, sb, 0, 1, 0);
        push(xw, h10, zn, xe, h01, zs, xw, h11, zs, sr, sg, sb, 0, 1, 0);
      }
    }
    var g2 = new THREE.BufferGeometry();
    g2.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g2.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    g2.computeVertexNormals();
    var mesh = new THREE.Mesh(g2, new THREE.MeshStandardMaterial({
      vertexColors: true, roughness: 0.95, metalness: 0.0
    }));
    // Casting a shadow from every one-metre cell created a black checkerboard
    // on pitched roofs. The surface receives the real scene lighting; only the
    // highlighted home, outlets and furniture cast detailed shadows.
    mesh.castShadow = false;
    mesh.receiveShadow = true;
    scene.add(mesh);
    // Convert contiguous rough LiDAR patches into low-poly tree crowns. This
    // preserves useful vegetation context without pretending a last-return
    // canopy is a vertical-walled building.
    var seen = new Uint8Array(n), crowns = [];
    for (i = 0; i < n; i++) {
      if (!veg[i] || seen[i]) continue;
      var todo = [i], count = 0, sumR = 0, sumQ = 0, maxH = 0;
      seen[i] = 1;
      while (todo.length) {
        var kk = todo.pop(), rr = Math.floor(kk / cols), qq = kk % cols;
        count++; sumR += rr; sumQ += qq; maxH = Math.max(maxH, h[kk]);
        var near = [kk - 1, kk + 1, kk - cols, kk + cols];
        for (var ni = 0; ni < 4; ni++) {
          var nk = near[ni];
          if (nk < 0 || nk >= n || seen[nk] || !veg[nk]) continue;
          if ((ni === 0 && qq === 0) || (ni === 1 && qq === cols - 1)) continue;
          seen[nk] = 1; todo.push(nk);
        }
      }
      if (count < 5) continue;   // drop single-cell speckle
      var avgR = sumR / count, avgQ = sumQ / count;
      var tx = W + avgQ + 0.5, tn = N - avgR - 0.5;
      if (tx < -105 || tx > 85 || tn < -82 || tn > 74) continue;
      // Crowns used to be suppressed across the whole central block, which threw
      // away the nine nearest trees - the County Street planting among them - and
      // left that ground reading as empty rather than as trees. The only place a
      // LiDAR crown is unwanted is over the roof garden, where the nineteen named
      // specimens are drawn explicitly from the 1:50 plan.
      if (pointInRing(GEO.home.garden_ring || GEO.home.ring, tx, tn) ||
          pointInRing(GEO.home.ring, tx, tn) || inReceptorDetail(tx, tn)) continue;
      var jx = hash("tjx" + i) - 0.5, jz = hash("tjz" + i) - 0.5;
      crowns.push({ x: tx + jx * 1.5, z: -tn + jz * 1.5, h: maxH, seed: hash("t" + i),
                    radius: clamp(Math.sqrt(count) * 0.32, 0.9, 3.6) });
    }
    if (crowns.length) {
      var crownMesh = new THREE.InstancedMesh(
        new THREE.IcosahedronGeometry(1, 1),
        new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.94, flatShading: true }),
        crowns.length
      );
      var crownDummy = new THREE.Object3D(); crownMesh.frustumCulled = false;
      // Identical spheres on a 1 m grid read as a hedge of lollipops. Vary hue,
      // squash and tilt per crown so a street tree looks like a street tree.
      var crownCol = new THREE.Color();
      crowns.forEach(function (tree, ci) {
        var sd = tree.seed;
        crownDummy.position.set(tree.x, Math.max(1.4, tree.h - tree.radius * (0.5 + sd * 0.25)), tree.z);
        crownDummy.scale.set(tree.radius * (0.82 + sd * 0.4),
                             tree.radius * (0.72 + hash("ty" + ci) * 0.62),
                             tree.radius * (0.82 + hash("tz" + ci) * 0.4));
        crownDummy.rotation.set((sd - 0.5) * 0.34, sd * Math.PI * 2, (hash("tr" + ci) - 0.5) * 0.34);
        crownDummy.updateMatrix(); crownMesh.setMatrixAt(ci, crownDummy.matrix);
        crownCol.setHSL(0.24 + sd * 0.055, 0.26 + sd * 0.16, 0.20 + hash("tl" + ci) * 0.13);
        crownMesh.setColorAt(ci, crownCol);
      });
      if (crownMesh.instanceColor) crownMesh.instanceColor.needsUpdate = true;
      crownMesh.castShadow = false; crownMesh.receiveShadow = true;
      scene.add(crownMesh);
      // A crown floating on nothing reads as a bush on a stick only once you
      // give it the stick.
      var trunkMesh = new THREE.InstancedMesh(
        new THREE.CylinderGeometry(0.10, 0.16, 1, 6),
        new THREE.MeshStandardMaterial({ color: 0x4a3a2c, roughness: 0.95 }),
        crowns.length
      );
      trunkMesh.frustumCulled = false; trunkMesh.castShadow = true;
      var td = new THREE.Object3D();
      crowns.forEach(function (tree, ci) {
        var cy = Math.max(1.4, tree.h - tree.radius * (0.5 + tree.seed * 0.25));
        var base = Math.max(0.4, cy - tree.radius * 0.75);
        td.position.set(tree.x, base / 2, tree.z);
        td.scale.set(tree.radius * 0.34, base, tree.radius * 0.34);
        td.updateMatrix(); trunkMesh.setMatrixAt(ci, td.matrix);
      });
      scene.add(trunkMesh);
    }
    window.__lidarMesh = mesh;
    window.__lidarTris = pos.length / 9;
  })();

  /* --------------------------------------------------------------- buildings */
  var bldGroup = new THREE.Group();
  scene.add(bldGroup);
  var ROLE_TINT = {
    context: 0x8a7360, new_kent: 0x966b4a, new_kent_rear: 0x845f45,
    county_street: 0x8d7462, county_street_flat: 0x7a7670
  };
  var litMaterials = [];  // buildings that gain window light after dark
  // Always draw the OSM/photo-informed houses in the central block. LiDAR is
  // retained beyond it for measured skyline and height context.
  bldGroup.visible = true;

  function patternTexture(kind) {
    var c = document.createElement("canvas"); c.width = c.height = 128;
    var x = c.getContext("2d");
    if (kind === "brick") {
      // Stretcher-bond grain only. Openings are not painted here: ExtrudeGeometry
      // UVs are not in world units, so a window tile would come out at a
      // meaningless size on every wall. Fine masonry reads as brick without
      // inventing architecture.
      x.fillStyle = "#8a7a6a"; x.fillRect(0, 0, 128, 128);
      var rowH = 8, brickW = 16;
      for (var by = 0; by < 128; by += rowH) {
        var off = ((by / rowH) % 2) * (brickW / 2);
        for (var bx = -brickW; bx < 128 + brickW; bx += brickW) {
          var bg = 132 + Math.random() * 38;
          x.fillStyle = "rgb(" + Math.round(bg) + "," + Math.round(bg - 14) + "," + Math.round(bg - 28) + ")";
          x.fillRect(bx + off + 1, by + 1, brickW - 2, rowH - 2);
        }
      }
      x.fillStyle = "rgba(255,255,255,0.04)";
      for (var bi = 0; bi < 400; bi++) x.fillRect(Math.random() * 128, Math.random() * 128, 1, 1);
    } else {
      x.fillStyle = "#c2c3c5"; x.fillRect(0, 0, 128, 128);
      x.strokeStyle = "rgba(15,18,22,.38)"; x.lineWidth = 2;
      for (var yy = 0; yy <= 128; yy += 18) {
        x.beginPath(); x.moveTo(0, yy); x.lineTo(128, yy); x.stroke();
      }
      for (var xxx = 0; xxx <= 128; xxx += 32) {
        x.beginPath(); x.moveTo(xxx, 0); x.lineTo(xxx - 14, 128); x.stroke();
      }
    }
    var t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(kind === "brick" ? 3 : 2, kind === "brick" ? 3 : 2);
    t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    return t;
  }
  var BRICK_TEX = patternTexture("brick"), ROOF_TEX = patternTexture("roof");
  function windowTexture() {
    var c = document.createElement("canvas"); c.width = 64; c.height = 80;
    var x = c.getContext("2d");
    x.fillStyle = "#a79d90"; x.fillRect(0, 0, 64, 80);
    x.fillStyle = "#2b3f4d"; x.fillRect(7, 7, 50, 66);
    x.fillStyle = "rgba(190,220,235,.30)"; x.fillRect(10, 10, 22, 28);
    x.fillRect(35, 40, 19, 30);
    x.fillStyle = "#9d9487"; x.fillRect(30, 7, 4, 66); x.fillRect(7, 38, 50, 4);
    var t = new THREE.CanvasTexture(c); t.minFilter = THREE.LinearFilter; return t;
  }
  var windowMat = new THREE.MeshStandardMaterial({
    color: 0xffffff, map: windowTexture(), roughness: 0.34, metalness: 0.08,
    emissive: 0xffc477, emissiveIntensity: 0.0
  });
  function visualRing(b) {
    // OSM joins 74–75 to the western neighbour. The supplied passage photo
    // shows a genuine open gap towards County Street, so pull that neighbour's
    // eastern wall back while preserving the source footprint in the JSON.
    if (String(b.osm_id) === "456076840") {
      return [
        [-4.55, 0.85], [-16.01, -0.78], [-15.84, -1.62],
        [-12.02, -17.98], [-2.24, -16.57], [-3.20, -12.70],
        [-3.55, -10.40]
      ];
    }
    return b.ring;
  }
  // The LiDAR shell is suppressed across the central block and drawn everywhere
  // else. Extruding the OSM footprints everywhere therefore double-built the
  // outer ring: two copies of the same terrace, with the procedural windows
  // floating clear of whichever surface lost the depth test.
  function inCentralBlock(ring) {
    var ex = 0, nn = 0, k;
    for (k = 0; k < ring.length; k++) { ex += ring[k][0]; nn += ring[k][1]; }
    ex /= ring.length; nn /= ring.length;
    return ex > -68 && ex < 58 && nn > -41 && nn < 31;
  }
  GEO.buildings.forEach(function (b) {
    if (LID && !inCentralBlock(b.ring)) return;
    if (ringArea(b.ring) < 6) return;
    var replaced = (GEO.receptor_homes || []).some(function (home) {
      return home.replace_osm_walls && String(home.osm_id) === String(b.osm_id);
    });
    if (replaced) return;
    var jitter = hash(b.osm_id + b.role);
    var base = new THREE.Color(ROLE_TINT[b.role] || 0x6d6a68);
    base.offsetHSL((jitter - 0.5) * 0.03, (jitter - 0.5) * 0.05, (jitter - 0.5) * 0.10);
    var eaves = b.eaves_m || 7;
    var displayRing = visualRing(b);
    // Clean division of labour: the OSM footprint supplies WALLS, extruded to
    // the lowest roof plateau in the LiDAR, and the measured surface supplies
    // every roof above that. A single box drawn to the median roof swallowed
    // real setbacks - the neighbouring terrace at 12.6 m sat 1.7 m inside a
    // block whose median roof is 15.3 m, taking its monitor with it.
    var wallTop = Math.max(1.0, (b.wall_top_m || eaves) - 0.15);
    var geo = new THREE.ExtrudeGeometry(shapeFrom(displayRing),
              { depth: wallTop, bevelEnabled: false });
    geo.rotateX(-Math.PI / 2);
    var m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color: base, map: BRICK_TEX, roughness: 0.88, metalness: 0.02
    }));
    m.castShadow = true; m.receiveShadow = true;
    m.userData.role = b.role;
    bldGroup.add(m);
    m.material.emissive = new THREE.Color(0xffbe72);
    m.material.emissiveIntensity = 0;
    litMaterials.push({ mat: m.material, w: 0.25 + jitter * 0.85 });

    // The pyramidal ridge cap and the flat-roof slab that used to be drawn here
    // are gone. Both were inventions - a pyramid from the footprint centroid is
    // not what a terrace roof looks like - and the measured 1 m surface now
    // draws every roof, including the pitches, hips and setbacks that are
    // actually there.

    // Generic windows used to be separate box meshes tacked onto each facade.
    // They repeatedly ended up detached - floating over the carriageway with
    // their own shadows - because the OSM massing and the LiDAR massing describe
    // the same terraces slightly differently and the boxes survived whichever
    // surface lost. They were invented detail rather than evidence, so the
    // facade pattern now lives in the wall texture, where it cannot come loose.
  });

  /* ------------------------------------------------ photo-informed host homes
   * The instrumented homes deserve more than anonymous context massing. OSM
   * and LiDAR still control footprint and level; the 19 August installation
   * photographs control the recognisable material language and mount context.
   * These details communicate the site but are not photogrammetry or a survey.
   */
  var receptorGroup = new THREE.Group();
  scene.add(receptorGroup);
  (function () {
    var pale = new THREE.MeshStandardMaterial({ color: 0xd8d0c3, roughness: 0.88 });
    var white = new THREE.MeshStandardMaterial({ color: 0xe3e2dc, roughness: 0.82 });
    var lightBrick = new THREE.MeshStandardMaterial({
      color: 0xbda98e, map: BRICK_TEX, roughness: 0.91
    });
    var roof = new THREE.MeshStandardMaterial({ color: 0x414449, map: ROOF_TEX, roughness: 0.82 });
    var deckMat = new THREE.MeshStandardMaterial({ color: 0x80766b, roughness: 0.93 });
    var timber = new THREE.MeshStandardMaterial({ color: 0x75543a, roughness: 0.91 });
    var bamboo = new THREE.MeshStandardMaterial({ color: 0x9b743b, roughness: 0.90 });
    var metal = new THREE.MeshStandardMaterial({ color: 0x626970, roughness: 0.43, metalness: 0.55 });
    var glass = new THREE.MeshPhysicalMaterial({
      color: 0x93b6c4, roughness: 0.12, transparent: true, opacity: 0.30,
      side: THREE.DoubleSide, depthWrite: false
    });
    var solar = new THREE.MeshStandardMaterial({
      color: 0x172b3b, roughness: 0.28, metalness: 0.45
    });
    var solarLine = new THREE.MeshStandardMaterial({ color: 0xb7c3ca, roughness: 0.36, metalness: 0.70 });
    var soffit = new THREE.MeshStandardMaterial({ color: 0xc6c3bc, roughness: 0.94 });
    // Roof-level concrete decks. The warm deckMat reads as terrain from above,
    // so a paved deck four storeys up simply vanished into the ground behind it.
    var paving = new THREE.MeshStandardMaterial({ color: 0xa8a49b, roughness: 0.95 });
    var planterMat = new THREE.MeshStandardMaterial({ color: 0x3d4140, roughness: 0.88 });
    var foliage = new THREE.MeshStandardMaterial({ color: 0x557244, roughness: 0.95 });
    var windowGlass = new THREE.MeshStandardMaterial({
      color: 0x263d4b, roughness: 0.18, metalness: 0.18,
      emissive: 0xffbd73, emissiveIntensity: 0.0
    });
    litMaterials.push({ mat: windowGlass, w: 0.88 });

    function extrude(group, ring, base, top, material) {
      var g = new THREE.ExtrudeGeometry(shapeFrom(ring), {
        depth: Math.max(top - base, 0.05), bevelEnabled: false
      });
      g.rotateX(-Math.PI / 2);
      var mesh = new THREE.Mesh(g, material);
      mesh.position.y = base;
      mesh.castShadow = true; mesh.receiveShadow = true;
      group.add(mesh);
      return mesh;
    }
    function beam(group, a, b, y, height, depth, material) {
      var dx = b[0] - a[0], dz = -(b[1] - a[1]), length = Math.hypot(dx, dz) || 0.01;
      var mesh = new THREE.Mesh(new THREE.BoxGeometry(length, height, depth), material);
      mesh.position.set((a[0] + b[0]) / 2, y, -(a[1] + b[1]) / 2);
      mesh.rotation.y = -Math.atan2(dz, dx);
      mesh.castShadow = true; mesh.receiveShadow = true;
      group.add(mesh);
      return mesh;
    }
    function vertical(group, e, n, base, top, radius, material, sides) {
      var mesh = new THREE.Mesh(
        new THREE.CylinderGeometry(radius, radius * 1.04, top - base, sides || 8), material
      );
      mesh.position.set(e, (base + top) / 2, -n);
      mesh.castShadow = true; group.add(mesh);
      return mesh;
    }
    function facadeWindow(group, e, n, y, width, height, yaw, frameColour) {
      var frame = new THREE.MeshStandardMaterial({ color: frameColour, roughness: 0.72 });
      var g = new THREE.Group(); g.position.set(e, y, -n); g.rotation.y = yaw || 0;
      var backing = new THREE.Mesh(new THREE.BoxGeometry(width + 0.18, height + 0.18, 0.10), frame);
      var pane = new THREE.Mesh(new THREE.BoxGeometry(width, height, 0.055), windowGlass);
      pane.position.z = 0.065; g.add(backing); g.add(pane);
      var mullion = new THREE.Mesh(new THREE.BoxGeometry(0.055, height, 0.075), frame);
      mullion.position.z = 0.10; g.add(mullion);
      var transom = new THREE.Mesh(new THREE.BoxGeometry(width, 0.055, 0.075), frame);
      transom.position.set(0, height * 0.12, 0.10); g.add(transom);
      g.traverse(function (o) { if (o.isMesh) o.castShadow = true; });
      group.add(g);
    }
    function gableRoof(group, cx, north, width, depth, eaves, rise, yaw) {
      var slope = Math.hypot(depth / 2, rise), angle = Math.atan2(rise, depth / 2);
      var holder = new THREE.Group();
      holder.position.set(cx, 0, -north); holder.rotation.y = yaw || 0;
      [-1, 1].forEach(function (side) {
        var panel = new THREE.Mesh(new THREE.BoxGeometry(width, 0.12, slope), roof);
        panel.position.set(0, eaves + rise / 2, side * depth / 4);
        panel.rotation.x = side * angle;
        panel.castShadow = true; panel.receiveShadow = true;
        holder.add(panel);
      });
      [-1, 1].forEach(function (end) {
        var x = end * width / 2;
        var vertices = end < 0
          ? [x, eaves, -depth / 2, x, eaves, depth / 2, x, eaves + rise, 0]
          : [x, eaves, -depth / 2, x, eaves + rise, 0, x, eaves, depth / 2];
        var geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
        geometry.computeVertexNormals();
        var gable = new THREE.Mesh(geometry, lightBrick);
        gable.castShadow = true; gable.receiveShadow = true; holder.add(gable);
      });
      group.add(holder);
    }
    function solarPanel(group, cfg, deckY) {
      var g = new THREE.Group();
      g.position.set(cfg.centre[0], deckY + 0.48, -cfg.centre[1]);
      g.rotation.y = (cfg.yaw_deg || 0) * Math.PI / 180;
      g.rotation.x = -0.24;
      var panel = new THREE.Mesh(new THREE.BoxGeometry(cfg.width_m, 0.07, cfg.depth_m), solar);
      panel.castShadow = true; g.add(panel);
      for (var i = -1; i <= 1; i++) {
        var rib = new THREE.Mesh(new THREE.BoxGeometry(0.018, 0.018, cfg.depth_m * 0.98), solarLine);
        rib.position.set(i * cfg.width_m / 3, 0.048, 0); g.add(rib);
      }
      for (var j = -1; j <= 1; j += 2) {
        var leg = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.58, 0.07), metal);
        leg.position.set(j * cfg.width_m * 0.32, -0.24, cfg.depth_m * 0.22); g.add(leg);
      }
      group.add(g);
    }
    function bambooScreen(group, cfg) {
      var a = cfg.from, b = cfg.to, count = Math.max(10, Math.round(Math.hypot(b[0] - a[0], b[1] - a[1]) / 0.105));
      for (var i = 0; i <= count; i++) {
        var f = i / count;
        vertical(group, lerp(a[0], b[0], f), lerp(a[1], b[1], f),
                 cfg.base_m, cfg.top_m - (i % 3) * 0.035, 0.035, bamboo, 7);
      }
      beam(group, a, b, cfg.base_m + 0.48, 0.065, 0.085, timber);
      beam(group, a, b, cfg.top_m - 0.32, 0.065, 0.085, timber);
      vertical(group, a[0], a[1], cfg.base_m, cfg.top_m, 0.065, timber, 8);
      vertical(group, b[0], b[1], cfg.base_m, cfg.top_m, 0.065, timber, 8);
    }
    function glassRail(group, cfg) {
      var a = cfg.from, b = cfg.to, spans = 7;
      beam(group, a, b, cfg.top_m, 0.075, 0.075, metal);
      beam(group, a, b, cfg.base_m + 0.62, 0.045, 0.055, metal);
      for (var i = 0; i <= spans; i++) {
        var f = i / spans;
        vertical(group, lerp(a[0], b[0], f), lerp(a[1], b[1], f),
                 cfg.base_m, cfg.top_m, 0.027, metal, 8);
      }
      for (var j = 0; j < spans; j++) {
        var f0 = (j + 0.08) / spans, f1 = (j + 0.92) / spans;
        var p0 = [lerp(a[0], b[0], f0), lerp(a[1], b[1], f0)];
        var p1 = [lerp(a[0], b[0], f1), lerp(a[1], b[1], f1)];
        var panel = beam(group, p0, p1, cfg.base_m + 0.88, 0.82, 0.018, glass);
        panel.castShadow = false;
      }
    }
    function barRail(group, cfg) {
      var a = cfg.from, b = cfg.to;
      var length = Math.hypot(b[0] - a[0], b[1] - a[1]);
      var bars = Math.max(3, Math.round(length / 0.18));
      beam(group, a, b, cfg.top_m, 0.075, 0.075, metal);
      beam(group, a, b, cfg.base_m + 0.08, 0.08, 0.075, metal);
      for (var i = 0; i <= bars; i++) {
        var f = i / bars;
        vertical(group, lerp(a[0], b[0], f), lerp(a[1], b[1], f),
                 cfg.base_m + 0.06, cfg.top_m, 0.018, metal, 8);
      }
    }
    function planter(group, e, n, y, scale, seed) {
      var pot = new THREE.Mesh(new THREE.BoxGeometry(scale, 0.48, scale * 0.72), planterMat);
      pot.position.set(e, y + 0.24, -n); pot.castShadow = true; group.add(pot);
      var crown = new THREE.Mesh(new THREE.IcosahedronGeometry(scale * 0.58, 1), foliage.clone());
      crown.material.color.offsetHSL((seed - 0.5) * 0.03, 0, (seed - 0.5) * 0.08);
      crown.position.set(e, y + 0.78 + scale * 0.28, -n);
      crown.scale.set(0.85, 1.35, 0.85); crown.castShadow = true; group.add(crown);
    }

    (GEO.receptor_homes || []).forEach(function (home) {
      var group = new THREE.Group(); group.userData.receptorHome = home.key;
      receptorGroup.add(group);
      if (home.replace_osm_walls) extrude(group, home.ring, 0, home.wall_top_m, lightBrick);
      var deckSurface = home.deck_style === "paving" ? paving : deckMat;
      if (home.terrace_ring) extrude(group, home.terrace_ring, home.deck_m, home.deck_m + 0.13, deckSurface);
      if (home.balcony_ring) extrude(group, home.balcony_ring, home.deck_m, home.deck_m + 0.13, deckSurface);
      if (home.upper_garden_ring) {
        extrude(group, home.upper_garden_ring, home.lower_deck_m, home.deck_m, lightBrick);
        extrude(group, home.upper_garden_ring, home.deck_m, home.deck_m + 0.15, deckMat);
      }
      if (home.lower_solar_ring) extrude(group, home.lower_solar_ring, home.lower_deck_m, home.lower_deck_m + 0.13, roof);
      (home.upper_volumes || []).forEach(function (volume) {
        var material = volume.style === "light_brick" ? lightBrick :
                       volume.style === "white_render" ? white : pale;
        extrude(group, volume.ring, volume.base_m, volume.top_m, material);
        extrude(group, volume.ring, volume.top_m, volume.top_m + 0.09, roof);
      });

      if (home.key === "intervening_neighbour_76") {
        gableRoof(group, -9.80, -4.25, 10.65, 7.55, 12.45, 1.25, -0.12);
        beam(group, [-12.10, -17.45], [-2.70, -16.10], home.deck_m + 0.38, 0.66, 0.22, lightBrick);
        facadeWindow(group, -9.45, -17.57, 5.55, 1.55, 1.85, 0.14, 0x8a6848);
        facadeWindow(group, -6.75, -17.18, 5.55, 1.55, 1.85, 0.14, 0x8a6848);
        facadeWindow(group, -11.25, -9.10, 10.15, 1.70, 1.60, 0.14, 0x8a6848);
        planter(group, -3.55, -15.35, home.deck_m, 0.90, 0.22);
        planter(group, -4.75, -15.50, home.deck_m, 0.72, 0.66);
      } else if (home.key === "home_81a") {
        (home.bamboo_screens || [home.bamboo_screen]).filter(Boolean).forEach(function (cfg) {
          bambooScreen(group, cfg);
        });
        (home.solar_panels || []).forEach(function (cfg) { solarPanel(group, cfg, home.lower_deck_m); });
        if (home.gable) {
          gableRoof(group, home.gable.centre[0], home.gable.centre[1],
                    home.gable.width_m, home.gable.depth_m, home.gable.eaves_m,
                    home.gable.rise_m, home.gable.yaw_deg * Math.PI / 180);
        }
        // The garden deck sits above the house eaves, so the blank gabled flank
        // wall is what the terrace actually looks at - no window reads from here.
        planter(group, -19.40, -11.10, home.deck_m, 0.80, 0.36);
        planter(group, -18.00, -10.45, home.deck_m, 0.66, 0.71);
        planter(group, -16.75, -10.35, home.deck_m, 0.72, 0.54);
        planter(group, -19.05, -12.30, home.deck_m, 0.55, 0.19);
      } else if (home.key === "western_private_home") {
        glassRail(group, home.glass_balustrade);
        // Windows face south off both render blocks onto the private terrace.
        // yaw = 180 - facade bearing, so a 176 degree wall reads as 0.068 rad.
        facadeWindow(group, -48.87, -9.30, 13.60, 1.50, 1.50, 0.068, 0x7a5537);
        facadeWindow(group, -46.80, -9.16, 13.60, 1.50, 1.50, 0.068, 0x7a5537);
        facadeWindow(group, -43.43, -8.53, 13.42, 1.35, 1.30, 0.068, 0x7a5537);
        facadeWindow(group, -41.34, -8.39, 13.42, 1.35, 1.30, 0.068, 0x7a5537);
        planter(group, -48.60, -11.20, home.deck_m, 0.85, 0.36);
        planter(group, -46.60, -11.00, home.deck_m, 0.70, 0.74);
        planter(group, -44.60, -10.85, home.deck_m, 0.62, 0.53);
        planter(group, -42.60, -10.70, home.deck_m, 0.55, 0.21);
        var privateLight = new THREE.Mesh(new THREE.SphereGeometry(0.16, 16, 10),
          new THREE.MeshStandardMaterial({ color: 0xd9d4c8, emissive: 0xffcf8e, emissiveIntensity: 0.18 }));
        privateLight.position.set(-47.85, 13.95, 9.24); group.add(privateLight);
      } else if (home.key === "communal_balcony") {
        barRail(group, home.metal_balustrade);
        (home.safety_rails || []).forEach(function (cfg) { barRail(group, cfg); });
        // The gallery reads as a gallery because of the deep concrete soffit over
        // it and the flats it serves behind - without those it is a rail on a slab.
        if (home.canopy) {
          extrude(group, home.canopy.ring, home.canopy.base_m, home.canopy.top_m, soffit);
        }
        facadeWindow(group, -48.17, -15.15, 13.50, 1.05, 1.45, -3.074, 0x8f8b83);
        facadeWindow(group, -45.88, -14.99, 13.50, 1.05, 1.45, -3.074, 0x8f8b83);
        facadeWindow(group, -43.58, -14.84, 13.50, 1.05, 1.45, -3.074, 0x8f8b83);
        var wallLight = new THREE.Mesh(new THREE.CylinderGeometry(0.19, 0.19, 0.11, 14),
          new THREE.MeshStandardMaterial({ color: 0xd9d4c8, emissive: 0xffcf8e, emissiveIntensity: 0.20 }));
        wallLight.rotation.x = Math.PI / 2;
        wallLight.position.set(-46.90, 14.30, 15.02); group.add(wallLight);
        planter(group, -47.80, -14.25, home.deck_m, 0.55, 0.28);
        planter(group, -44.60, -14.05, home.deck_m, 0.62, 0.61);
        planter(group, -42.30, -13.90, home.deck_m, 0.50, 0.44);
      }
    });

    // The final 1/4 pair is visible in the photograph on a shared timber
    // cross-rail immediately beside Tempest. Draw the real mounting context,
    // not two invented freestanding masts.
    var installed = GEO.monitor_states && GEO.monitor_states.deployment && GEO.monitor_states.deployment.positions;
    if (installed && installed["1"] && installed["4"]) {
      var p1 = installed["1"], p4 = installed["4"];
      var a = [p1.east_m - 0.42, p1.north_m - 0.05];
      var b = [p4.east_m + 0.42, p4.north_m + 0.05];
      beam(receptorGroup, a, b, p1.height_m - 0.02, 0.16, 0.12, timber);
      [a, b].forEach(function (p) {
        vertical(receptorGroup, p[0], p[1], GEO.heights.garden_deck_m,
                 p1.height_m + 0.18, 0.045, timber, 8);
      });
    }
  })();

  /* --------------------------------------------------------------- chimneys
   * Every photograph of this roofscape is full of brick stacks with pots on
   * them, and without those the terrace row reads as a line of blank wedges.
   * Placed on the ridge line of anything that has one, deterministic per
   * building so the skyline does not reshuffle on reload.
   */
  (function () {
    var brick = new THREE.MeshStandardMaterial({ color: 0x6b4f43, roughness: 0.95 });
    var pot = new THREE.MeshStandardMaterial({ color: 0x8d5a3f, roughness: 0.9 });
    var stacks = [], pots = [];
    GEO.buildings.forEach(function (b) {
      if (!b.ridge_m || ringArea(b.ring) < 20) return;
      var j = hash("chim" + b.osm_id + b.ring.length);
      if (j > 0.82) return;
      var cx = 0, cz = 0, i;
      for (i = 0; i < b.ring.length; i++) { cx += b.ring[i][0]; cz += b.ring[i][1]; }
      cx /= b.ring.length; cz /= b.ring.length;
      // slide it along the ridge so they are not all dead centre
      var a = b.ring[0], c = b.ring[1];
      var dx = c[0] - a[0], dz = c[1] - a[1], L = Math.hypot(dx, dz) || 1;
      var off = (j - 0.5) * Math.min(L * 0.55, 4.5);
      var x = cx + dx / L * off, z = cz + dz / L * off;
      var hgt = 1.0 + j * 1.1;
      stacks.push({ x: x, z: z, y: b.ridge_m + hgt / 2 - 0.15, h: hgt, w: 0.75 + j * 0.5 });
      var np = 2 + Math.round(j * 2);
      for (i = 0; i < np; i++) {
        pots.push({ x: x + (i - (np - 1) / 2) * 0.34, z: z, y: b.ridge_m + hgt + 0.12 });
      }
    });
    var d = new THREE.Object3D();
    if (LID) return;  // real stacks are already in the LIDAR surface
    var sm = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), brick, stacks.length);
    sm.castShadow = true; sm.receiveShadow = true; sm.frustumCulled = false;
    stacks.forEach(function (s, i) {
      d.position.set(s.x, s.y, -s.z);
      d.scale.set(s.w, s.h, s.w * 0.62);
      d.rotation.set(0, 0, 0); d.updateMatrix();
      sm.setMatrixAt(i, d.matrix);
    });
    scene.add(sm);
    var pm = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.13, 0.15, 0.42, 8), pot, pots.length);
    pm.castShadow = true; pm.frustumCulled = false;
    pots.forEach(function (q, i) {
      d.position.set(q.x, q.y, -q.z); d.scale.set(1, 1, 1); d.updateMatrix();
      pm.setMatrixAt(i, d.matrix);
    });
    scene.add(pm);
  })();

  /* ------------------------------------------------------- after-dark lighting
   * The scene is about evenings, so it has to stay readable once the sun has
   * gone. Rather than pay for real point lights, the street is lit with emissive
   * lamp heads plus additive glow discs on the carriageway, and buildings pick
   * up a warm emissive tint standing in for lit windows.
   */
  var lampGroup = new THREE.Group();
  scene.add(lampGroup);
  var lampHeads = [], lampPools = [];
  (function () {
    if (!nkr) return;
    var line = nkr.centreline, glow = softGlowTexture();
    for (var seg = 0; seg < line.length - 1; seg++) {
      for (var f = 0; f < 1; f += 0.34) {
        var a = line[seg], b = line[seg + 1];
        var ex = lerp(a[0], b[0], f), nn = lerp(a[1], b[1], f);
        var dx = b[0] - a[0], dn = b[1] - a[1], L = Math.hypot(dx, dn) || 1;
        var side = (seg + Math.round(f * 3)) % 2 ? 1 : -1;
        var ox = (-dn / L) * (nkr.half_width_m + 1.2) * side;
        var on = (dx / L) * (nkr.half_width_m + 1.2) * side;
        var post = new THREE.Mesh(
          new THREE.CylinderGeometry(0.09, 0.13, 8.0, 6),
          new THREE.MeshStandardMaterial({ color: 0x3b4045, roughness: 0.7, metalness: 0.4 })
        );
        post.position.set(ex + ox, 4.0, -(nn + on));
        lampGroup.add(post);
        var housing = new THREE.Mesh(
          new THREE.BoxGeometry(0.58, 0.16, 0.36),
          new THREE.MeshStandardMaterial({ color: 0x2a2e32, roughness: 0.45, metalness: 0.55 })
        );
        housing.position.set(ex + ox, 8.08, -(nn + on));
        lampGroup.add(housing);
        var head = new THREE.Mesh(
          new THREE.SphereGeometry(0.28, 8, 6),
          new THREE.MeshBasicMaterial({ color: 0xffd49a, transparent: true, opacity: 0.12 })
        );
        head.position.set(ex + ox, 7.96, -(nn + on));
        lampGroup.add(head);
        lampHeads.push(head);
        var pool = new THREE.Sprite(new THREE.SpriteMaterial({
          map: glow, color: 0xffc98a, transparent: true, opacity: 0,
          blending: THREE.AdditiveBlending, depthWrite: false
        }));
        pool.position.set(ex + ox * 0.3, 0.35, -(nn + on * 0.3));
        pool.scale.set(15, 15, 1);
        lampGroup.add(pool);
        lampPools.push(pool);
      }
    }
  })();

  function softGlowTexture() {
    var c = document.createElement("canvas"); c.width = c.height = 128;
    var x = c.getContext("2d");
    var g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
    g.addColorStop(0, "rgba(255,255,255,0.62)");
    g.addColorStop(0.4, "rgba(255,255,255,0.16)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    x.fillStyle = g; x.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  }

  function setNight(night) {
    var i;
    for (i = 0; i < litMaterials.length; i++) {
      litMaterials[i].mat.emissiveIntensity = night * 0.16 * litMaterials[i].w;
    }
    for (i = 0; i < lampHeads.length; i++) lampHeads[i].material.opacity = 0.14 + night * 0.86;
    for (i = 0; i < lampPools.length; i++) lampPools[i].material.opacity = night * 0.55;
    for (i = 0; i < windowMats.length; i++) {
      windowMats[i].emissiveIntensity = 0.06 + night * 0.72;
    }
  }

  var windowMats = [];

  /* -------------------------------------------------------------------- home */
  var H = GEO.heights;
  var homeGroup = new THREE.Group();
  scene.add(homeGroup);

  var shell = new THREE.Mesh(
    (function () { var g = new THREE.ExtrudeGeometry(shapeFrom(GEO.home.ring),
      { depth: H.garden_deck_m, bevelEnabled: false }); g.rotateX(-Math.PI / 2); return g; })(),
    new THREE.MeshStandardMaterial({ color: 0xd0b99e, map: BRICK_TEX, roughness: 0.85 })
  );
  shell.castShadow = true; shell.receiveShadow = true;
  homeGroup.add(shell);

  var deck = new THREE.Mesh(
    (function () { var g = new THREE.ExtrudeGeometry(shapeFrom(GEO.home.garden_ring),
      { depth: 0.12, bevelEnabled: false }); g.rotateX(-Math.PI / 2); return g; })(),
    new THREE.MeshStandardMaterial({ color: 0x9c5334, roughness: 0.9 })
  );
  deck.position.y = H.garden_deck_m;
  deck.receiveShadow = true;
  homeGroup.add(deck);

  // parapet as a thin wall following the garden ring
  (function () {
    var ring = GEO.home.garden_ring, i;
    var mat = new THREE.MeshStandardMaterial({ color: 0x7d7269, roughness: 0.9 });
    for (i = 0; i < ring.length; i++) {
      var a = ring[i], b = ring[(i + 1) % ring.length];
      var dx = b[0] - a[0], dz = -(b[1] - a[1]), L = Math.hypot(dx, dz);
      var w = new THREE.Mesh(new THREE.BoxGeometry(L, H.home_parapet_m - H.garden_deck_m, 0.30), mat);
      w.position.set((a[0] + b[0]) / 2, (H.garden_deck_m + H.home_parapet_m) / 2, -(a[1] + b[1]) / 2);
      w.rotation.y = Math.atan2(dz, dx) * -1;
      w.castShadow = true; w.receiveShadow = true;
      homeGroup.add(w);
    }
  })();

  // Greenhouse: glass walls and a real gabled roof rather than the former
  // translucent cuboid. The 1:50 plan fixes its footprint; photographs guide
  // the visual form.
  (function () {
    var r = GEO.home.greenhouse.ring;
    var wallH = 1.35, ridgeH = H.greenhouse_ridge_m - H.garden_deck_m;
    var g = new THREE.ExtrudeGeometry(shapeFrom(r), { depth: wallH, bevelEnabled: false });
    g.rotateX(-Math.PI / 2);
    var ghGlass = new THREE.MeshPhysicalMaterial({
      color: 0xa8d4dd, roughness: 0.10, metalness: 0.0,
      transparent: true, opacity: 0.36, transmission: 0.0, side: THREE.DoubleSide,
      emissive: 0xffc07a, emissiveIntensity: 0.04
    });
    var m = new THREE.Mesh(g, ghGlass);
    windowMats.push(ghGlass);
    m.position.y = H.garden_deck_m;
    homeGroup.add(m);

    var ra = [(r[0][0] + r[1][0]) / 2, (r[0][1] + r[1][1]) / 2];
    var rb = [(r[2][0] + r[3][0]) / 2, (r[2][1] + r[3][1]) / 2];
    var py = H.garden_deck_m + wallH, ry = H.garden_deck_m + ridgeH;
    var gp = [
      r[0][0],py,-r[0][1], r[3][0],py,-r[3][1], rb[0],ry,-rb[1],
      r[0][0],py,-r[0][1], rb[0],ry,-rb[1], ra[0],ry,-ra[1],
      r[1][0],py,-r[1][1], ra[0],ry,-ra[1], rb[0],ry,-rb[1],
      r[1][0],py,-r[1][1], rb[0],ry,-rb[1], r[2][0],py,-r[2][1],
      r[0][0],py,-r[0][1], ra[0],ry,-ra[1], r[1][0],py,-r[1][1],
      r[3][0],py,-r[3][1], r[2][0],py,-r[2][1], rb[0],ry,-rb[1]
    ];
    var gg = new THREE.BufferGeometry();
    gg.setAttribute("position", new THREE.Float32BufferAttribute(gp, 3));
    gg.computeVertexNormals();
    var roof = new THREE.Mesh(gg, new THREE.MeshPhysicalMaterial({
      color: 0xb9e0e6, roughness: 0.08, transparent: true, opacity: 0.46,
      side: THREE.DoubleSide
    }));
    roof.castShadow = true; homeGroup.add(roof);

    var frameMat = new THREE.MeshStandardMaterial({ color: 0x66737b, roughness: 0.45, metalness: 0.7 });
    [r[0], r[1], r[2], r[3], ra, rb].forEach(function (p, i) {
      var top = i < 4 ? py : ry;
      var post = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, top - H.garden_deck_m, 6), frameMat);
      post.position.set(p[0], (top + H.garden_deck_m) / 2, -p[1]); homeGroup.add(post);
    });
  })();

  // Side passage/courtyard towards County Street. OSM generalises the two
  // properties into touching footprints, while the photographs show a narrow
  // open gap. This explicit strip preserves that observed topology.
  (function () {
    var ax = -3.75, az = 0.30, bx = -2.85, bz = 9.80;
    var dx = bx - ax, dz = bz - az, length = Math.hypot(dx, dz);
    var angle = Math.atan2(dx, dz), cx = (ax + bx) / 2, cz = (az + bz) / 2;
    var nx = dz / length, nz = -dx / length, width = 1.35;
    var floor = new THREE.Mesh(
      new THREE.BoxGeometry(width, 0.08, length),
      new THREE.MeshStandardMaterial({ color: 0x777169, roughness: 0.96 })
    );
    floor.position.set(cx, 0.04, cz); floor.rotation.y = angle; homeGroup.add(floor);
    // Low kerbs keep the passage legible without rebuilding the tall walls
    // that previously hid it from every useful camera angle.
    var edgeMat = new THREE.MeshStandardMaterial({ color: 0xa99b88, roughness: 0.92 });
    [-1, 1].forEach(function (side) {
      var kerb = new THREE.Mesh(new THREE.BoxGeometry(0.10, 0.30, length), edgeMat);
      kerb.position.set(cx + nx * width * 0.5 * side, 0.15,
                        cz + nz * width * 0.5 * side);
      kerb.rotation.y = angle; homeGroup.add(kerb);
    });
    var door = new THREE.Mesh(
      new THREE.BoxGeometry(0.10, 2.05, 0.95),
      new THREE.MeshStandardMaterial({ color: 0x5d3928, roughness: 0.72 })
    );
    door.position.set(cx + nx * width * 0.55, 1.15, cz + nz * width * 0.55 + 1.1);
    door.rotation.y = angle; homeGroup.add(door);
    var bedroom = new THREE.Mesh(new THREE.BoxGeometry(0.10, 1.45, 1.30), windowMat);
    bedroom.position.set(cx + nx * width * 0.56, 5.80, cz + nz * width * 0.56 - 0.8);
    bedroom.rotation.y = angle; homeGroup.add(bedroom);
  })();

  // The stair up to the roof terrace, and the door off it.
  //
  // A door used to be placed loose against the west parapet at north -8.65 with
  // no stair behind it, which asserted an opening into nothing. The 1:50
  // EXISTING FIRST FLOOR AND ROOF PLAN (24/AP/1691) labels "Stairs" on the west
  // side, and registering that drawing against two features whose local
  // coordinates are already known - the skylight and the greenhouse - puts the
  // enclosure at east -1.26, north -5.12, 1.47 x 3.83 m, with the door on its
  // EAST face at about (-0.15, -6.36). The registration checks out: the implied
  // scale is 57.1 PDF units per metre against 56.7 expected for 1:50 on this
  // page, so the drawing is being read at its stated scale.
  (function () {
    // Nudged 0.13 m east of the registered centre so the enclosure sits fully
    // on the deck; the raw fix put its south-west corner 0.12 m past the west
    // parapet, which is inside the registration's own uncertainty.
    var cE = -1.13, cN = -5.12, w = 1.47, l = 3.83, hgt = 2.25;
    var deck = H.garden_deck_m;
    var box = new THREE.Mesh(
      new THREE.BoxGeometry(w, hgt, l),
      new THREE.MeshStandardMaterial({ color: 0xc0ab90, map: BRICK_TEX, roughness: 0.9 })
    );
    box.position.set(cE, deck + hgt / 2, -cN);
    box.castShadow = true; box.receiveShadow = true; homeGroup.add(box);
    var cap = new THREE.Mesh(
      new THREE.BoxGeometry(w + 0.16, 0.10, l + 0.16),
      new THREE.MeshStandardMaterial({ color: 0x6e7176, roughness: 0.95 })
    );
    cap.position.set(cE, deck + hgt + 0.05, -cN);
    cap.castShadow = true; homeGroup.add(cap);
    // Door in the SOUTH wall, toward its east end - that is where the plan's
    // swing arc sits, and the site owner confirms it. It was first read off the drawing as
    // an east-facing door, which put it on the wrong wall.
    var southN = cN - l / 2;
    var d = new THREE.Mesh(
      new THREE.BoxGeometry(0.88, 2.02, 0.06),
      new THREE.MeshStandardMaterial({ color: 0x5a3d2c, roughness: 0.75 })
    );
    d.position.set(cE + 0.22, deck + 1.01, -southN + 0.04);
    homeGroup.add(d);
    // a shallow threshold, so the door reads as an opening rather than a panel
    var step = new THREE.Mesh(
      new THREE.BoxGeometry(1.10, 0.06, 0.34),
      new THREE.MeshStandardMaterial({ color: 0x8d8579, roughness: 0.95 })
    );
    step.position.set(cE + 0.22, deck + 0.03, -southN + 0.20);
    homeGroup.add(step);
  })();

  // planters and specimens
  (function () {
    var pm = new THREE.MeshStandardMaterial({ color: 0x5b4a3c, roughness: 0.95 });
    (GEO.home.planters || []).forEach(function (p) {
      var g = new THREE.ExtrudeGeometry(shapeFrom(p.ring), { depth: 0.55, bevelEnabled: false });
      g.rotateX(-Math.PI / 2);
      var m = new THREE.Mesh(g, pm);
      m.position.y = H.garden_deck_m + 0.12;
      m.castShadow = true; m.receiveShadow = true;
      homeGroup.add(m);
    });
    var plants = GEO.home.planting || [];
    if (plants.length) {
      var leaf = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.85,
                                                 flatShading: true });
      var im = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 1), leaf, plants.length);
      im.castShadow = true;
      im.frustumCulled = false;
      var d = new THREE.Object3D(), lc = new THREE.Color();
      // Nineteen named specimens drawn as nineteen identical green balls looked
      // like a hedge. Acers, grasses and agapanthus are not the same shape or
      // the same green, and this is a plant nursery's roof garden.
      plants.forEach(function (p, i) {
        var r = p.radius_m || p.r || 0.55;
        var sd = hash((p.label || "plant") + i);
        var tall = sd > 0.62;                       // upright grasses vs mounded shrubs
        d.position.set(p.at ? p.at[0] : p[0],
                       H.garden_deck_m + 0.55 + r * (tall ? 1.05 : 0.72),
                       -(p.at ? p.at[1] : p[1]));
        d.scale.set(r * (tall ? 0.72 : 1.02 + sd * 0.22),
                    r * (tall ? 2.05 + sd : 1.05 + sd * 0.4),
                    r * (tall ? 0.72 : 1.02 + hash("pz" + i) * 0.22));
        d.rotation.set((sd - 0.5) * 0.3, sd * 6.283, (hash("pr" + i) - 0.5) * 0.3);
        d.updateMatrix();
        im.setMatrixAt(i, d.matrix);
        lc.setHSL(0.23 + sd * 0.07, 0.30 + sd * 0.22, 0.24 + hash("pl" + i) * 0.16);
        im.setColorAt(i, lc);
      });
      if (im.instanceColor) im.instanceColor.needsUpdate = true;
      homeGroup.add(im);
    }
  })();

  /* --------------------------------------------------------- bedroom windows
   * The two openings that actually matter. Generic windows were removed from
   * this scene twice because they were invented; these are not. Bedroom 1's
   * rear window comes straight from the 1:50 drawings - centre, width, sill and
   * head are all in site_geometry - and it is the habitable-room opening the
   * officer assessment placed about 1.5 m from the 183 outlet. Bedroom 2 is
   * located from the first-floor plan's own room label, which lands 1.6 m from
   * The supplied GPS fix for the indoor sensor places unit 5 in Bedroom 2, so this is
   * the room its readings describe.
   */
  (function () {
    var glass = new THREE.MeshStandardMaterial({
      color: 0x2a3f4e, roughness: 0.10, metalness: 0.28,
      emissive: 0xffc07a, emissiveIntensity: 0.08
    });
    windowMats.push(glass);
    var frameMat = new THREE.MeshStandardMaterial({ color: 0xd8d2c6, roughness: 0.72 });
    var sillMat = new THREE.MeshStandardMaterial({ color: 0xb9b2a4, roughness: 0.9 });

    function window2(cfg) {
      var hgt = cfg.head - cfg.sill, y = (cfg.sill + cfg.head) / 2;
      var g = new THREE.Group();
      g.position.set(cfg.east, y, -cfg.north);
      g.rotation.y = cfg.yaw;
      var reveal = new THREE.Mesh(
        new THREE.BoxGeometry(cfg.width + 0.22, hgt + 0.22, 0.16), frameMat);
      reveal.position.z = -0.02; g.add(reveal);
      var pane = new THREE.Mesh(
        new THREE.BoxGeometry(cfg.width, hgt, 0.06), glass);
      pane.position.z = 0.07; g.add(pane);
      // one mullion and one transom, so it reads as a window and not a hole
      var mull = new THREE.Mesh(new THREE.BoxGeometry(0.07, hgt, 0.09), frameMat);
      mull.position.z = 0.10; g.add(mull);
      var tran = new THREE.Mesh(new THREE.BoxGeometry(cfg.width, 0.07, 0.09), frameMat);
      tran.position.set(0, hgt * 0.18, 0.10); g.add(tran);
      var sill = new THREE.Mesh(
        new THREE.BoxGeometry(cfg.width + 0.34, 0.09, 0.30), sillMat);
      sill.position.set(0, -hgt / 2 - 0.10, 0.10); g.add(sill);
      g.traverse(function (o) { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
      homeGroup.add(g);
      return g;
    }

    var rw = GEO.home.rear_window;
    if (rw) {
      window2({ east: rw.centre[0], north: rw.centre[1], width: rw.width_m,
                sill: rw.sill_m, head: rw.head_m, yaw: 0.124 });   // faces south
    }
    // Bedroom 2, south-facing, in the western range's rear wall. Same sill and
    // head as Bedroom 1 - one measured storey height, applied to both.
    window2({ east: 3.5, north: -10.71, width: 1.80,
              sill: GEO.heights.bedroom_window_sill_m,
              head: GEO.heights.bedroom_window_head_m, yaw: 0.124 });
  })();

  /* ------------------------------------------------------------------ stacks */
  var SRC_COLOUR = {
    existing_173: 0xff704d, permitted_183: 0xa78bfa, chicky_163: 0x52c99a
  };
  var stacks = {};
  GEO.sources.forEach(function (s) {
    var g = new THREE.Group();
    var h = s.outlet_m - s.base_m;
    var duct = new THREE.Mesh(
      new THREE.CylinderGeometry(s.radius_m, s.radius_m * 1.05, h, 16),
      new THREE.MeshStandardMaterial({ color: 0xa9b0b6, roughness: 0.42, metalness: 0.65 })
    );
    duct.position.y = s.base_m + h / 2;
    duct.castShadow = true;
    g.add(duct);
    var cowl = new THREE.Mesh(
      new THREE.ConeGeometry(s.radius_m * 1.9, s.radius_m * 1.3, 16),
      new THREE.MeshStandardMaterial({ color: 0x8f979d, roughness: 0.5, metalness: 0.6 })
    );
    cowl.position.y = s.outlet_m + s.radius_m * 0.9;
    cowl.castShadow = true;
    g.add(cowl);
    var halo = new THREE.Mesh(
      new THREE.SphereGeometry(s.radius_m * 1.5, 12, 10),
      new THREE.MeshBasicMaterial({ color: SRC_COLOUR[s.key] || 0xffffff,
        transparent: true, opacity: 0.0 })
    );
    halo.position.y = s.outlet_m;
    g.add(halo);
    // The 1 m composite cannot resolve a narrow duct and its capture year at
    // this cell has not been established. The guide makes the scenario outlet
    // explicit without implying that it was measured by LIDAR.
    var guide = new THREE.Mesh(
      new THREE.CylinderGeometry(0.035, 0.035, s.outlet_m, 6),
      new THREE.MeshBasicMaterial({ color: SRC_COLOUR[s.key] || 0xffffff,
                                    transparent: true, opacity: 0.30 })
    );
    guide.position.y = s.outlet_m / 2;
    g.add(guide);
    g.position.set(s.east_m, 0, -s.north_m);
    scene.add(g);
    stacks[s.key] = { group: g, src: s, halo: halo };
  });

  /* ------------------------------------------------------------------ plumes */
  function softSprite() {
    var c = document.createElement("canvas"); c.width = c.height = 128;
    var x = c.getContext("2d");
    var gr = x.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0, "rgba(255,255,255,0.95)");
    gr.addColorStop(0.18, "rgba(255,255,255,0.55)");
    gr.addColorStop(0.48, "rgba(255,255,255,0.16)");
    gr.addColorStop(1, "rgba(255,255,255,0)");
    x.fillStyle = gr; x.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  }
  var SPRITE = softSprite();

  function Plume(src, colour, n) {
    this.src = src;
    this.n = n;
    this.pos = new Float32Array(n * 3);
    this.age = new Float32Array(n);
    this.life = new Float32Array(n);
    this.seed = new Float32Array(n * 3);
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(this.pos, 3));
    g.setAttribute("alpha", new THREE.BufferAttribute(new Float32Array(n), 1));
    g.setAttribute("psize", new THREE.BufferAttribute(new Float32Array(n), 1));
    this.geo = g;
    this.mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.NormalBlending,
      uniforms: { map: { value: SPRITE }, tint: { value: new THREE.Color(colour) },
                  opacity: { value: 1.0 } },
      vertexShader:
        "attribute float alpha; attribute float psize; varying float vA;" +
        "void main(){ vA = alpha; vec4 mv = modelViewMatrix * vec4(position,1.0);" +
        "gl_PointSize = min(psize * (340.0 / -mv.z), 140.0);"
        + " gl_Position = projectionMatrix * mv; }",
      fragmentShader:
        "uniform sampler2D map; uniform vec3 tint; uniform float opacity; varying float vA;" +
        "void main(){ vec4 t = texture2D(map, gl_PointCoord);" +
        "gl_FragColor = vec4(tint, t.a * vA * opacity); }"
    });
    this.points = new THREE.Points(g, this.mat);
    this.points.frustumCulled = false;
    for (var i = 0; i < n; i++) { this.age[i] = Math.random() * 6; this.life[i] = 1; this.reset(i, true); }
    scene.add(this.points);
  }
  Plume.prototype.reset = function (i, spread) {
    var s = this.src;
    this.pos[i * 3] = s.east_m + (Math.random() - 0.5) * s.radius_m * 0.8;
    this.pos[i * 3 + 1] = s.outlet_m + (spread ? Math.random() * 2 : 0);
    this.pos[i * 3 + 2] = -s.north_m + (Math.random() - 0.5) * s.radius_m * 0.8;
    this.age[i] = 0;
    this.life[i] = 4.5 + Math.random() * 4.5;
    this.seed[i * 3] = (Math.random() - 0.5);
    this.seed[i * 3 + 1] = Math.random();
    this.seed[i * 3 + 2] = (Math.random() - 0.5);
  };
  Plume.prototype.update = function (dt, wind, strength, t) {
    var vis = this.smoke ? 1 : 0.20;
    var reach = this.smoke ? 1 : 0.18;
    var a = this.geo.attributes.alpha.array, ps = this.geo.attributes.psize.array;
    var active = Math.floor(this.n * clamp(strength, 0, 1));
    for (var i = 0; i < this.n; i++) {
      if (i >= active) { a[i] = 0; continue; }
      this.age[i] += dt;
      if (this.age[i] > this.life[i]) this.reset(i, false);
      var f = this.age[i] / this.life[i];
      // rise fast off the outlet, then bend over into the wind as momentum decays
      var buoy = 2.6 * Math.exp(-this.age[i] * 0.75);
      this.pos[i * 3] += (wind.x * f * 1.25 * reach + this.seed[i * 3] * 0.30 * reach +
                          Math.sin(t * 0.7 + i) * 0.06) * dt;
      this.pos[i * 3 + 1] += (buoy * reach + this.seed[i * 3 + 1] * 0.16) * dt;
      this.pos[i * 3 + 2] += (wind.z * f * 1.25 * reach + this.seed[i * 3 + 2] * 0.30 * reach +
                              Math.cos(t * 0.6 + i) * 0.06) * dt;
      a[i] = Math.pow(Math.sin(Math.PI * f), 0.92) * (this.smoke ? 0.26 : 0.30) * vis * clamp(strength, 0, 1);
      ps[i] = this.smoke ? 1.6 + f * f * 11.5 : 1.1 + f * 2.8;
    }
    this.geo.attributes.position.needsUpdate = true;
    this.geo.attributes.alpha.needsUpdate = true;
    this.geo.attributes.psize.needsUpdate = true;
  };

  /* The supplied record contains visible-plume evidence for 173 only. That is
   * an evidence statement, not a claim that another cooking process can never
   * emit visible aerosol. Other outlets get only a locator shimmer. */
  var SMOKING = { existing_173: 1.0 };
  var plumes = {};
  GEO.sources.forEach(function (s) {
    var smoke = SMOKING[s.key] || 0;
    var tint = smoke
      ? new THREE.Color(0xe4ddd2).lerp(new THREE.Color(SRC_COLOUR[s.key]), 0.08)
      : new THREE.Color(SRC_COLOUR[s.key] || 0xcccccc).lerp(new THREE.Color(0xd8dbdd), 0.55);
    plumes[s.key] = new Plume(s, tint, smoke ? 1200 : 90);
    plumes[s.key].smoke = smoke;
  });

  /* ----------------------------------------------------------------- traffic */
  function Traffic(road) {
    this.road = road;
    this.line = road.centreline;
    this.len = 0;
    this.cum = [0];
    for (var i = 1; i < this.line.length; i++) {
      this.len += Math.hypot(this.line[i][0] - this.line[i - 1][0],
                             this.line[i][1] - this.line[i - 1][1]);
      this.cum.push(this.len);
    }
    this.max = 90;
    var body = new THREE.BoxGeometry(1, 1, 1);
    this.mesh = new THREE.InstancedMesh(body, new THREE.MeshStandardMaterial({
      color: 0xb9c0c8, roughness: 0.55, metalness: 0.25
    }), this.max);
    this.mesh.castShadow = true;
    this.mesh.frustumCulled = false;
    this.mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(this.max * 3), 3);
    scene.add(this.mesh);

    // A single box is a brick. Splitting body and cabin costs one more
    // InstancedMesh and turns bricks into cars, vans, lorries and buses.
    this.cabs = new THREE.InstancedMesh(body, new THREE.MeshStandardMaterial({
      color: 0xffffff, roughness: 0.4, metalness: 0.15
    }), this.max);
    this.cabs.castShadow = true; this.cabs.frustumCulled = false;
    this.cabs.instanceColor =
      new THREE.InstancedBufferAttribute(new Float32Array(this.max * 3), 3);
    scene.add(this.cabs);

    this.lampGeo = new THREE.SphereGeometry(0.42, 8, 6);
    this.lamps = new THREE.InstancedMesh(this.lampGeo, new THREE.MeshBasicMaterial({
      color: 0xffd9a0, transparent: true, opacity: 0.0
    }), this.max);
    this.lamps.frustumCulled = false;
    scene.add(this.lamps);

    this.wheels = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.30, 0.30, 0.20, 10),
      new THREE.MeshStandardMaterial({ color: 0x161618, roughness: 0.72 }),
      this.max * 4
    );
    this.wheels.castShadow = true;
    this.wheels.frustumCulled = false;
    scene.add(this.wheels);

    this.heads = new THREE.InstancedMesh(
      new THREE.BoxGeometry(0.18, 0.10, 0.08),
      new THREE.MeshBasicMaterial({ color: 0xffe2a8 }),
      this.max * 2
    );
    this.heads.frustumCulled = false;
    scene.add(this.heads);
    this.tails = new THREE.InstancedMesh(
      new THREE.BoxGeometry(0.16, 0.08, 0.06),
      new THREE.MeshBasicMaterial({ color: 0xff3b30 }),
      this.max * 2
    );
    this.tails.frustumCulled = false;
    scene.add(this.tails);

    // Vehicles used to be dropped at random arc positions and then all moved at
    // the same speed, so any two that started on top of each other stayed on top
    // of each other for ever. They are now slotted into per-lane queues at a
    // fixed spacing and advanced by a single shared phase, which makes an
    // overlap impossible by construction.
    this.phase = 0;
    this.cars = [];
    for (var k = 0; k < this.max; k++) {
      this.cars.push({ dir: k % 2 ? 1 : -1, lane: (k >> 1) % 2, slot: k >> 2,
                       cls: 0, w: 1.8, l: 4.3, h: 1.5, tint: 0.5 });
    }
    this.dummy = new THREE.Object3D();
  }
  Traffic.prototype.at = function (s) {
    s = ((s % this.len) + this.len) % this.len;
    for (var i = 1; i < this.cum.length; i++) {
      if (s <= this.cum[i]) {
        var t = (s - this.cum[i - 1]) / (this.cum[i] - this.cum[i - 1] || 1);
        var a = this.line[i - 1], b = this.line[i];
        return { x: lerp(a[0], b[0], t), n: lerp(a[1], b[1], t),
                 hx: b[0] - a[0], hn: b[1] - a[1] };
      }
    }
    var e = this.line[this.line.length - 1];
    return { x: e[0], n: e[1], hx: 1, hn: 0 };
  };
  Traffic.prototype.update = function (dt, flow, speedKmh, night) {
    var v = speedKmh / 3.6;
    this.phase += v * dt;
    // Density from the hourly flow, then split over two lanes each way. The gap
    // is floored at 13.5 m so a 11.5 m bus still clears the vehicle in front.
    var perDir = clamp(Math.round((flow / Math.max(speedKmh, 5)) * (this.len / 1000) / 2), 0, this.max / 2);
    var perLane = Math.max(0, Math.round(perDir / 2));
    var MIN_GAP = 13.5;
    if (perLane > 0 && this.len / perLane < MIN_GAP) perLane = Math.floor(this.len / MIN_GAP);
    var gap = perLane > 0 ? this.len / perLane : this.len;
    var shown = perLane * 4;
    var col = new THREE.Color();
    for (var i = 0; i < this.max; i++) {
      var c = this.cars[i];
      if (i >= shown) {
        this.dummy.position.set(0, -900, 0); this.dummy.scale.set(0.01, 0.01, 0.01);
        this.dummy.rotation.set(0, 0, 0);
        this.dummy.updateMatrix(); this.mesh.setMatrixAt(i, this.dummy.matrix);
        this.cabs.setMatrixAt(i, this.dummy.matrix);
        this.lamps.setMatrixAt(i, this.dummy.matrix);
        var hidden = this.dummy.matrix;
        for (var hw = 0; hw < 4; hw++) this.wheels.setMatrixAt(i * 4 + hw, hidden);
        this.heads.setMatrixAt(i * 2, hidden); this.heads.setMatrixAt(i * 2 + 1, hidden);
        this.tails.setMatrixAt(i * 2, hidden); this.tails.setMatrixAt(i * 2 + 1, hidden);
        continue;
      }
      if (c.cls === 0) {
        // Long vehicles are kept in the nearside lane, as they mostly are in
        // reality, and it stops an 11.5 m bus appearing in an overtaking lane.
        var r = c.lane === 0 ? Math.random() : Math.random() * 0.86;
        c.cls = 1;
        var pal = [[0.58, 0.04], [0.62, 0.06], [0.00, 0.00], [0.08, 0.55],
                   [0.58, 0.42], [0.33, 0.18], [0.10, 0.08]];
        var pick = pal[Math.floor(Math.random() * pal.length)];
        c.hue = pick[0]; c.sat = pick[1];
        if (r > 0.93) { c.w = 2.5; c.l = 11.5; c.h = 3.15; c.tint = 0.18; }        // bus
        else if (r > 0.86) { c.w = 2.45; c.l = 8.6; c.h = 2.9; c.tint = 0.55; }    // hgv
        else if (r > 0.66) { c.w = 2.0; c.l = 5.3; c.h = 2.05; c.tint = 0.72; }    // van
        else { c.w = 1.75; c.l = 4.2; c.h = 1.38; c.tint = 0.22 + Math.random() * 0.45; }
        // a small fixed jitter so the two directions are not in lockstep
        c.stagger = (c.dir > 0 ? 0 : gap * 0.5) + (c.lane ? gap * 0.28 : 0);
      }
      c.s = c.slot * gap + c.stagger + this.phase * c.dir;
      var p = this.at(c.s);
      var L = Math.hypot(p.hx, p.hn) || 1;
      var laneOff = (0.28 + c.lane * 0.42) * this.road.half_width_m;
      var offx = -(-p.hn / L) * laneOff * c.dir;
      var offn = -(p.hx / L) * laneOff * c.dir;
      var boxy = c.l > 7;                       // bus or lorry: cabin runs the length
      var hBody = c.h * (boxy ? 0.42 : 0.58);
      var hCab = c.h - hBody;
      var yaw = Math.atan2(p.hx, p.hn) + (c.dir < 0 ? Math.PI : 0);
      var px = p.x + offx, pz = -(p.n + offn);
      var ride = ROAD_Y + 0.32;
      this.dummy.position.set(px, ride + hBody / 2, pz);
      this.dummy.rotation.set(0, yaw, 0);
      this.dummy.scale.set(c.w, hBody, c.l);
      this.dummy.updateMatrix();
      this.mesh.setMatrixAt(i, this.dummy.matrix);
      col.setHSL(c.hue, c.sat, 0.10 + c.tint * 0.28);
      this.mesh.setColorAt(i, col);

      var lCab = boxy ? c.l * 0.94 : c.l * 0.48;
      var back = boxy ? 0 : -c.l * 0.12 * c.dir;
      this.dummy.position.set(px + Math.sin(yaw) * back,
                              ride + hBody + hCab / 2,
                              pz + Math.cos(yaw) * back);
      this.dummy.rotation.set(0, yaw, 0);
      this.dummy.scale.set(c.w * (boxy ? 0.99 : 0.88), hCab, lCab);
      this.dummy.updateMatrix();
      this.cabs.setMatrixAt(i, this.dummy.matrix);
      this.cabs.setColorAt(i, boxy ? col : new THREE.Color(0x1c2830));
      this.dummy.scale.set(0.9, 0.55, 0.9);
      this.dummy.position.y = ride + c.h * 0.42;
      this.dummy.updateMatrix();
      this.lamps.setMatrixAt(i, this.dummy.matrix);

      var fx = Math.sin(yaw), fz = Math.cos(yaw);
      var rx = fz, rz = -fx;
      var wheelX = [1, 1, -1, -1], wheelZ = [1, -1, 1, -1];
      for (var wi = 0; wi < 4; wi++) {
        var wx = px + rx * (c.w * 0.42) * wheelX[wi] + fx * (c.l * 0.32) * wheelZ[wi];
        var wz = pz + rz * (c.w * 0.42) * wheelX[wi] + fz * (c.l * 0.32) * wheelZ[wi];
        this.dummy.position.set(wx, ride - 0.02, wz);
        this.dummy.rotation.set(Math.PI / 2, yaw, 0);
        this.dummy.scale.set(boxy ? 1.15 : 1, boxy ? 1.2 : 1, boxy ? 1.15 : 1);
        this.dummy.updateMatrix();
        this.wheels.setMatrixAt(i * 4 + wi, this.dummy.matrix);
      }
      for (var li = 0; li < 2; li++) {
        var side = li === 0 ? -1 : 1;
        this.dummy.rotation.set(0, yaw, 0);
        this.dummy.scale.set(1, 1, 1);
        this.dummy.position.set(
          px + rx * c.w * 0.32 * side + fx * c.l * 0.50,
          ride + hBody * 0.55, pz + rz * c.w * 0.32 * side + fz * c.l * 0.50);
        this.dummy.updateMatrix();
        this.heads.setMatrixAt(i * 2 + li, this.dummy.matrix);
        this.dummy.position.set(
          px + rx * c.w * 0.32 * side - fx * c.l * 0.50,
          ride + hBody * 0.55, pz + rz * c.w * 0.32 * side - fz * c.l * 0.50);
        this.dummy.updateMatrix();
        this.tails.setMatrixAt(i * 2 + li, this.dummy.matrix);
      }
    }
    this.mesh.count = this.max;
    this.cabs.count = this.max;
    this.mesh.instanceMatrix.needsUpdate = true;
    this.cabs.instanceMatrix.needsUpdate = true;
    this.wheels.instanceMatrix.needsUpdate = true;
    this.heads.instanceMatrix.needsUpdate = true;
    this.tails.instanceMatrix.needsUpdate = true;
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true;
    if (this.cabs.instanceColor) this.cabs.instanceColor.needsUpdate = true;
    this.lamps.instanceMatrix.needsUpdate = true;
    this.lamps.material.opacity = night * 0.85;
    return shown;
  };

  // Do not extrapolate the road beyond the surveyed OSM centreline. The former
  // 170 m tangent extension crossed a real context block at the west end. A
  // shorter, correct traffic stream is preferable to invented carriageway.
  var traffic = nkr ? new Traffic(nkr) : null;

  /* ---------------------------------------------------------------- monitors */
  function labelSprite(text, colour, sub) {
    var pad = 14, fs = 40, sfs = 26;
    var c = document.createElement("canvas"), x = c.getContext("2d");
    x.font = "700 " + fs + "px system-ui, sans-serif";
    var w1 = x.measureText(text).width;
    x.font = "500 " + sfs + "px ui-monospace, monospace";
    var w2 = sub ? x.measureText(sub).width : 0;
    c.width = Math.ceil(Math.max(w1, w2) + pad * 2);
    c.height = sub ? fs + sfs + pad * 2 + 6 : fs + pad * 2;
    x = c.getContext("2d");
    x.fillStyle = "rgba(10,12,14,0.78)";
    x.strokeStyle = colour; x.lineWidth = 2;
    x.beginPath();
    if (x.roundRect) x.roundRect(1.5, 1.5, c.width - 3, c.height - 3, 12);
    else x.rect(1.5, 1.5, c.width - 3, c.height - 3);
    x.fill(); x.stroke();
    x.fillStyle = "#f4f1ea"; x.textBaseline = "top";
    x.font = "700 " + fs + "px system-ui, sans-serif";
    x.fillText(text, pad, pad - 2);
    if (sub) {
      x.fillStyle = colour;
      x.font = "500 " + sfs + "px ui-monospace, monospace";
      x.fillText(sub, pad, pad + fs + 2);
    }
    var t = new THREE.CanvasTexture(c);
    t.minFilter = THREE.LinearFilter;
    var s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true }));
    s.userData.aspect = c.width / c.height;
    s.userData.base = c.height / 46;
    s.renderOrder = 20;
    return s;
  }

  var labels = [];
  var weatherStationItems = [];
  var metGroup = new THREE.Group();
  scene.add(metGroup);
  (GEO.weather_stations || []).forEach(function (station) {
    var e = station.render_east_m, n = station.render_north_m;
    var anchorE = Number.isFinite(station.mount_anchor_east_m)
      ? station.mount_anchor_east_m : e;
    var anchorN = Number.isFinite(station.mount_anchor_north_m)
      ? station.mount_anchor_north_m : n;
    var headX = e - anchorE, headZ = -(n - anchorN);
    var base = station.mast_base_m, head = station.sensor_height_m;
    var white = new THREE.MeshStandardMaterial({
      color: 0xf5f7f8, roughness: 0.38, metalness: 0.04, transparent: true
    });
    var shade = new THREE.MeshStandardMaterial({
      color: 0xdbe2e6, roughness: 0.46, transparent: true
    });
    var dark = new THREE.MeshStandardMaterial({
      color: 0x17232b, roughness: 0.30, metalness: 0.12, transparent: true
    });
    var metal = new THREE.MeshStandardMaterial({
      color: 0xc9d2d8, roughness: 0.34, metalness: 0.58, transparent: true
    });
    var materials = [white, shade, dark, metal];
    var stationGroup = new THREE.Group();
    stationGroup.position.set(anchorE, base, -anchorN);
    metGroup.add(stationGroup);
    function piece(geometry, material, y, x, z) {
      var mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(x || 0, y, z || 0);
      mesh.castShadow = true;
      stationGroup.add(mesh);
      return mesh;
    }
    // The installed universal mount is a wall plate and curved projecting pole,
    // not a freestanding vertical mast. Its upper end lands at the corrected
    // head coordinate north of unit 1.
    var mountRise = Math.max(head - base - 0.15, 0.35);
    piece(new THREE.BoxGeometry(0.16, 0.28, 0.035), metal, 0.14);
    var mountCurve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0.08, 0),
      new THREE.Vector3(0, mountRise * 0.48, 0),
      new THREE.Vector3(headX * 0.30, mountRise * 0.76, headZ * 0.30),
      new THREE.Vector3(headX * 0.76, mountRise * 0.94, headZ * 0.76),
      new THREE.Vector3(headX, mountRise, headZ)
    ]);
    var mount = new THREE.Mesh(
      new THREE.TubeGeometry(mountCurve, 24, 0.025, 8, false), metal
    );
    mount.castShadow = true;
    stationGroup.add(mount);
    var hy = head - base;
    piece(new THREE.CylinderGeometry(0.09, 0.07, 0.18, 20), white,
          hy - 0.10, headX, headZ);
    for (var li = 0; li < 4; li++) {
      piece(new THREE.CylinderGeometry(0.13 - li * 0.006, 0.12 - li * 0.006, 0.025, 24),
            shade, hy + li * 0.035, headX, headZ);
    }
    piece(new THREE.CylinderGeometry(0.17, 0.15, 0.06, 28), white,
          hy + 0.17, headX, headZ);
    piece(new THREE.CylinderGeometry(0.18, 0.18, 0.055, 28), white,
          hy + 0.225, headX, headZ);
    piece(new THREE.CylinderGeometry(0.16, 0.18, 0.035, 28), shade,
          hy + 0.27, headX, headZ);
    for (var pi = 0; pi < 3; pi++) {
      var panel = new THREE.Mesh(new THREE.BoxGeometry(0.065, 0.14, 0.012), dark);
      var angle = pi * Math.PI * 2 / 3;
      panel.position.set(headX + Math.sin(angle) * 0.085, hy - 0.105,
                         headZ + Math.cos(angle) * 0.085);
      panel.rotation.y = angle;
      stationGroup.add(panel);
    }
    var label = labelSprite("Roof weather sensor", "#4fd1c5", "installed 12 Aug · approximate position");
    label.userData.base *= 0.72;
    label.position.set(e, head + 0.75, -n);
    label.userData.weatherLabel = true;
    metGroup.add(label);
    labels.push(label);
    weatherStationItems.push({
      data: station,
      group: stationGroup,
      label: label,
      materials: materials,
      installedAt: Date.parse(station.installed_date_local + "T00:00:00+01:00")
    });
  });

  var monGroup = new THREE.Group();
  var monitorItems = [];
  var MONITOR_POD_H = 0.18;
  scene.add(monGroup);
  (GEO.monitors || []).forEach(function (m) {
    var pod = new THREE.Mesh(
      new THREE.BoxGeometry(0.22, MONITOR_POD_H, 0.10),
      new THREE.MeshStandardMaterial({ color: 0xf2f5f7, roughness: 0.6, emissive: 0x14343a,
                                       emissiveIntensity: 0.5 })
    );
    pod.position.set(m.east_m, m.height_m, -m.north_m);
    pod.castShadow = true;
    monGroup.add(pod);
    var glow = new THREE.Sprite(new THREE.SpriteMaterial({
      map: softGlowTexture(), color: 0x4fd1c5, transparent: true, opacity: 0.22,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    glow.position.set(m.east_m, m.height_m, -m.north_m);
    glow.scale.set(1.15, 1.15, 1);
    glow.renderOrder = 8;
    monGroup.add(glow);
    var band = new THREE.Mesh(
      new THREE.BoxGeometry(0.225, 0.035, 0.105),
      new THREE.MeshStandardMaterial({ color: 0x1788c9, roughness: 0.52 })
    );
    band.position.set(m.east_m, m.height_m - 0.07, -m.north_m);
    monGroup.add(band);
    var mast = new THREE.Mesh(
      new THREE.CylinderGeometry(0.012, 0.012, 1, 6),
      new THREE.MeshStandardMaterial({ color: 0x9aa3ab, roughness: 0.5, metalness: 0.4 })
    );
    var initialBase = Number.isFinite(m.mount_base_m) ? m.mount_base_m : m.height_m - 1.52;
    var initialLength = Math.max(m.height_m - MONITOR_POD_H / 2 - initialBase, 0.05);
    mast.position.set(m.east_m, initialBase + initialLength / 2, -m.north_m);
    mast.scale.y = initialLength;
    mast.visible = !/indoor/i.test(m.environment || "") && !m.mount_style;
    monGroup.add(mast);
    var bracket = new THREE.Mesh(
      new THREE.BoxGeometry(0.10, 0.30, 0.045),
      new THREE.MeshStandardMaterial({ color: 0x6b7379, roughness: 0.42, metalness: 0.58 })
    );
    bracket.position.set(m.east_m, m.height_m - 0.02, -m.north_m + 0.065);
    bracket.castShadow = true;
    bracket.visible = !/indoor/i.test(m.environment || "") && !!m.mount_style;
    monGroup.add(bracket);
    var lab = labelSprite("unit " + m.unit, "#4fd1c5", m.label.split(",")[0].replace(" - ", " · "));
    lab.userData.base *= 0.68;
    lab.position.set(m.east_m, m.height_m + 0.65 + (labels.length % 3) * 0.35, -m.north_m);
    monGroup.add(lab);
    lab.userData.monitorLabel = true;
    labels.push(lab);
    monitorItems.push({ data: m, pod: pod, band: band, mast: mast,
                        bracket: bracket, label: lab, glow: glow });
  });

  var colocLabel = labelSprite("4 units co-located", "#4fd1c5", "units 1–4 · outdoors");
  colocLabel.userData.base *= 0.58;
  colocLabel.userData.colocationLabel = true;
  monGroup.add(colocLabel); labels.push(colocLabel);

  // A dashed line used to link the courtyard pod to the bedroom pod as a
  // vertical pair. The supplied GPS fixes put unit 1 in the courtyard by County Street
  // and unit 5 in the middle of the house, so they are not stacked and the line
  // has been removed rather than left asserting a relationship that is not there.
  var verticalPair = null;

  function moveMonitor(item, position, index, mode) {
    var e = position.east_m, n = position.north_m, h = position.height_m;
    item.pod.position.set(e, h, -n);
    item.band.position.set(e, h - 0.07, -n);
    if (item.glow) item.glow.position.set(e, h, -n);
    var base = Number.isFinite(position.mount_base_m) ? position.mount_base_m : h - 1.52;
    var length = Math.max(h - MONITOR_POD_H / 2 - base, 0.05);
    item.mast.position.set(e, base + length / 2, -n);
    item.mast.scale.y = length;
    var mountStyle = position.mount_style || "";
    item.mast.visible = !/indoor/i.test(position.environment || "") && !mountStyle;
    if (item.bracket) {
      item.bracket.position.set(e, h - 0.02, -n + 0.065);
      item.bracket.visible = !/indoor/i.test(position.environment || "") && !!mountStyle;
    }
    var unit = String(item.data.unit);
    var labelLift = mode === "deployment"
      ? ({ "1": 0.48, "2": 0.72, "3": 0.70, "4": 1.18, "5": 0.62 }[unit] || 0.65)
      : 0.65;
    item.label.position.set(e, h + labelLift, -n);
  }

  function setNetworkMode(mode) {
    mode = mode === "deployment" ? "deployment" : "colocation";
    var stateSet = GEO.monitor_states && GEO.monitor_states[mode];
    if (!stateSet) return;
    var indoorUnits = GEO.monitor_states.colocation.indoor_units || [];
    monitorItems.forEach(function (item, index) {
      var unit = String(item.data.unit);
      var p = stateSet.positions[unit];
      if (p) moveMonitor(item, p, index, mode);
      // In co-location the outdoor four are one indistinguishable row, so their
      // individual labels are replaced by a single group label. Unit 5 is not in
      // that row - it is indoors - so it keeps its own label in both states.
      item.label.userData.suppressed =
        mode === "colocation" && indoorUnits.indexOf(unit) === -1;
    });
    var cp = GEO.monitor_states.colocation.positions;
    var ce = 0, cn = 0, ch = 0, count = 0;
    (GEO.monitor_states.colocation.colocated_units ||
     Object.keys(cp)).forEach(function (unit) {
      if (!cp[unit]) return;
      ce += cp[unit].east_m; cn += cp[unit].north_m; ch += cp[unit].height_m; count++;
    });
    colocLabel.position.set(ce / count, ch / count + 1.05, -cn / count);
    colocLabel.userData.suppressed = mode !== "colocation";
    if (verticalPair) verticalPair.visible = mode === "deployment";
    if (window.__state) window.__state.networkMode = mode;
    if (window.__networkModeChanged) window.__networkModeChanged(mode, stateSet);
  }
  window.__setNetworkMode = setNetworkMode;

  GEO.sources.forEach(function (s) {
    var col = "#" + new THREE.Color(SRC_COLOUR[s.key] || 0xffffff).getHexString();
    var name = { existing_173: "173 charcoal outlet", permitted_183: "183 kebab outlet",
                 chicky_163: "western outlet" }[s.key] || s.key;
    var lab = labelSprite(name, col, "approximate location");
    lab.position.set(s.east_m, s.outlet_m + 3.4 + (labels.length % 2) * 1.6, -s.north_m);
    scene.add(lab);
    lab.userData.sourceKey = s.key;
    labels.push(lab);
    stacks[s.key].label = lab;
  });

  /* --------------------------------------------------------- label declutter
   * Twenty labels in a 40 m courtyard will always collide in screen space, so
   * they are projected each frame, sorted near-to-far, and any that lands on
   * top of one already drawn is dropped. Nearest wins, which keeps whatever the
   * camera is actually looking at readable.
   */
  var _v = new THREE.Vector3();
  var placed = [];
  function declutter() {
    if (!state.showLabels) { labels.forEach(function (l) { l.visible = false; }); return; }
    var w = canvas.clientWidth, h = canvas.clientHeight, i, j;
    // A sprite k world units tall covers k * pxPerUnit / d pixels. The box used
    // to be scale.x * 8, which only matches at about 150 m - in the close views
    // labels sit 15-40 m out, so boxes came out 5-10x too small and overlapping
    // labels survived. It also read scale before scale was assigned below, so a
    // label hidden last frame tested with a stale size.
    var pxPerUnit = h / (2 * Math.tan(camera.fov * Math.PI / 360));
    var items = [];
    for (i = 0; i < labels.length; i++) {
      if (labels[i].userData.suppressed) { labels[i].visible = false; continue; }
      if (labels[i].userData.sourceKey && !state.enabled[labels[i].userData.sourceKey]) {
        labels[i].visible = false; continue;
      }
      _v.copy(labels[i].position).project(camera);
      if (_v.z > 1) { labels[i].visible = false; continue; }
      var dist = camera.position.distanceTo(labels[i].position);
      var size = clamp(dist / 52, 0.30, 2.2) * labels[i].userData.base;
      // 0.88 lets labels touch at the very edge rather than dropping a
      // neighbour over a couple of pixels of overlap.
      var px = 0.44 * size * pxPerUnit / Math.max(dist, 0.001);
      items.push({ l: labels[i], k: size,
                   x: (_v.x * 0.5 + 0.5) * w, y: (-_v.y * 0.5 + 0.5) * h,
                   d: dist,
                   hw: px * labels[i].userData.aspect, hh: px });
    }
    items.sort(function (a, b) { return a.d - b.d; });
    placed.length = 0;
    for (i = 0; i < items.length; i++) {
      var it = items[i], clash = false;
      for (j = 0; j < placed.length; j++) {
        var q = placed[j];
        if (Math.abs(it.x - q.x) < (it.hw + q.hw) && Math.abs(it.y - q.y) < (it.hh + q.hh)) {
          clash = true; break;
        }
      }
      it.l.visible = !clash && it.d < 190;
      if (!clash) {
        it.l.scale.set(it.k * it.l.userData.aspect, it.k, 1);
        it.l.material.opacity = clamp(1.25 - it.d / 190, 0.25, 1);
        placed.push(it);
      }
    }
  }

  /* ------------------------------------------------------------------ camera */
  var target = new THREE.Vector3(-1, 7, 10);
  var sph = new THREE.Spherical(66, 0.88, 0.70);
  var want = { t: target.clone(), s: new THREE.Spherical(sph.radius, sph.phi, sph.theta) };
  var dragging = false, lastX = 0, lastY = 0, pointers = {};

  function applyCam() {
    sph.radius = lerp(sph.radius, want.s.radius, 0.10);
    sph.phi = lerp(sph.phi, want.s.phi, 0.10);
    sph.theta = lerp(sph.theta, want.s.theta, 0.10);
    target.lerp(want.t, 0.10);
    var p = new THREE.Vector3().setFromSpherical(sph).add(target);
    camera.position.copy(p);
    camera.lookAt(target);
    var needle = document.getElementById("compass-needle");
    if (needle) needle.style.transform = "rotate(" + (sph.theta * 180 / Math.PI) + "deg)";
  }
  canvas.addEventListener("pointerdown", function (e) {
    dragging = true; lastX = e.clientX; lastY = e.clientY;
    pointers[e.pointerId] = 1; canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!dragging) return;
    want.s.theta -= (e.clientX - lastX) * 0.0055;
    want.s.phi = clamp(want.s.phi - (e.clientY - lastY) * 0.0045, 0.14, 1.50);
    lastX = e.clientX; lastY = e.clientY;
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(function (ev) {
    canvas.addEventListener(ev, function (e) { dragging = false; delete pointers[e.pointerId]; });
  });
  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    want.s.radius = clamp(want.s.radius * (1 + Math.sign(e.deltaY) * 0.11), 12, 240);
  }, { passive: false });

  function nudgeCamera(action) {
    if (action === "left") want.s.theta += 0.14;
    else if (action === "right") want.s.theta -= 0.14;
    else if (action === "up") want.s.phi = clamp(want.s.phi - 0.10, 0.14, 1.50);
    else if (action === "down") want.s.phi = clamp(want.s.phi + 0.10, 0.14, 1.50);
    else if (action === "in") want.s.radius = clamp(want.s.radius * 0.84, 12, 240);
    else if (action === "out") want.s.radius = clamp(want.s.radius * 1.18, 12, 240);
    else if (action === "home") goto("overview");
  }
  window.__nudgeCamera = nudgeCamera;
  document.addEventListener("keydown", function (event) {
    var tag = event.target && event.target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" ||
        (event.target && event.target.isContentEditable)) return;
    var action = {
      ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down",
      "+": "in", "=": "in", "-": "out", Home: "home"
    }[event.key];
    if (!action) return;
    event.preventDefault();
    nudgeCamera(action);
  });

  // Camera presets. north runs into -z, so a camera at large +z sits south of
  // the site looking back over the A201, the parade and then the terrace - the
  // order in which the three sources actually stack up behind each other.
  var VIEWS = {
    overview: { t: new THREE.Vector3(0, 7, 11), r: 57, phi: 0.90, th: 0.72 },
    // Targeted 6 m east of the network centroid and pulled back to 72 m. Centring
    // on 81a pushed units 1, 4 and Tempest underneath the reading panel and wind
    // rose - three of the five pods were unreadable in the first view anyone sees.
    deployment: { t: new THREE.Vector3(-12, 9.2, 12), r: 72, phi: 0.79, th: 0.86 },
    // From the south-east, i.e. from the 173 flue side: the panelled belt reads
    // in front, the screen across it, the raised garden set back behind.
    // From the north-east. The obvious framing is from the flue side, but the
    // 173 outlet is only 11 m from unit 2, so any south-easterly camera stands
    // almost on top of the stack and its label swallows the roof.
    // From the south-west, the way the installation photographs were taken: the
    // panelled belt in front, the screen across it, the raised garden behind and
    // the gabled house on the left.
    home81a: { t: new THREE.Vector3(-18.3, 8.6, 12.3), r: 13.5, phi: 0.87, th: 5.30 },
    // From the south, so the communal rail reads where it is - outboard of and
    // below the private terrace, not on it.
    western: { t: new THREE.Vector3(-45.6, 13.5, 11.6), r: 24.0, phi: 0.86, th: 2.62 },
    // Over the deck looking south. Eye level does not work here: the terrace
    // parapet and the rear of the parade are barely a metre apart, so a
    // standing camera sees a wall. The oblique is what shows the gap.
    terrace:  { t: new THREE.Vector3(4.0, 9.8, 11.0), r: 20.3, phi: 1.41, th: 1.62 },
    weather:  { t: new THREE.Vector3(6.85, 9.72, 13.17), r: 9.0, phi: 1.25, th: 3.60 },
    // Over the home roof looking down and west into the gap. Straight
    // overhead loses the two buildings that make it a gap; anything lower is
    // blocked by one of them, because the slot is only about 1.4 m wide.
    side:     { t: new THREE.Vector3(-3.0, 3.2, 5.0), r: 33.4, phi: 0.31, th: 0.51 },
    // Along the gap from the east - the dimension the whole case turns on.
    flues:    { t: new THREE.Vector3(7.0, 9.2, 15.8), r: 17.0, phi: 1.24, th: 1.51 },
    // Three-quarter view along the A201 from above the carriageway. At street
    // level the road is a thin strip in a wide pale field and reads as nothing;
    // from 18 m up the canyon, the markings and the traffic all read.
    street:   { t: new THREE.Vector3(6, 1.5, 45), r: 40.0, phi: 1.15, th: 4.76 },
    aerial:   { t: new THREE.Vector3(0, 5, 14), r: 84, phi: 0.60, th: 0.62 },
    // Near-plan. The most useful view when you are deciding where a box goes,
    // and the one that makes a misclassified tree or a phantom building obvious
    // - both are invisible in perspective.
    plan:     { t: new THREE.Vector3(0, 5, 12), r: 150, phi: 0.16, th: 0.10 }
  };
  function goto(name, immediate) {
    var v = VIEWS[name]; if (!v) return;
    want.t.copy(v.t); want.s.radius = v.r; want.s.phi = v.phi; want.s.theta = v.th;
    // Deep-linked QA views should open at the requested camera, rather than
    // spending their first few seconds interpolating from the overview.
    if (immediate) {
      target.copy(want.t);
      sph.phi = want.s.phi; sph.theta = want.s.theta;
      // Open slightly further out so the first second eases into the shot.
      sph.radius = want.s.radius * 1.16;
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (b) {
      b.classList.toggle("on", b.dataset.view === name);
    });
    var selector = document.getElementById("view-select");
    if (selector) selector.value = name;
  }
  window.__goto = goto;

  /* -------------------------------------------------------------------- time */
  var urlState = new URLSearchParams(window.location.search);
  var requestedRecord = urlState.get("record");
  if (requestedRecord === "air-quality") requestedRecord = "historic-aq";
  var initialRecordMode = requestedRecord === "historic-aq" ||
      (requestedRecord === "current-aq" && CURRENT_AQ) || requestedRecord === "tempest"
    ? requestedRecord : CURRENT_AQ ? "current-aq" : WEATHER ? "tempest" : "historic-aq";
  var firstRecord = recordForMode(initialRecordMode);
  var state = { frame: (firstRecord && firstRecord.default_frame) || 0,
                hour: 19, minute: 0, playing: false, wind: null, windSpeed: 0,
                hasWind: false, windIsCalm: false, recordMode: initialRecordMode,
                enabled: { existing_173: true, permitted_183: false,
                           chicky_163: false },
                trafficOn: true, showLabels: true, plumePreview: true,
                networkMode: "deployment" };
  window.__state = state;
  setNetworkMode(urlState.get("layout") || "deployment");
  function setRecordMode(mode) {
    if (mode === "air-quality") mode = "historic-aq";
    if (mode === "current-aq" && !CURRENT_AQ) return;
    if (mode === "tempest" && !WEATHER) return;
    if (mode !== "current-aq" && mode !== "tempest") mode = "historic-aq";
    var record = recordForMode(mode);
    if (!record) return;
    state.recordMode = mode;
    state.frame = record.default_frame || 0;
    state.playing = false;
    var playButton = document.getElementById("play");
    if (playButton) playButton.textContent = "▶";
    if (window.__recordModeChanged) window.__recordModeChanged(mode, record);
  }
  window.__setRecordMode = setRecordMode;
  setRecordMode(initialRecordMode);

  function sunPosition(h, doy) {
    // London 51.5N; clock is BST so solar noon falls at about 13:05. The
    // declination now comes from the actual day of the record rather than being
    // pinned to 9 August.
    var lat = 51.5 * Math.PI / 180;
    var dec = -23.44 * Math.cos(2 * Math.PI * ((doy || 221) + 10) / 365) * Math.PI / 180;
    var HA = (h - 13.05) * 15 * Math.PI / 180;
    var sinE = Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(HA);
    var elev = Math.asin(clamp(sinE, -1, 1));
    var az = Math.atan2(Math.sin(HA),
                        Math.cos(HA) * Math.sin(lat) - Math.tan(dec) * Math.cos(lat));
    return { elev: elev, azim: az + Math.PI };  // compass bearing, 0 = north
  }

  function applyHour() {
    var d = recordDate(state.frame);
    var local = window.__londonParts ? window.__londonParts(d) : {
      year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate(),
      hour: d.getUTCHours(), minute: d.getUTCMinutes()
    };
    state.hour = local.hour;
    state.minute = local.minute;
    var doy = Math.floor(
      (Date.UTC(local.year, local.month - 1, local.day) - Date.UTC(local.year, 0, 0)) / 86400000
    );
    var weather = frameWeather(state.frame);
    state.wind = weather.wind_from_deg;
    state.windSpeed = weather.wind_average_m_s === null ? 0 : weather.wind_average_m_s;
    state.windIsCalm = weather.wind_average_m_s !== null && weather.wind_average_m_s < 0.2;
    state.hasWind = !state.windIsCalm && weather.wind_from_deg !== null &&
                    weather.wind_average_m_s !== null;
    var h = state.hour + state.minute / 60;
    var s = sunPosition(h, doy);
    var e = s.elev, day = clamp((e + 0.09) / 0.5, 0, 1);
    var dusk = clamp(1 - Math.abs(e) / 0.30, 0, 1);
    var night = clamp(-e / 0.22, 0, 1);

    var R = 260;
    sun.position.set(Math.sin(s.azim) * Math.cos(e) * R,
                     Math.max(Math.sin(e), -0.12) * R,
                     -Math.cos(s.azim) * Math.cos(e) * R);
    sun.target.position.set(0, 6, 6);
    sun.intensity = 0.06 + day * 2.15;
    sun.color.setHSL(lerp(0.045, 0.11, day * day), lerp(0.82, 0.30, day), lerp(0.56, 0.66, day));
    hemi.intensity = 0.30 + day * 0.16;
    hemi.color.setHSL(0.60, lerp(0.28, 0.42, day), lerp(0.22, 0.46, day));
    hemi.groundColor.setHSL(0.07, 0.22, lerp(0.06, 0.13, day));
    bounce.intensity = 0.10 + day * 0.14;
    renderer.toneMappingExposure = lerp(0.92, 1.04, day);
    setNight(night);

    skyUniforms.top.value.setHSL(0.60, lerp(0.55, 0.58, day), lerp(0.055, 0.28, day));
    skyUniforms.mid.value.setHSL(lerp(0.58, 0.56, day), lerp(0.35, 0.34, day), lerp(0.10, 0.46, day));
    skyUniforms.bot.value.setHSL(lerp(0.055, 0.10, 1 - dusk), lerp(0.68, 0.28, day), lerp(0.16, 0.58, day));
    skyUniforms.sunDir.value.copy(sun.position).normalize();
    skyUniforms.glow.value = 0.22 + dusk * 1.45;
    skyUniforms.night.value = night;
    scene.fog.color.copy(skyUniforms.mid.value)
      .lerp(new THREE.Color(0x0b1017), 0.38 + night * 0.40);
    scene.fog.density = lerp(0.0038, 0.0024, day);

    var th = TRAFFIC.hourly[String(state.hour)];
    var flow = state.trafficOn ? th.all_motor : 0;

    weatherStationItems.forEach(function (item) {
      var installed = d.getTime() >= item.installedAt;
      item.materials.forEach(function (material) {
        material.opacity = installed ? 1 : 0.16;
        material.depthWrite = installed;
      });
      item.label.userData.suppressed = !installed;
    });

    // Every pod carries its own measurement, so the network reads at a glance.
    var readings = {};
    monitorItems.forEach(function (item) {
      var v = obsPM(String(item.data.unit), state.frame);
      readings[item.data.unit] = v;
      var col = pmColour(v);
      item.pod.material.color.copy(col);
      item.pod.material.emissive.copy(col);
      item.pod.material.emissiveIntensity = v === null ? 0.05 : 0.12 + clamp(v / 90, 0, 1) * 0.65;
      if (item.glow) {
        item.glow.material.color.copy(col);
        item.glow.material.opacity = v === null ? 0.08 : 0.16 + clamp(v / 90, 0, 1) * 0.42;
        var gs = 0.95 + clamp((v || 0) / 90, 0, 1) * 0.85;
        item.glow.scale.set(gs, gs, 1);
      }
    });

    window.__hud(h, s, flow, th, night, d, readings, weather);
    return { night: night, flow: flow, th: th, weather: weather };
  }

  /* -------------------------------------------------------------------- loop */
  var clock = new THREE.Clock();
  var acc = 0;
  function resize() {
    var w = canvas.clientWidth, hgt = canvas.clientHeight;
    if (canvas.width !== w * renderer.getPixelRatio() || canvas.height !== hgt * renderer.getPixelRatio()) {
      renderer.setSize(w, hgt, false);
      camera.aspect = w / Math.max(hgt, 1);
      camera.updateProjectionMatrix();
    }
  }
  function frame() {
    var dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    resize();
    if (state.playing) {
      acc += dt;
      if (acc > 0.28) {
        acc = 0;
        state.frame = (state.frame + 1) % Math.max(activeCount(), 1);
        window.__syncTime();
      }
    }
    var env = applyHour();

    var wr = state.hasWind ? (state.wind + 180) * Math.PI / 180 : 0;
    var wind = state.hasWind
      ? new THREE.Vector3(Math.sin(wr), 0, -Math.cos(wr)).multiplyScalar(state.windSpeed)
      : new THREE.Vector3();

    GEO.sources.forEach(function (s) {
      var on = state.enabled[s.key];
      var scenarioStrength = window.__plumeScenarioStrength
        ? window.__plumeScenarioStrength(s.key, state.hour)
        : window.__schedule(s.key, state.hour);
      // A valid calm observation has no meaningful direction, but the source
      // does not stop emitting: keep the zero horizontal wind vector and let
      // buoyancy/turbulence disperse the plume. Missing or non-calm weather
      // without a direction remains unavailable and suppresses the scenario.
      var usableWeather = state.hasWind || state.windIsCalm;
      var strength = on && usableWeather ? scenarioStrength : 0;
      plumes[s.key].update(dt, wind, strength, t);
      stacks[s.key].halo.material.opacity = strength * 0.30 * (0.7 + 0.3 * Math.sin(t * 3));
      stacks[s.key].halo.scale.setScalar(1 + strength * 0.5);
      if (stacks[s.key].label) stacks[s.key].label.visible = state.showLabels && on;
    });
    declutter();

    if (traffic) traffic.update(dt, env.flow, TRAFFIC.speed_kmh_animation, env.night);

    applyCam();
    renderer.render(scene, camera);
    if (!window.__readyMarked && t > 0.12) {
      window.__readyMarked = true;
      document.body.classList.add("ready");
    }
    requestAnimationFrame(frame);
  }

  var initialView = urlState.get("view");
  goto(initialView && VIEWS[initialView] ? initialView : "deployment", true);
  window.__syncTime();
  frame();
  window.__traffic = traffic;
  window.__scene = { scene: scene, camera: camera, goto: goto, applyHour: applyHour };
})();
