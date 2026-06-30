(function () {
  "use strict";

  var canvas = document.getElementById("viewer");
  var wrap = document.querySelector(".viewer-wrap");
  var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0xf7f8f9, 1);

  var scene = new THREE.Scene();
  var group = new THREE.Group();
  scene.add(group);

  var perspectiveCamera = new THREE.PerspectiveCamera(35, 1, 0.1, 1000);
  var orthoCamera = new THREE.OrthographicCamera(-5, 5, 5, -5, 0.1, 1000);
  var camera = orthoCamera;

  var controls = {
    target: new THREE.Vector3(0, 1.8, 0),
    distance: 8,
    yaw: 0,
    pitch: 0.08,
    dragging: false,
    lastX: 0,
    lastY: 0
  };

  var state = {
    primary: null,
    compare: null,
    layout: "side",
    showGuides: true,
    showGrid: true,
    orthographic: true,
    primaryOpacity: 1,
    compareOpacity: 1,
    primaryColor: "#202020",
    compareColor: "#9aa3aa",
    underlay: {
      loaded: false,
      move: false,
      dragging: false,
      x: 0,
      y: 0,
      scale: 1,
      rotation: 0,
      lastX: 0,
      lastY: 0
    }
  };

  var grid = new THREE.GridHelper(7, 28, 0xd5dde7, 0xe5ebf2);
  grid.position.y = 0;
  grid.visible = false;
  scene.add(grid);

  var guides = new THREE.Group();
  scene.add(guides);

  var ambient = new THREE.AmbientLight(0xffffff, 0.75);
  scene.add(ambient);
  var light = new THREE.DirectionalLight(0xffffff, 0.55);
  light.position.set(4, 7, 6);
  scene.add(light);

  var primaryFile = document.getElementById("primary-file");
  var compareFile = document.getElementById("compare-file");
  var primaryName = document.getElementById("primary-name");
  var compareName = document.getElementById("compare-name");
  var diagnostics = document.getElementById("diagnostics");
  var supportText = document.getElementById("support-text");
  var viewerEmpty = document.getElementById("viewer-empty");
  var objWorker = window.Worker ? new Worker("web/obj-worker.js") : null;
  var parseJobId = 0;
  var underlayFile = document.getElementById("underlay-file");
  var underlayLayer = document.getElementById("underlay-layer");
  var underlayImage = document.getElementById("underlay-image");
  var moveUnderlayButton = document.getElementById("move-underlay");
  var clearUnderlayButton = document.getElementById("clear-underlay");
  var generateTopViewButton = document.getElementById("generate-top-view");
  var topViewCanvas = document.getElementById("topview-canvas");
  var topViewStatus = document.getElementById("topview-status");
  var topViewLinks = document.getElementById("topview-links");
  var topViewUrls = [];
  var sectionDefs = [
    { name: "neck", label: "首", y: 3.42, color: "#8a603e" },
    { name: "shoulder", label: "肩", y: 2.82, color: "#9156dc" },
    { name: "bust", label: "バスト", y: 2.62, color: "#d90535" },
    { name: "under_bust", label: "アンダーバスト", y: 2.38, color: "#f58722" },
    { name: "waist", label: "ウエスト", y: 1.92, color: "#0d8cc7" },
    { name: "hip_upper", label: "ヒップ上側", y: 1.42, color: "#37985f" },
    { name: "hip", label: "ヒップ", y: 0.98, color: "#202020" }
  ];

  function updateSceneVisibility() {
    var hasModel = Boolean(state.primary || state.compare);
    grid.visible = hasModel && state.showGrid;
    guides.visible = hasModel && state.showGuides;
    if (viewerEmpty) viewerEmpty.classList.toggle("is-hidden", hasModel || state.underlay.loaded);
  }

  function hasAnyModel() {
    return Boolean(state.primary || state.compare);
  }
  function makeMaterial(color, opacity) {
    return new THREE.MeshLambertMaterial({
      color: new THREE.Color(color),
      transparent: opacity < 1,
      opacity: opacity,
      side: THREE.DoubleSide,
      depthWrite: opacity >= 0.98
    });
  }

  function parseObj(text) {
    var positions = [];
    var indices = [];
    var rawFaceCount = 0;
    var lines = text.split(/\r?\n/);

    for (var i = 0; i < lines.length; i += 1) {
      var line = lines[i].trim();
      if (!line || line.charAt(0) === "#") continue;
      var parts = line.split(/\s+/);
      if (parts[0] === "v" && parts.length >= 4) {
        positions.push(parseFloat(parts[1]), parseFloat(parts[2]), parseFloat(parts[3]));
      } else if (parts[0] === "f" && parts.length >= 4) {
        rawFaceCount += 1;
        var face = [];
        for (var j = 1; j < parts.length; j += 1) {
          var token = parts[j].split("/")[0];
          var index = parseInt(token, 10);
          if (!Number.isFinite(index)) continue;
          if (index < 0) index = positions.length / 3 + index + 1;
          face.push(index - 1);
        }
        for (var k = 1; k + 1 < face.length; k += 1) {
          indices.push(face[0], face[k], face[k + 1]);
        }
      }
    }

    if (!positions.length || !indices.length) {
      throw new Error("OBJの頂点または面を読み込めませんでした。");
    }

    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    return {
      geometry: geometry,
      vertexCount: positions.length / 3,
      faceCount: rawFaceCount,
      triangleCount: indices.length / 3,
      byteLength: text.length
    };
  }

  function parseObjAsync(text) {
    if (!objWorker) {
      return Promise.resolve(parseObj(text));
    }

    return new Promise(function (resolve, reject) {
      var jobId = parseJobId + 1;
      parseJobId = jobId;

      function cleanup() {
        objWorker.removeEventListener("message", onMessage);
        objWorker.removeEventListener("error", onError);
      }

      function onMessage(event) {
        if (!event.data || event.data.id !== jobId) return;
        cleanup();
        if (!event.data.ok) {
          reject(new Error(event.data.error || "OBJの読み込みに失敗しました。"));
          return;
        }
        resolve(createParsedGeometry(event.data.parsed));
      }

      function onError(error) {
        cleanup();
        reject(error);
      }

      objWorker.addEventListener("message", onMessage);
      objWorker.addEventListener("error", onError);
      objWorker.postMessage({ id: jobId, text: text });
    }).catch(function () {
      return parseObj(text);
    });
  }

  function createParsedGeometry(parsed) {
    var geometry = new THREE.BufferGeometry();
    var positions = new Float32Array(parsed.positions);
    var indices = new Uint32Array(parsed.indices);
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    return {
      geometry: geometry,
      vertexCount: parsed.vertexCount,
      faceCount: parsed.faceCount,
      triangleCount: parsed.triangleCount,
      byteLength: parsed.byteLength
    };
  }

  function loadUnderlay(file) {
    if (!file || !underlayImage || !underlayLayer) return;
    var reader = new FileReader();
    reader.onload = function () {
      state.underlay.loaded = true;
      state.underlay.x = 0;
      state.underlay.y = 0;
      state.underlay.scale = 1;
      state.underlay.rotation = 0;
      underlayImage.src = String(reader.result || "");
      underlayLayer.classList.add("is-visible");
      updateUnderlay();
      updateSceneVisibility();
      setUnderlayMove(true);
      supportText.textContent = "下絵を読み込みました。ドラッグで移動、ホイールで拡大縮小、Shift+ホイールで回転できます。";
    };
    reader.onerror = function () {
      supportText.textContent = "下絵画像を読み込めませんでした。";
    };
    reader.readAsDataURL(file);
  }

  function updateUnderlay() {
    if (!underlayImage) return;
    underlayImage.style.transform = "translate(-50%, -50%) translate(" + state.underlay.x + "px, " + state.underlay.y + "px) scale(" + state.underlay.scale + ") rotate(" + state.underlay.rotation + "deg)";
  }

  function setUnderlayMove(active) {
    state.underlay.move = Boolean(active && state.underlay.loaded);
    wrap.classList.toggle("is-underlay-move", state.underlay.move);
    if (moveUnderlayButton) moveUnderlayButton.classList.toggle("is-active", state.underlay.move);
  }

  function clearUnderlay() {
    state.underlay.loaded = false;
    state.underlay.dragging = false;
    state.underlay.move = false;
    if (underlayImage) underlayImage.removeAttribute("src");
    if (underlayLayer) underlayLayer.classList.remove("is-visible");
    setUnderlayMove(false);
    updateSceneVisibility();
    supportText.textContent = "下絵を削除しました。";
  }

  function normalizeMesh(mesh) {
    mesh.geometry.computeBoundingBox();
    var box = mesh.geometry.boundingBox;
    var size = new THREE.Vector3();
    var center = new THREE.Vector3();
    box.getSize(size);
    box.getCenter(center);
    var height = Math.max(size.y, 0.0001);
    var scale = 4 / height;
    mesh.scale.setScalar(scale);
    mesh.position.set(-center.x * scale, -box.min.y * scale, -center.z * scale);
    mesh.userData.originalSize = size.clone();
    mesh.userData.originalHeight = size.y;
  }

  function createModel(parsed, name, kind) {
    var color = kind === "primary" ? state.primaryColor : state.compareColor;
    var opacity = kind === "primary" ? state.primaryOpacity : state.compareOpacity;
    var mesh = new THREE.Mesh(parsed.geometry, makeMaterial(color, opacity));
    normalizeMesh(mesh);
    var modelGroup = new THREE.Group();
    modelGroup.add(mesh);
    modelGroup.userData = {
      name: name,
      kind: kind,
      stats: parsed,
      mesh: mesh
    };
    group.add(modelGroup);
    return modelGroup;
  }


  function clearTopViewOutput() {
    topViewUrls.forEach(function (url) { URL.revokeObjectURL(url); });
    topViewUrls = [];
    if (topViewLinks) topViewLinks.innerHTML = "";
    if (topViewStatus) topViewStatus.textContent = state.primary ? "上面図生成を押すと、現在の断面ガイドから断面図を作成します。" : "主OBJを読み込むと、断面の上面図を生成できます。";
    if (topViewCanvas) {
      var ctx = topViewCanvas.getContext("2d");
      ctx.clearRect(0, 0, topViewCanvas.width, topViewCanvas.height);
    }
  }

  function localYFromWorld(mesh, worldY) {
    return (worldY - mesh.position.y) / mesh.scale.y;
  }

  function triangleSectionPoint(a, b, planeY, eps) {
    var da = a.y - planeY;
    var db = b.y - planeY;
    if (Math.abs(da) <= eps && Math.abs(db) <= eps) return null;
    if ((da > eps && db > eps) || (da < -eps && db < -eps)) return null;
    var denom = da - db;
    if (Math.abs(denom) <= eps) return null;
    var t = da / denom;
    if (t < -eps || t > 1 + eps) return null;
    t = Math.max(0, Math.min(1, t));
    return {
      x: a.x + (b.x - a.x) * t,
      z: a.z + (b.z - a.z) * t
    };
  }

  function uniqueSectionPoints(points, tol) {
    var out = [];
    points.forEach(function (point) {
      var exists = out.some(function (other) {
        return Math.hypot(point.x - other.x, point.z - other.z) <= tol;
      });
      if (!exists) out.push(point);
    });
    return out;
  }

  function sliceMeshAtWorldY(model, worldY) {
    var mesh = model.userData.mesh;
    var geometry = mesh.geometry;
    var positions = geometry.attributes.position.array;
    var index = geometry.index ? geometry.index.array : null;
    var planeY = localYFromWorld(mesh, worldY);
    var eps = Math.max(mesh.userData.originalHeight || 1, 1) * 1e-6;
    var pointTol = Math.max(mesh.userData.originalHeight || 1, 1) * 1e-5;
    var segments = [];

    function vertexAt(i) {
      var p = i * 3;
      return { x: positions[p], y: positions[p + 1], z: positions[p + 2] };
    }

    var triangleCount = index ? index.length / 3 : positions.length / 9;
    for (var t = 0; t < triangleCount; t += 1) {
      var i0 = index ? index[t * 3] : t * 3;
      var i1 = index ? index[t * 3 + 1] : t * 3 + 1;
      var i2 = index ? index[t * 3 + 2] : t * 3 + 2;
      var a = vertexAt(i0);
      var b = vertexAt(i1);
      var c = vertexAt(i2);
      var hits = uniqueSectionPoints([
        triangleSectionPoint(a, b, planeY, eps),
        triangleSectionPoint(b, c, planeY, eps),
        triangleSectionPoint(c, a, planeY, eps)
      ].filter(Boolean), pointTol);
      if (hits.length >= 2) {
        segments.push({
          a: { x: hits[0].x * 100, z: hits[0].z * 100 },
          b: { x: hits[1].x * 100, z: hits[1].z * 100 }
        });
      }
    }
    return segments;
  }

  function samePoint(a, b, tol) {
    return Math.hypot(a.x - b.x, a.z - b.z) <= tol;
  }

  function pathLength(points) {
    var total = 0;
    for (var i = 1; i < points.length; i += 1) total += Math.hypot(points[i].x - points[i - 1].x, points[i].z - points[i - 1].z);
    if (points.length > 2 && samePoint(points[0], points[points.length - 1], 0.2)) total += Math.hypot(points[0].x - points[points.length - 1].x, points[0].z - points[points.length - 1].z);
    return total;
  }

  function connectSegments(segments) {
    var unused = segments.slice();
    var paths = [];
    var tol = 0.12;
    while (unused.length) {
      var first = unused.pop();
      var path = [first.a, first.b];
      var extended = true;
      while (extended) {
        extended = false;
        for (var i = unused.length - 1; i >= 0; i -= 1) {
          var seg = unused[i];
          var head = path[0];
          var tail = path[path.length - 1];
          if (samePoint(tail, seg.a, tol)) {
            path.push(seg.b); unused.splice(i, 1); extended = true; break;
          }
          if (samePoint(tail, seg.b, tol)) {
            path.push(seg.a); unused.splice(i, 1); extended = true; break;
          }
          if (samePoint(head, seg.b, tol)) {
            path.unshift(seg.a); unused.splice(i, 1); extended = true; break;
          }
          if (samePoint(head, seg.a, tol)) {
            path.unshift(seg.b); unused.splice(i, 1); extended = true; break;
          }
        }
      }
      if (path.length > 2) paths.push(path);
    }
    return paths.map(function (points) {
      return { points: points, perimeter: pathLength(points), closed: samePoint(points[0], points[points.length - 1], 0.25) };
    }).sort(function (a, b) { return b.perimeter - a.perimeter; });
  }

  function buildTopViewSections(model) {
    return sectionDefs.map(function (section) {
      var paths = connectSegments(sliceMeshAtWorldY(model, section.y));
      var maxPerimeter = paths.length ? paths[0].perimeter : 0;
      var visiblePaths = paths.filter(function (path, index) {
        return index < 4 && path.perimeter >= Math.max(maxPerimeter * 0.08, 1);
      });
      return {
        name: section.name,
        label: section.label,
        color: section.color,
        height_world: section.y,
        perimeter_cm: maxPerimeter,
        paths: visiblePaths
      };
    });
  }

  function drawDashedLine(ctx, x1, y1, x2, y2) {
    ctx.save();
    ctx.setLineDash([8, 8]);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.restore();
  }

  function generateTopView() {
    if (!topViewCanvas || !topViewStatus) return;
    if (!state.primary) {
      topViewStatus.textContent = "先に主OBJを読み込んでください。";
      return;
    }
    topViewStatus.textContent = "上面断面図を生成中です...";
    topViewLinks.innerHTML = "";

    setTimeout(function () {
      var models = [
        { key: "primary", label: "主モデル", model: state.primary, dashed: false }
      ];
      if (state.compare) models.push({ key: "compare", label: "比較モデル", model: state.compare, dashed: true });
      var result = models.map(function (entry) {
        return {
          key: entry.key,
          label: entry.label,
          dashed: entry.dashed,
          file: entry.model.userData.name,
          sections: buildTopViewSections(entry.model)
        };
      });

      var allPoints = [];
      result.forEach(function (modelResult) {
        modelResult.sections.forEach(function (section) {
          section.paths.forEach(function (path) {
            path.points.forEach(function (point) { allPoints.push(point); });
          });
        });
      });
      if (!allPoints.length) {
        topViewStatus.textContent = "断面ループが見つかりませんでした。モデルの向きや断面高さを確認してください。";
        return;
      }

      var ctx = topViewCanvas.getContext("2d");
      var width = topViewCanvas.width;
      var height = topViewCanvas.height;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, width, height);

      var minX = Math.min.apply(null, allPoints.map(function (p) { return p.x; }));
      var maxX = Math.max.apply(null, allPoints.map(function (p) { return p.x; }));
      var minZ = Math.min.apply(null, allPoints.map(function (p) { return p.z; }));
      var maxZ = Math.max.apply(null, allPoints.map(function (p) { return p.z; }));
      minX = Math.min(minX, 0); maxX = Math.max(maxX, 0);
      minZ = Math.min(minZ, 0); maxZ = Math.max(maxZ, 0);
      var pad = 54;
      var legendW = 250;
      var plotW = width - pad * 2 - legendW;
      var plotH = height - pad * 2;
      var scale = Math.min(plotW / Math.max(maxX - minX, 1), plotH / Math.max(maxZ - minZ, 1));
      var ox = pad + plotW / 2 - ((minX + maxX) / 2) * scale;
      var oy = pad + plotH / 2 + ((minZ + maxZ) / 2) * scale;
      function tx(point) { return ox + point.x * scale; }
      function ty(point) { return oy - point.z * scale; }

      ctx.strokeStyle = "#d8dde4";
      ctx.lineWidth = 1.4;
      drawDashedLine(ctx, tx({ x: 0, z: minZ }), ty({ x: 0, z: minZ }), tx({ x: 0, z: maxZ }), ty({ x: 0, z: maxZ }));
      drawDashedLine(ctx, tx({ x: minX, z: 0 }), ty({ x: minX, z: 0 }), tx({ x: maxX, z: 0 }), ty({ x: maxX, z: 0 }));
      ctx.fillStyle = "#6e7681";
      ctx.font = "16px system-ui, sans-serif";
      ctx.fillText("前", tx({ x: 0, z: maxZ }) + 10, ty({ x: 0, z: maxZ }) + 18);
      ctx.fillText("後", tx({ x: 0, z: minZ }) + 10, ty({ x: 0, z: minZ }) - 8);

      result.forEach(function (modelResult) {
        modelResult.sections.forEach(function (section) {
          ctx.strokeStyle = section.color;
          ctx.lineWidth = modelResult.dashed ? 2.2 : 3;
          ctx.setLineDash(modelResult.dashed ? [10, 7] : []);
          section.paths.forEach(function (path) {
            if (path.points.length < 2) return;
            ctx.beginPath();
            ctx.moveTo(tx(path.points[0]), ty(path.points[0]));
            for (var i = 1; i < path.points.length; i += 1) ctx.lineTo(tx(path.points[i]), ty(path.points[i]));
            if (path.closed) ctx.closePath();
            ctx.stroke();
          });
        });
      });
      ctx.setLineDash([]);

      var lx = width - legendW + 10;
      var ly = 42;
      ctx.fillStyle = "#2f363d";
      ctx.font = "bold 20px system-ui, sans-serif";
      ctx.fillText("測定断面", lx, ly);
      ly += 28;
      sectionDefs.forEach(function (section) {
        var primary = result[0].sections.find(function (item) { return item.name === section.name; });
        var compare = result[1] && result[1].sections.find(function (item) { return item.name === section.name; });
        ctx.strokeStyle = section.color;
        ctx.lineWidth = 5;
        ctx.beginPath();
        ctx.moveTo(lx, ly - 4);
        ctx.lineTo(lx + 34, ly - 4);
        ctx.stroke();
        ctx.fillStyle = section.color;
        ctx.font = "bold 16px system-ui, sans-serif";
        ctx.fillText(section.label + ": " + (primary && primary.perimeter_cm ? primary.perimeter_cm.toFixed(2) + " cm" : "--"), lx + 45, ly + 2);
        if (compare) {
          ctx.fillStyle = "#6e7681";
          ctx.font = "13px system-ui, sans-serif";
          ctx.fillText("比較: " + (compare.perimeter_cm ? compare.perimeter_cm.toFixed(2) + " cm" : "--"), lx + 45, ly + 21);
          ly += 46;
        } else {
          ly += 34;
        }
      });
      ctx.fillStyle = "#6e7681";
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText("実線=主 / 破線=比較", lx, height - 28);

      topViewUrls.forEach(function (url) { URL.revokeObjectURL(url); });
      var jsonBlob = new Blob([JSON.stringify({ generated_at: new Date().toISOString(), models: result }, null, 2)], { type: "application/json" });
      var jsonUrl = URL.createObjectURL(jsonBlob);
      topViewUrls = [jsonUrl];
      topViewLinks.innerHTML = '<a download="quack-contour-topview.png" href="' + topViewCanvas.toDataURL("image/png") + '">PNG</a>' +
        '<a download="quack-contour-topview.json" href="' + jsonUrl + '">JSON</a>';
      topViewStatus.textContent = "上面断面図を生成しました。";
    }, 20);
  }

  function loadFile(file, kind) {
    if (!file) return;
    var reader = new FileReader();
    wrap.classList.add("is-loading");
    supportText.textContent = (kind === "primary" ? "主モデル" : "比較モデル") + "を読み込み中です。";
    reader.onload = function () {
      parseObjAsync(String(reader.result || "")).then(function (parsed) {
        if (state[kind]) {
          group.remove(state[kind]);
          disposeObject(state[kind]);
        }
        state[kind] = createModel(parsed, file.name, kind);
        if (kind === "primary") primaryName.textContent = "主モデル: " + file.name;
        if (kind === "compare") compareName.textContent = "比較モデル: " + file.name;
        supportText.textContent = kind === "primary" ? "主モデルを読み込みました。比較OBJを追加できます。" : "比較モデルを読み込みました。濃さや配置を調整できます。";
        updateLayout();
        updateSceneVisibility();
        updateDiagnostics();
        clearTopViewOutput();
        fitView();
      }).catch(function (error) {
        supportText.textContent = error.message;
      }).finally(function () {
        wrap.classList.remove("is-loading");
      });
    };
    reader.onerror = function () {
      wrap.classList.remove("is-loading");
      supportText.textContent = "ファイルを読み込めませんでした。";
    };
    reader.readAsText(file);
  }

  function disposeObject(object) {
    object.traverse(function (child) {
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose();
    });
  }

  function updateMaterial(kind) {
    var model = state[kind];
    if (!model) return;
    var mesh = model.userData.mesh;
    var color = kind === "primary" ? state.primaryColor : state.compareColor;
    var opacity = kind === "primary" ? state.primaryOpacity : state.compareOpacity;
    mesh.material.dispose();
    mesh.material = makeMaterial(color, opacity);
  }

  function updateLayout() {
    var primary = state.primary;
    var compare = state.compare;
    var showPrimary = document.getElementById("show-primary").checked;
    var showCompare = document.getElementById("show-compare").checked;
    if (primary) primary.visible = showPrimary;
    if (compare) compare.visible = showCompare;

    if (state.layout === "side") {
      if (primary) primary.position.x = compare ? 1.45 : 0;
      if (compare) compare.position.x = primary ? -1.45 : 0;
    } else {
      if (primary) primary.position.x = 0;
      if (compare) compare.position.x = 0;
    }
    updateGuides();
  }

  function updateGuides() {
    while (guides.children.length) {
      var child = guides.children.pop();
      disposeObject(child);
    }
    guides.visible = hasAnyModel() && state.showGuides;
    if (!hasAnyModel() || !state.showGuides) return;

    var levels = [3.42, 2.82, 2.62, 2.38, 1.92, 1.42, 0.98];
    var colors = [0x8a603e, 0x9156dc, 0xd90535, 0xf58722, 0x0d8cc7, 0x37985f, 0x202020];
    for (var i = 0; i < levels.length; i += 1) {
      var material = new THREE.LineBasicMaterial({ color: colors[i], transparent: true, opacity: 0.86 });
      var geometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-2.4, levels[i], 0.02),
        new THREE.Vector3(2.4, levels[i], 0.02)
      ]);
      guides.add(new THREE.Line(geometry, material));
    }
  }

  function updateDiagnostics() {
    var items = [];
    if (state.primary) items.push(diagnosticCard(state.primary, "主モデル"));
    if (state.compare) items.push(diagnosticCard(state.compare, "比較モデル"));
    if (!items.length) {
      diagnostics.innerHTML = "<p class=\"hint\">まだモデルが読み込まれていません。</p>";
      return;
    }
    diagnostics.innerHTML = items.join("");
  }

  function diagnosticCard(model, label) {
    var stats = model.userData.stats;
    var mesh = model.userData.mesh;
    var size = mesh.userData.originalSize || new THREE.Vector3();
    var heightCm = size.y * 100;
    var mb = stats.byteLength / 1024 / 1024;
    var warning = stats.triangleCount > 150000 || mb > 20;
    var status = warning ? "注意" : "OK";
    var statusClass = warning ? "status-warn" : "status-ok";
    return "<article class=\"diag-card\">" +
      "<strong><span>" + escapeHtml(label) + "</span><span class=\"" + statusClass + "\">" + status + "</span></strong>" +
      "<dl>" +
      "<dt>ファイル</dt><dd>" + escapeHtml(model.userData.name) + "</dd>" +
      "<dt>頂点</dt><dd>" + formatNumber(stats.vertexCount) + "</dd>" +
      "<dt>面</dt><dd>" + formatNumber(stats.faceCount) + "</dd>" +
      "<dt>三角面</dt><dd>" + formatNumber(stats.triangleCount) + "</dd>" +
      "<dt>推定身長</dt><dd>" + (Number.isFinite(heightCm) ? heightCm.toFixed(1) + " cm" : "--") + "</dd>" +
      "<dt>OBJサイズ</dt><dd>" + mb.toFixed(1) + " MB</dd>" +
      "</dl>" +
      "</article>";
  }

  function formatNumber(value) {
    return Math.round(value).toLocaleString("ja-JP");
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, function (ch) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" })[ch];
    });
  }

  function resize() {
    var rect = wrap.getBoundingClientRect();
    var width = Math.max(1, Math.floor(rect.width));
    var height = Math.max(1, Math.floor(rect.height));
    renderer.setSize(width, height, false);
    perspectiveCamera.aspect = width / height;
    perspectiveCamera.updateProjectionMatrix();
    var aspect = width / height;
    var viewHeight = 5.4;
    orthoCamera.left = -viewHeight * aspect / 2;
    orthoCamera.right = viewHeight * aspect / 2;
    orthoCamera.top = viewHeight / 2;
    orthoCamera.bottom = -viewHeight / 2;
    orthoCamera.updateProjectionMatrix();
  }

  function updateCamera() {
    camera = state.orthographic ? orthoCamera : perspectiveCamera;
    var x = Math.sin(controls.yaw) * Math.cos(controls.pitch) * controls.distance;
    var y = Math.sin(controls.pitch) * controls.distance + 1.9;
    var z = Math.cos(controls.yaw) * Math.cos(controls.pitch) * controls.distance;
    camera.position.set(x, y, z);
    camera.lookAt(controls.target);
  }

  function fitView() {
    controls.yaw = 0;
    controls.pitch = 0.08;
    controls.target.set(0, 2, 0);
    controls.distance = state.layout === "side" && state.primary && state.compare ? 8.5 : 7.2;
    updateCamera();
  }

  function render() {
    resize();
    updateCamera();
    renderer.render(scene, camera);
    requestAnimationFrame(render);
  }

  function setActiveButton(id, active) {
    var button = document.getElementById(id);
    if (button) button.classList.toggle("is-active", active);
  }

  primaryFile.addEventListener("change", function (event) { loadFile(event.target.files[0], "primary"); });
  compareFile.addEventListener("change", function (event) { loadFile(event.target.files[0], "compare"); });
  if (underlayFile) underlayFile.addEventListener("change", function (event) { loadUnderlay(event.target.files[0]); });
  if (moveUnderlayButton) moveUnderlayButton.addEventListener("click", function () { setUnderlayMove(!state.underlay.move); });
  if (clearUnderlayButton) clearUnderlayButton.addEventListener("click", clearUnderlay);

  document.getElementById("show-primary").addEventListener("change", updateLayout);
  document.getElementById("show-compare").addEventListener("change", updateLayout);
  document.getElementById("show-guides").addEventListener("change", function (event) { state.showGuides = event.target.checked; updateGuides(); updateSceneVisibility(); });
  document.getElementById("show-grid").addEventListener("change", function (event) { state.showGrid = event.target.checked; updateSceneVisibility(); setActiveButton("grid-floating", state.showGrid); });
  document.getElementById("orthographic").addEventListener("change", function (event) { state.orthographic = event.target.checked; setActiveButton("camera-floating", state.orthographic); });

  document.getElementById("primary-color").addEventListener("input", function (event) { state.primaryColor = event.target.value; updateMaterial("primary"); });
  document.getElementById("compare-color").addEventListener("input", function (event) { state.compareColor = event.target.value; updateMaterial("compare"); });

  document.getElementById("primary-opacity").addEventListener("input", function (event) {
    state.primaryOpacity = Number(event.target.value) / 100;
    document.getElementById("primary-opacity-value").textContent = event.target.value + "%";
    updateMaterial("primary");
  });
  document.getElementById("compare-opacity").addEventListener("input", function (event) {
    state.compareOpacity = Number(event.target.value) / 100;
    document.getElementById("compare-opacity-value").textContent = event.target.value + "%";
    updateMaterial("compare");
  });

  document.getElementById("layout-mode").addEventListener("change", function (event) { state.layout = event.target.value; updateLayout(); fitView(); });
  document.getElementById("fit-view").addEventListener("click", fitView);
  if (generateTopViewButton) generateTopViewButton.addEventListener("click", generateTopView);
  document.getElementById("fit-view-floating").addEventListener("click", fitView);
  document.getElementById("reset-view").addEventListener("click", fitView);
  document.getElementById("grid-floating").addEventListener("click", function () {
    state.showGrid = !state.showGrid;
    updateSceneVisibility();
    document.getElementById("show-grid").checked = state.showGrid;
    setActiveButton("grid-floating", state.showGrid);
  });
  document.getElementById("camera-floating").addEventListener("click", function () {
    state.orthographic = !state.orthographic;
    document.getElementById("orthographic").checked = state.orthographic;
    setActiveButton("camera-floating", state.orthographic);
  });

  canvas.addEventListener("pointerdown", function (event) {
    if (state.underlay.move && state.underlay.loaded) {
      state.underlay.dragging = true;
      state.underlay.lastX = event.clientX;
      state.underlay.lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
      return;
    }
    controls.dragging = true;
    controls.lastX = event.clientX;
    controls.lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", function (event) {
    if (state.underlay.dragging) {
      var ux = event.clientX - state.underlay.lastX;
      var uy = event.clientY - state.underlay.lastY;
      state.underlay.lastX = event.clientX;
      state.underlay.lastY = event.clientY;
      state.underlay.x += ux;
      state.underlay.y += uy;
      updateUnderlay();
      return;
    }
    if (!controls.dragging) return;
    var dx = event.clientX - controls.lastX;
    var dy = event.clientY - controls.lastY;
    controls.lastX = event.clientX;
    controls.lastY = event.clientY;
    controls.yaw -= dx * 0.008;
    controls.pitch = Math.max(-0.65, Math.min(0.65, controls.pitch - dy * 0.006));
  });
  canvas.addEventListener("pointerup", function (event) {
    if (state.underlay.dragging) {
      state.underlay.dragging = false;
      canvas.releasePointerCapture(event.pointerId);
      return;
    }
    controls.dragging = false;
    canvas.releasePointerCapture(event.pointerId);
  });
  canvas.addEventListener("wheel", function (event) {
    event.preventDefault();
    if (state.underlay.move && state.underlay.loaded) {
      if (event.shiftKey) {
        state.underlay.rotation += event.deltaY > 0 ? 2 : -2;
      } else {
        var zoomFactor = event.deltaY > 0 ? 0.94 : 1.06;
        state.underlay.scale = Math.max(0.1, Math.min(8, state.underlay.scale * zoomFactor));
      }
      updateUnderlay();
      return;
    }
    controls.distance = Math.max(3.4, Math.min(18, controls.distance + event.deltaY * 0.008));
  }, { passive: false });

  ["dragenter", "dragover"].forEach(function (type) {
    wrap.addEventListener(type, function (event) { event.preventDefault(); wrap.classList.add("is-dragging"); });
  });
  ["dragleave", "drop"].forEach(function (type) {
    wrap.addEventListener(type, function (event) { event.preventDefault(); wrap.classList.remove("is-dragging"); });
  });
  wrap.addEventListener("drop", function (event) {
    var file = event.dataTransfer.files[0];
    if (!file) return;
    loadFile(file, state.primary ? "compare" : "primary");
  });

  setActiveButton("grid-floating", state.showGrid);
  setActiveButton("camera-floating", state.orthographic);
  updateDiagnostics();
  updateGuides();
  updateSceneVisibility();
  fitView();
  render();
}());