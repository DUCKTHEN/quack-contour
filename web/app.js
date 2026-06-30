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
    compareColor: "#9aa3aa"
  };

  var grid = new THREE.GridHelper(7, 28, 0xd5dde7, 0xe5ebf2);
  grid.position.y = 0;
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
  var objWorker = window.Worker ? new Worker("web/obj-worker.js") : null;
  var parseJobId = 0;

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
        updateDiagnostics();
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
    guides.visible = state.showGuides;
    if (!state.showGuides) return;

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

  document.getElementById("show-primary").addEventListener("change", updateLayout);
  document.getElementById("show-compare").addEventListener("change", updateLayout);
  document.getElementById("show-guides").addEventListener("change", function (event) { state.showGuides = event.target.checked; updateGuides(); });
  document.getElementById("show-grid").addEventListener("change", function (event) { state.showGrid = event.target.checked; grid.visible = state.showGrid; setActiveButton("grid-floating", state.showGrid); });
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
  document.getElementById("fit-view-floating").addEventListener("click", fitView);
  document.getElementById("reset-view").addEventListener("click", fitView);
  document.getElementById("grid-floating").addEventListener("click", function () {
    state.showGrid = !state.showGrid;
    grid.visible = state.showGrid;
    document.getElementById("show-grid").checked = state.showGrid;
    setActiveButton("grid-floating", state.showGrid);
  });
  document.getElementById("camera-floating").addEventListener("click", function () {
    state.orthographic = !state.orthographic;
    document.getElementById("orthographic").checked = state.orthographic;
    setActiveButton("camera-floating", state.orthographic);
  });

  canvas.addEventListener("pointerdown", function (event) {
    controls.dragging = true;
    controls.lastX = event.clientX;
    controls.lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", function (event) {
    if (!controls.dragging) return;
    var dx = event.clientX - controls.lastX;
    var dy = event.clientY - controls.lastY;
    controls.lastX = event.clientX;
    controls.lastY = event.clientY;
    controls.yaw -= dx * 0.008;
    controls.pitch = Math.max(-0.65, Math.min(0.65, controls.pitch - dy * 0.006));
  });
  canvas.addEventListener("pointerup", function (event) {
    controls.dragging = false;
    canvas.releasePointerCapture(event.pointerId);
  });
  canvas.addEventListener("wheel", function (event) {
    event.preventDefault();
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
  fitView();
  render();
}());