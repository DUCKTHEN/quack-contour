(function () {
  "use strict";

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

    var positionArray = new Float32Array(positions);
    var indexArray = new Uint32Array(indices);
    return {
      positions: positionArray.buffer,
      indices: indexArray.buffer,
      vertexCount: positionArray.length / 3,
      faceCount: rawFaceCount,
      triangleCount: indexArray.length / 3,
      byteLength: text.length
    };
  }

  self.addEventListener("message", function (event) {
    var id = event.data.id;
    try {
      var parsed = parseObj(String(event.data.text || ""));
      self.postMessage({ id: id, ok: true, parsed: parsed }, [parsed.positions, parsed.indices]);
    } catch (error) {
      self.postMessage({ id: id, ok: false, error: error.message || String(error) });
    }
  });
}());
