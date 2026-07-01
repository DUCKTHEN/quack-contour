"""Interactive local web UI for setting horizontal avatar section lines."""

from __future__ import annotations

import cgi
import copy
import csv
import json
import math
import mimetypes
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT
EXPORTS = ROOT / "outputs"
UPLOADS = ROOT / "uploads"
WEB_OUTPUTS = ROOT / "outputs"
DEFAULT_LOGO = ROOT / "assets" / "IconAhiru.png"
QUACK_SOUND = ROOT / "assets" / "sounds" / "duck-quacking-37392.mp3"
VENDOR = ROOT / "vendor"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import obj_section_tool  # noqa: E402
import compare_avatar_sections  # noqa: E402


def safe_name(value: str, fallback: str = "avatar_sections") -> str:
    value = Path(value or fallback).stem
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return value or fallback


def resolve_input_path(value: str) -> Path:
    path = Path(value.strip().strip('"'))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def ensure_obj_path(path: Path) -> Path:
    if path.suffix.lower() != ".obj":
        raise ValueError("現在はOBJのみ対応しています。OBJファイルを指定してください。")
    return path


def save_upload(field, target_dir: Path) -> Path | None:
    if field is None or not getattr(field, "filename", ""):
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (safe_name(field.filename, "upload") + Path(field.filename).suffix)
    with target.open("wb") as fh:
        while True:
            chunk = field.file.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    return target


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def load_line_rows(path: Path) -> list[dict[str, object]]:
    return obj_section_tool.load_lines(path)

def make_lines_path_from_form(form: cgi.FieldStorage, *, text_key: str, file_key: str, path_key: str, fallback_prefix: str) -> Path:
    upload = save_upload(form[file_key] if file_key in form else None, UPLOADS)
    lines_text = form.getfirst(text_key, "").strip()
    if lines_text:
        UPLOADS.mkdir(parents=True, exist_ok=True)
        lines_path = UPLOADS / f"{fallback_prefix}_{int(time.time() * 1000)}.csv"
        lines_path.write_text(lines_text, encoding="utf-8")
    else:
        lines_path = upload or resolve_input_path(form.getfirst(path_key, ""))
    if not lines_path.exists():
        raise FileNotFoundError(f"lines not found: {lines_path}")
    return lines_path


def make_mesh_path_from_form(form: cgi.FieldStorage, *, file_key: str, path_key: str, upload_dir: Path = UPLOADS) -> Path:
    upload = save_upload(form[file_key] if file_key in form else None, upload_dir)
    mesh_path = upload or resolve_input_path(form.getfirst(path_key, ""))
    if not mesh_path.exists():
        raise FileNotFoundError(f"mesh not found: {mesh_path}")
    return ensure_obj_path(mesh_path)


def config_from_lines(mesh_path: Path, lines_path: Path, title: str, display_height_cm: float | None) -> dict:
    args = SimpleNamespace(
        mesh=str(mesh_path),
        lines=str(lines_path),
        title=title,
        unit_scale=1.0,
        display_height_cm=display_height_cm,
    )
    return obj_section_tool.make_config(args)





def form_float(form: cgi.FieldStorage, key: str, default: float | None = None) -> float | None:
    raw = form.getfirst(key, "")
    if raw is None:
        return default
    raw = str(raw).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return value


def display_scale_from_form(form: cgi.FieldStorage, key: str) -> float:
    value = form_float(form, key, 1.0)
    if value is None:
        return 1.0
    return max(0.05, min(8.0, value))


def scale_section_measurements(sections: list[dict], scale: float) -> list[dict]:
    if abs(scale - 1.0) < 0.0001:
        return sections
    scaled = copy.deepcopy(sections)
    for section in scaled:
        if "height_from_floor_cm" in section:
            section["height_from_floor_cm"] = float(section["height_from_floor_cm"]) * scale
        if "mesh_y" in section:
            section["mesh_y"] = float(section["mesh_y"]) * scale
        if isinstance(section.get("points"), list):
            section["points"] = [(float(point[0]) * scale, float(point[1]) * scale) for point in section["points"]]
        summary = section.get("summary")
        if isinstance(summary, dict):
            if "perimeter" in summary:
                summary["perimeter"] = float(summary["perimeter"]) * scale
            if "centroid" in summary and isinstance(summary["centroid"], list):
                summary["centroid"] = [float(value) * scale for value in summary["centroid"]]
            if "bbox" in summary and isinstance(summary["bbox"], list):
                summary["bbox"] = [float(value) * scale for value in summary["bbox"]]
        spine = section.get("spine_estimate")
        if isinstance(spine, dict) and "z" in spine:
            spine["z"] = float(spine["z"]) * scale
        scan_best = section.get("scan_best")
        if isinstance(scan_best, list):
            for record in scan_best:
                if not isinstance(record, dict):
                    continue
                if "height_from_floor_cm" in record:
                    record["height_from_floor_cm"] = float(record["height_from_floor_cm"]) * scale
                if "perimeter" in record:
                    record["perimeter"] = float(record["perimeter"]) * scale
                if "diff" in record:
                    record["diff"] = float(record["diff"]) * scale
    return scaled

def apply_interactive_generation_limits(config: dict) -> dict:
    """Keep browser-triggered section generation responsive.

    Imported line templates may scan broad height ranges at fine steps. That is useful
    for offline measurement passes, but too expensive for repeated UI generation.
    """
    limited = dict(config)
    sections = []
    for row in limited.get("sections", []):
        section = dict(row)
        if section.get("scan", True):
            try:
                section["scan_around_cm"] = min(float(section.get("scan_around_cm", 2.0) or 2.0), 2.0)
            except (TypeError, ValueError):
                section["scan_around_cm"] = 2.0
            try:
                section["scan_step_cm"] = max(float(section.get("scan_step_cm", 0.75) or 0.75), 0.75)
            except (TypeError, ValueError):
                section["scan_step_cm"] = 0.75
        sections.append(section)
    limited["sections"] = sections
    return limited


def rows_to_csv(rows: list[dict[str, object]]) -> str:
    fields = [
        "name",
        "label",
        "color",
        "height_from_floor_cm",
        "height_ratio",
        "expected_cm",
        "scan_around_cm",
        "scan_step_cm",
        "select",
        "scan",
    ]
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    return out.getvalue()


def parse_obj_preview(path: Path, max_faces: int = 2500) -> dict:
    path = ensure_obj_path(path)
    vertices_np, faces_np, loader = obj_section_tool.meshcut.load_mesh(path)
    vertices = vertices_np.tolist()
    faces = faces_np.astype(int).tolist()
    stride = max(1, math.ceil(len(faces) / max_faces))
    sampled_faces = faces[::stride]
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return {
        "vertices": vertices,
        "faces": sampled_faces,
        "silhouette_faces": faces,
        "source_face_count": len(faces),
        "face_stride": stride,
        "loader": loader,
        "bounds": {
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        },
        "height_cm": max(ys) - min(ys),
    }


def html_page() -> bytes:
    return r"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Quack Contour</title>
  <link rel="icon" type="image/png" href="/asset/IconAhiru.png?v=contour">
  <link rel="shortcut icon" type="image/png" href="/favicon.ico?v=contour">
  <style>
    :root {
      --ink:#edf2f7; --muted:#9aa8b7; --line:#252b34; --panel:#101419; --paper:#101114;
      --panel-2:#151a21; --field:#171c24; --accent:#f2c037; --accent-2:#f2c037; --blue:#4aa3ff;
      --viewer:#ffffff; --left-panel-width:280px; --right-panel-width:320px;
    }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"Yu Gothic UI","Meiryo",system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; font-size:14px; color:var(--ink); background:var(--paper); overflow:hidden; }
    body.panel-resizing { cursor:col-resize; user-select:none; }
    body.panel-resizing #viewer, body.panel-resizing #webglViewer { pointer-events:none; }
    header { height:68px; display:flex; align-items:center; justify-content:flex-start; gap:26px; padding:10px 14px; border-bottom:1px solid var(--line); background:#101114; }
    .brand { display:flex; align-items:center; gap:12px; min-width:0; transform:translateY(3px); }
    .brand-text { display:grid; gap:0; }
    h1 { margin:0; font-size:22px; letter-spacing:0; color:var(--accent); line-height:1; font-weight:800; }
    .subtitle { margin-top:2px; color:var(--muted); font-size:12px; font-weight:600; }
    .brand-sound-button { display:grid; place-items:center; width:38px; height:38px; padding:0; border:0; border-radius:6px; background:transparent; cursor:pointer; box-shadow:none; }
    .brand-sound-button:hover, .brand-sound-button:focus-visible { background:transparent; filter:none; }
    .brand-sound-button:hover .duck, .brand-sound-button:focus-visible .duck { transform:translateY(-1px); box-shadow:0 0 0 2px rgba(242,192,55,.55); }
    .brand-sound-button:active .duck { transform:translateY(1px) scale(.98); }
    .brand-sound-button:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
    .header-actions { margin-left:auto; display:flex; align-items:center; gap:8px; }
    .header-tool-button, .language-toggle { min-width:88px; height:34px; padding:0 14px; border:1px solid #2b2e35; border-radius:4px; background:#15171c; color:var(--ink); font-weight:800; font-size:13px; display:flex; align-items:center; justify-content:center; cursor:pointer; user-select:none; }
    .header-tool-button:hover, .header-tool-button:focus-visible, .language-toggle:hover, .language-toggle:focus-visible { border-color:rgba(244,191,36,.72); color:#fff; outline:none; }
    .history-actions { display:flex; align-items:center; gap:6px; margin-right:4px; }
    .history-button { min-width:54px; height:34px; padding:0 10px; border:1px solid #2b2e35; border-radius:4px; background:#15171c; color:var(--accent); font-size:12px; font-weight:800; line-height:1; cursor:pointer; user-select:none; }
    .history-button:hover, .history-button:focus-visible { border-color:rgba(244,191,36,.72); color:#fff; outline:none; }
    .history-button:disabled { opacity:.42; cursor:not-allowed; color:#6c7480; border-color:#242a33; filter:none; }
    .doodle-actions { display:flex; align-items:center; gap:6px; margin:0 6px 0 2px; }
    .doodle-button { display:grid; place-items:center; width:38px; min-width:38px; height:34px; padding:0; border:1px solid #2b2e35; border-radius:4px; background:#15171c; color:var(--ink); cursor:pointer; }
    .doodle-button:hover, .doodle-button:focus-visible { border-color:rgba(244,191,36,.72); color:#fff; filter:none; outline:none; }
    .doodle-button.active, .doodle-button[aria-pressed="true"] { background:rgba(244,191,36,.16); border-color:rgba(244,191,36,.72); color:var(--accent); box-shadow:0 0 0 1px rgba(244,191,36,.13) inset; }
    .doodle-button svg { width:18px; height:18px; stroke:currentColor; fill:none; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; pointer-events:none; }
    .doodle-button .doodle-dot { width:17px; height:17px; border:2px solid currentColor; border-radius:50%; display:block; }
    .doodle-color-input { width:34px; min-width:34px; height:34px; padding:3px; border:1px solid #2b2e35; border-radius:4px; background:#15171c; cursor:pointer; }
    .doodle-color-input::-webkit-color-swatch-wrapper { padding:0; }
    .doodle-color-input::-webkit-color-swatch { border:0; border-radius:3px; }
    .doodle-color-input::-moz-color-swatch { border:0; border-radius:3px; }
    .doodle-color-input:hover, .doodle-color-input:focus-visible { border-color:rgba(244,191,36,.72); outline:none; }
    .duck { width:38px; height:38px; border-radius:4px; object-fit:contain; image-rendering:auto; background:transparent; border:0; box-shadow:none; transition:transform 120ms ease, box-shadow 120ms ease; }
    main { height:calc(100vh - 68px); min-height:0; display:grid; grid-template-columns:var(--left-panel-width) minmax(620px,1fr) var(--right-panel-width); gap:10px; padding:0 10px 0 0; align-items:stretch; overflow:hidden; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:12px; overflow:hidden; box-shadow:0 0 0 1px rgba(255,255,255,.015) inset; }
    .side-panel { position:relative; min-height:0; max-height:100%; overflow:auto; padding:0 8px 0 0; border-width:0 1px 0 0; border-radius:0; box-shadow:none; background:#101114; }
    .side-panel .panel-section { padding:10px 12px; }
    .side-panel .panel-section:first-child { padding-top:10px; }
    .side-panel .panel-section h2 { font-size:13px; }
    .side-panel > .hint { margin:0; padding:10px 12px 14px; border-top:1px solid var(--line); }
    .right-panel { position:relative; margin-top:8px; max-height:calc(100% - 8px); overflow:auto; }
    .panel-resizer { position:absolute; top:0; bottom:0; width:10px; height:auto; min-height:0; z-index:30; padding:0; border:0; border-radius:0; background:transparent; cursor:col-resize; touch-action:none; }
    .panel-resizer::after { content:""; position:absolute; top:8px; bottom:8px; left:4px; width:2px; border-radius:2px; background:transparent; transition:background 120ms ease, box-shadow 120ms ease; }
    .panel-resizer:hover::after, .panel-resizer:focus-visible::after, .panel-resizer.dragging::after { background:rgba(244,191,36,.66); box-shadow:0 0 0 1px rgba(244,191,36,.18); }
    .panel-resizer:focus-visible { outline:2px solid rgba(244,191,36,.5); outline-offset:-2px; }
    .panel-resizer:hover { filter:none; }
    .left-panel-resizer { right:0; }
    .right-panel-resizer { left:0; }
    .panel-section { padding:10px 0 12px; border-bottom:1px solid var(--line); }
    .panel-section:first-child { padding-top:0; }
    .panel-section:last-child { border-bottom:0; padding-bottom:0; }
    .panel-section h2 { margin:0 0 8px; color:var(--muted); font-size:12px; font-weight:800; letter-spacing:0; }
    .panel-section .hint { margin-bottom:0; }
    .hidden-import-fields { display:none; }
    .compact-import-row { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin:0 0 8px; }
    .import-name-row { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin:0 0 6px; }
    .import-name-cell { min-width:0; height:24px; padding:3px 7px; border:1px solid #252d38; border-radius:4px; background:#0f141b; color:var(--muted); font-size:10px; line-height:16px; font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .import-name-cell.loaded { color:var(--ink); border-color:rgba(244,191,36,.38); background:rgba(244,191,36,.08); }
    .import-chip { height:30px; border:1px solid #2d3542; border-radius:5px; background:#151a22; color:var(--ink); font-size:11px; font-weight:800; display:flex; align-items:center; justify-content:center; gap:5px; cursor:pointer; user-select:none; white-space:nowrap; }
    .import-chip:hover { border-color:rgba(244,191,36,.7); color:#fff; }
    .import-chip.primary { background:rgba(244,191,36,.12); border-color:rgba(244,191,36,.38); }
    .import-chip.loaded { background:rgba(244,191,36,.14); border-color:rgba(244,191,36,.58); color:#fff4c7; box-shadow:0 0 0 1px rgba(244,191,36,.14) inset; }
    label { display:block; margin:9px 0 4px; color:var(--muted); font-size:12px; font-weight:600; }
    input, select { width:100%; height:34px; border:1px solid #2b323d; border-radius:5px; padding:0 8px; font:inherit; color:var(--ink); background:var(--field); }
    input[type=file] { height:auto; padding:8px; color:var(--muted); }
    input[type=color] { width:40px; min-width:40px; height:28px; padding:2px; border-radius:4px; cursor:pointer; }
    input:focus, select:focus { outline:2px solid rgba(244,191,36,.32); border-color:var(--accent); }
    button { height:36px; border:1px solid rgba(244,191,36,.36); border-radius:4px; background:var(--accent); color:#171100; font-size:13px; font-weight:800; cursor:pointer; }
    button.secondary { background:#15171c; color:var(--ink); border:1px solid #2b2e35; }
    .compact-button { width:100%; height:32px; margin-top:8px; }
    button:hover { filter:brightness(1.06); }
    button:disabled { opacity:.55; cursor:progress; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .button-row { display:grid; grid-template-columns:1fr 1fr; gap:7px; margin-top:4px; }
    .action-row { padding:0; }
    .underlay-actions { grid-template-columns:1fr 1fr 1fr; margin-top:7px; }
    .file-action { height:36px; border:1px solid #2b2e35; border-radius:4px; background:#15171c; color:var(--ink); font-size:13px; font-weight:800; cursor:pointer; display:flex; align-items:center; justify-content:center; margin:0; user-select:none; }
    .file-action:hover { filter:brightness(1.06); border-color:rgba(244,191,36,.36); }
    button.active, .file-action.active { background:var(--accent); color:#171100; border-color:rgba(244,191,36,.72); }
    .model-controls { display:grid; gap:7px; margin-top:0; padding:9px; border:1px solid #252d38; border-radius:5px; background:#0f141b; }
    .model-control-row { display:grid; grid-template-columns:1fr 64px; gap:7px; align-items:center; }
    .model-color-row { display:grid; grid-template-columns:1fr 1fr; gap:7px; }
    .model-opacity-row { display:grid; gap:6px; }
    .height-scale-box { display:grid; gap:6px; padding:7px; border:1px solid #252d38; border-radius:5px; background:#111821; }
    .height-scale-title { color:var(--muted); font-size:11px; font-weight:800; }
    .height-scale-mode-row { display:grid; grid-template-columns:1fr 1fr 52px; gap:5px; }
    .height-scale-mode-row button { height:30px; min-height:30px; padding:0 6px; font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .height-scale-mode-row button.active { background:rgba(244,191,36,.18); border-color:rgba(244,191,36,.75); color:var(--accent); }
    .height-scale-note { min-height:14px; color:var(--muted); font-size:10px; line-height:1.35; }
    .model-opacity-control { display:grid; grid-template-columns:minmax(76px,.9fr) minmax(82px,1fr) 38px; align-items:center; gap:7px; margin:0; color:var(--muted); font-size:11px; font-weight:800; }
    .model-opacity-control input[type=range] { height:18px; min-width:0; padding:0; accent-color:var(--accent); }
    .model-opacity-value { color:var(--accent); font-size:11px; font-weight:900; text-align:right; font-variant-numeric:tabular-nums; }
    .background-color-row { grid-template-columns:1fr; }
    .model-color { display:flex; align-items:center; justify-content:space-between; gap:6px; margin:0; color:var(--muted); font-size:11px; }
    .model-toggle { display:inline-flex; align-items:center; gap:7px; margin:0; color:var(--ink); font-size:12px; cursor:pointer; user-select:none; }
    .model-toggle input { width:16px; height:16px; margin:0; padding:0; accent-color:var(--accent); }
    .delete-button { height:28px; font-size:12px; background:#151a22; color:#ffb4a8; border-color:#52302d; }
    .advanced-line-settings { display:none; }
    #viewerWrap { position:relative; width:100%; height:100%; min-height:0; border:1px solid #d8dde5; border-radius:6px; background:var(--viewer); overflow:hidden; }
    #webglViewer, #viewer { position:absolute; inset:0; width:100%; height:100%; display:block; transform-origin:0 0; }
    #webglViewer { background:var(--viewer); }
        #viewer { cursor:grab; }
    #viewer:active { cursor:grabbing; }
    .view-state-input { display:none !important; }
    .view-quick-controls { position:absolute; left:12px; bottom:12px; z-index:24; display:flex; align-items:center; gap:6px; padding:5px; border:1px solid rgba(37,43,52,.18); border-radius:6px; background:rgba(16,20,25,.78); box-shadow:0 6px 18px rgba(0,0,0,.12); backdrop-filter:blur(4px); }
    .view-icon-button { display:grid; place-items:center; width:32px; min-width:32px; height:32px; padding:0; border:1px solid rgba(215,222,232,.36); border-radius:5px; background:rgba(255,255,255,.92); color:#647282; box-shadow:none; cursor:pointer; }
    .view-icon-button:hover, .view-icon-button:focus-visible { border-color:rgba(244,191,36,.78); color:#1f2630; filter:none; outline:none; }
    .view-icon-button.active, .view-icon-button[aria-pressed="true"] { border-color:rgba(244,191,36,.88); background:rgba(242,192,55,.96); color:#171100; }
    .view-icon-button svg { width:19px; height:19px; stroke:currentColor; fill:none; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; pointer-events:none; }
    .hint { color:var(--muted); font-size:11px; line-height:1.45; margin-top:7px; }
    .help-details { margin-top:12px; border-top:1px solid var(--line); padding-top:9px; }
    .help-details summary { cursor:pointer; color:var(--muted); font-size:11px; font-weight:900; list-style:none; user-select:none; }
    .help-details summary::-webkit-details-marker { display:none; }
    .help-details summary::before { content:'?'; display:inline-grid; place-items:center; width:16px; height:16px; margin-right:6px; border:1px solid #354050; border-radius:50%; color:var(--accent); font-size:10px; }
    .help-details[open] summary { color:var(--ink); }
    .help-details .hint { margin:8px 0 0; }
    .viewer-panel { display:flex; min-height:0; padding:8px 0 0; background:transparent; border:0; box-shadow:none; overflow:visible; }
        #isoGuidePanel { flex:1 1 520px; max-width:640px; min-height:46px; border:1px solid #252b34; border-radius:5px; padding:7px 12px; background:#151a21; display:flex; align-items:center; gap:12px; box-shadow:0 0 0 1px rgba(0,0,0,.12); }
    .iso-bubble-duck { flex:0 0 34px; width:34px; height:34px; border:1px solid #252b34; border-radius:5px; background:#101419; display:grid; place-items:center; }
    .iso-bubble-duck img { width:28px; height:28px; object-fit:contain; image-rendering:auto; }
    .iso-copy { min-width:0; display:grid; gap:2px; }
    .iso-title { display:none; }
    .iso-body { color:var(--ink); font-size:12px; line-height:1.35; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .iso-note { color:var(--muted); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .status { min-height:20px; margin-top:8px; color:var(--muted); font-size:12px; line-height:1.45; }
    .status:empty { min-height:0; margin-top:0; }
    .line-target-tabs { display:none; grid-template-columns:1fr 1fr; gap:6px; margin:0 0 8px; }
    .line-target-tabs button { height:30px; border:1px solid #2d3542; border-radius:5px; background:#151a22; color:var(--ink); }
    .line-target-tabs button.active { background:var(--accent); color:#171100; border-color:var(--accent); }
    .line-target-tabs button:disabled { opacity:.45; cursor:not-allowed; }
    .line-list { display:grid; gap:6px; max-height:420px; overflow:auto; padding-right:4px; }
    .section-grid-header, .section-row { display:grid; grid-template-columns:minmax(86px,1.05fr) minmax(82px,.9fr) minmax(82px,.9fr); gap:6px; align-items:stretch; }
    .section-grid-header { color:var(--muted); font-size:11px; font-weight:800; padding:0 4px 1px; }
    .section-grid-header > span { display:flex; align-items:center; min-width:0; }
    .section-grid-header > span:not(:first-child) { justify-content:center; }
    .section-column-toggle { width:100%; height:24px; min-height:24px; padding:0 6px; border:1px solid transparent; border-radius:4px; background:transparent; color:var(--muted); display:flex; align-items:center; justify-content:center; gap:5px; font-size:11px; font-weight:800; cursor:pointer; }
    .section-column-toggle:hover, .section-column-toggle:focus-visible { border-color:#2d3542; color:var(--ink); outline:none; filter:none; }
    .section-column-toggle:disabled { opacity:.34; cursor:not-allowed; }
    .section-column-toggle .column-toggle-mark { width:13px; height:13px; border:1px solid #55606e; border-radius:3px; display:grid; place-items:center; color:#171100; background:#121820; font-size:10px; line-height:1; }
    .section-column-toggle.all-visible .column-toggle-mark { background:var(--accent); border-color:var(--accent); }
    .section-column-toggle.partial-visible .column-toggle-mark { background:#2b323d; border-color:#697586; color:var(--accent); }
    .section-column-toggle .column-toggle-text { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .section-row { border:1px solid #252d38; border-radius:6px; padding:6px; background:var(--panel-2); }
    .section-row.active, .section-row.active-row { outline:1px solid rgba(244,191,36,.45); border-color:rgba(244,191,36,.45); }
    .section-name-cell { display:flex; align-items:center; gap:8px; min-width:0; }
    input[type=color].line-color-input { width:18px; min-width:18px; max-width:18px; height:18px; min-height:18px; padding:0; border:1px solid rgba(255,255,255,.22); border-radius:50%; background:transparent; cursor:pointer; overflow:hidden; appearance:none; -webkit-appearance:none; box-shadow:0 0 0 1px rgba(0,0,0,.18) inset; flex:0 0 18px; }
    input[type=color].line-color-input::-webkit-color-swatch-wrapper { padding:0; }
    input[type=color].line-color-input::-webkit-color-swatch { border:0; border-radius:50%; }
    input[type=color].line-color-input::-moz-color-swatch { border:0; border-radius:50%; }
    input[type=color].line-color-input:hover, input[type=color].line-color-input:focus-visible { border-color:rgba(244,191,36,.75); outline:2px solid rgba(244,191,36,.24); outline-offset:2px; }
    .line-name-input { width:100%; height:26px; padding:0 6px; border:1px solid transparent; background:transparent; color:var(--ink); font-weight:800; font-size:13px; }
    .line-name-input:focus { background:#0d1116; border-color:var(--accent); outline:2px solid rgba(244,191,36,.22); }
    .section-model-cell { min-width:0; min-height:34px; border:1px solid #29313c; border-radius:5px; padding:6px 7px; display:grid; grid-template-columns:18px minmax(0,1fr); gap:6px; align-items:center; cursor:pointer; }
    .section-model-cell.active { border-color:rgba(244,191,36,.72); box-shadow:0 0 0 1px rgba(244,191,36,.28); }
    .section-model-cell.unavailable { opacity:.34; cursor:not-allowed; }
    .section-model-cell.hidden-plane { background:#11161d; border-color:#252b34; }
    .section-model-cell.hidden-plane .measure-value { opacity:.46; }
    .section-model-toggle { display:grid; place-items:center; width:18px; height:18px; margin:0; min-width:0; cursor:pointer; user-select:none; }
    .section-model-toggle input { width:16px; height:16px; min-width:16px; margin:0; padding:0; accent-color:var(--accent); }
    .measure-value { color:var(--muted); font-size:12px; font-weight:700; font-variant-numeric:tabular-nums; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
    .line-tools { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:5px; }
    .control-group { margin-top:10px; }
    .control-group-title { margin:0 0 6px; color:var(--muted); font-size:10px; line-height:1; font-weight:900; letter-spacing:0; }
    .left-panel .panel-section h2, .left-panel .control-group-title { display:none; }
    .left-panel .panel-section { padding-top:8px; padding-bottom:10px; }
    .left-panel .button-row { margin-top:0; }
    .left-panel .control-group { margin-top:8px; }
    .left-panel .control-group:first-child { margin-top:0; }
    .option-toggle-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:6px; }
    .option-toggle-grid.single { grid-template-columns:1fr; }
    .option-toggle { display:inline-flex; align-items:center; gap:6px; margin:10px 0 0; color:var(--ink); font-size:12px; cursor:pointer; user-select:none; }
    .option-toggle input { width:16px; height:16px; margin:0; padding:0; accent-color:var(--accent); }
    .chip-toggle { min-height:30px; margin:0; padding:6px 8px; border:1px solid #252d38; border-radius:5px; background:#11161d; font-size:11px; font-weight:800; line-height:1.2; overflow:hidden; }
    .chip-toggle:hover { border-color:rgba(244,191,36,.42); background:#141a22; }
    .chip-toggle input { flex:0 0 auto; width:15px; height:15px; }
    .chip-toggle span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .compact-button.mask-clear { height:30px; margin-top:6px; color:#ffb4a8; border-color:#3a2525; background:#151315; }
    .compact-button.mask-clear:hover { border-color:#6a3a31; background:#1b1515; }
    .option-select { margin-top:12px; }
    .option-select label { margin:0 0 5px; }
    .mesh-count { font-weight:800; color:var(--ink); margin-bottom:8px; font-size:12px; line-height:1.35; overflow-wrap:anywhere; }
    .model-diagnostics { display:grid; gap:6px; margin:8px 0 10px; padding:8px; border:1px solid #252d38; border-radius:6px; background:#0f141a; }
    .diagnostic-panel-title { display:flex; align-items:center; justify-content:space-between; gap:8px; color:var(--accent); font-size:11px; font-weight:900; line-height:1.2; }
    .diagnostic-empty { color:var(--muted); font-size:10px; line-height:1.4; }
    .diagnostic-card { border:1px solid #252d38; border-radius:5px; padding:7px 8px; background:#10161d; }
    .diagnostic-title { display:flex; justify-content:space-between; gap:8px; color:var(--ink); font-size:11px; font-weight:900; line-height:1.25; }
    .diagnostic-title .diagnostic-state { color:#7bd88f; }
    .diagnostic-card.warn .diagnostic-title .diagnostic-state { color:#ffcb6b; }
    .diagnostic-card.danger .diagnostic-title .diagnostic-state { color:#ff8f7c; }
    .diagnostic-list { margin:5px 0 0; padding:0; list-style:none; color:var(--muted); font-size:10px; line-height:1.35; }
    .diagnostic-list li + li { margin-top:2px; }
    .section-model-cell.warning { border-color:rgba(255,203,107,.58); background:rgba(255,203,107,.06); }
    .section-warning-mark { flex:0 0 auto; display:inline-grid; place-items:center; width:15px; height:15px; margin-right:4px; border-radius:50%; background:rgba(255,203,107,.16); color:#ffcb6b; font-size:10px; font-weight:900; }
    .measure-value.warning { color:#ffcb6b; }
    .visibility-toggle { display:inline-flex; align-items:center; gap:5px; margin:0; color:var(--muted); font-size:11px; cursor:pointer; user-select:none; }
    .visibility-toggle input { width:16px; height:16px; padding:0; margin:0; accent-color:var(--accent); }
    input[type=range] { height:22px; padding:0; accent-color:var(--accent); }
    .preview-img { width:100%; max-height:360px; object-fit:contain; border:1px solid var(--line); border-radius:6px; background:#fff; }
    table { width:100%; border-collapse:collapse; font-size:12px; margin-top:10px; }
    th,td { text-align:left; border-bottom:1px solid #232a34; padding:6px 4px; white-space:nowrap; }
    th { color:var(--muted); font-weight:700; }
    a { color:var(--blue); text-decoration:none; margin-right:12px; }
    ::-webkit-scrollbar { width:12px; height:12px; }
    ::-webkit-scrollbar-track { background:#0d1116; }
    ::-webkit-scrollbar-thumb { background:#303846; border:3px solid #0d1116; border-radius:10px; }
    .path-field { display:none !important; }
    @media (max-width:1200px) { body { overflow:auto; } header { height:auto; flex-wrap:wrap; } #isoGuidePanel { flex-basis:100%; max-width:none; } main { height:auto; min-height:calc(100vh - 88px); grid-template-columns:1fr; padding:8px 10px 18px; overflow:visible; } .side-panel { border:1px solid var(--line); border-radius:6px; max-height:none; padding-right:0; } .right-panel { margin-top:0; max-height:none; } .panel-resizer { display:none; } #viewerWrap { height:560px; min-height:360px; } }
  </style>
</head>
<body>
<header>
  <div class="brand">
    <button class="brand-sound-button" id="brandSoundButton" type="button" aria-label="全リセット">
      <img class="duck" src="/asset/IconAhiru.png" alt="" aria-hidden="true">
    </button>
    <div class="brand-text">
      <h1>Quack Contour</h1>
      <div class="subtitle" id="appSubtitle">OBJ body section tool</div>
    </div>
  </div>
  <div id="isoGuidePanel" role="status" aria-live="polite">
    <div class="iso-bubble-duck" aria-hidden="true"><img src="/asset/IconAhiru.png" alt=""></div>
    <div class="iso-copy">
      <div class="iso-title" id="isoGuideTitle" aria-hidden="true"></div>
      <div class="iso-body" id="isoGuideText">先に主モデルを読み込んでください。</div>
      <div class="iso-note" id="isoGuideNote">次の操作をここに表示します。</div>
    </div>
  </div>
  <div class="header-actions">
        <div class="history-actions" aria-label="Undo and redo">
      <button type="button" id="undoButton" class="history-button">Undo</button>
      <button type="button" id="redoButton" class="history-button">Redo</button>
    </div>
    <div class="doodle-actions" aria-label="Doodle tools">
      <button type="button" id="doodlePenButton" class="doodle-button" aria-pressed="false" title="落書きペン">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20z"/><path d="M13.5 7.5l3 3"/></svg>
      </button>
      <button type="button" id="doodleCircleButton" class="doodle-button" aria-pressed="false" title="円を描く"><span class="doodle-dot" aria-hidden="true"></span></button>
      <button type="button" id="doodleClearButton" class="doodle-button" aria-pressed="false" title="落書きを消す">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg>
      </button>
      <input type="color" id="doodleColorInput" class="doodle-color-input" value="#2f80ed" title="落書き色" aria-label="落書き色">
    </div>
    <label class="header-tool-button" for="underlayImageFile" data-i18n="underlayImage">画像</label>
    <button type="button" id="moveUnderlayButton" class="header-tool-button" data-i18n="moveUnderlay">下絵を動かす</button>
    <button type="button" id="deleteUnderlayButton" class="header-tool-button" data-i18n="deleteUnderlay">下絵削除</button>
    <button type="button" id="languageToggleButton" class="language-toggle">English</button>
  </div>
</header>
<main>
  <section class="panel side-panel left-panel">
    <button type="button" class="panel-resizer left-panel-resizer" id="leftPanelResizer" aria-label="Resize left panel" title="Drag to resize / double-click to reset"></button>
    <form id="toolForm">
      <div class="hidden-import-fields" aria-hidden="true">
        <input name="mesh_file" id="meshFile" type="file" accept=".obj" tabindex="-1">
        <input class="path-field" name="mesh_path" id="meshPath" value="" tabindex="-1">
        <input id="compareMeshFile" type="file" accept=".obj" tabindex="-1">
        <input class="path-field" id="compareMeshPath" value="" tabindex="-1">
        <input id="underlayImageFile" type="file" accept="image/*" tabindex="-1">
      </div>
      <div class="panel-section action-section">
        <h2 data-i18n="generate">生成</h2>
        <div class="button-row action-row">
          <button type="submit" id="runButton" data-i18n="makeTopView">上面図生成</button>
          <button type="button" id="compareReportButton" class="secondary" data-i18n="makeCompare">比較図生成</button>
          <button type="button" id="exportSilhouetteButton" class="secondary" data-i18n="exportSilhouette">シルエットPNG</button>
        </div>
        <div class="status" id="status"></div>
      </div>
      <div class="panel-section">
        <h2 data-i18n="displayModels">表示モデル</h2>
        <div class="model-controls">
          <div class="model-control-row">
            <label class="model-toggle"><input type="checkbox" id="showPrimaryModel" checked><span data-i18n="showPrimary">主モデルを表示</span></label>
            <button type="button" id="deletePrimaryModel" class="delete-button" data-i18n="delete">削除</button>
          </div>
          <div class="model-control-row">
            <label class="model-toggle"><input type="checkbox" id="showCompareModel" checked><span data-i18n="showCompare">比較モデルを表示</span></label>
            <button type="button" id="deleteCompareModel" class="delete-button" data-i18n="delete">削除</button>
          </div>
          <div class="model-color-row">
            <label class="model-color"><span data-i18n="primaryColor">主モデル色</span><input type="color" id="primarySilhouetteColor" value="#1f1f1f"></label>
            <label class="model-color"><span data-i18n="compareColor">比較色</span><input type="color" id="compareSilhouetteColor" value="#8c939b"></label>
          </div>
          <div class="model-opacity-row">
            <label class="model-opacity-control"><span data-i18n="primaryOpacity">&#20027;&#12514;&#12487;&#12523;&#28611;&#12373;</span><input type="range" id="primaryModelOpacity" min="10" max="100" step="1" value="100"><strong id="primaryModelOpacityValue" class="model-opacity-value">100%</strong></label>
            <label class="model-opacity-control"><span data-i18n="compareOpacity">&#27604;&#36611;&#28611;&#12373;</span><input type="range" id="compareModelOpacity" min="10" max="100" step="1" value="100"><strong id="compareModelOpacityValue" class="model-opacity-value">100%</strong></label>
          </div>
          <div class="height-scale-box">
            <div class="height-scale-title" data-i18n="heightScaleTitle">&#34920;&#31034;&#36523;&#38263;&#21512;&#12431;&#12379;</div>
            <div class="height-scale-mode-row">
              <button type="button" id="heightScaleMatchPrimary" class="secondary" data-i18n="heightScaleMatchPrimary">&#20027;&#12395;&#21512;&#12431;&#12379;&#12427;</button>
              <button type="button" id="heightScaleMatchCompare" class="secondary" data-i18n="heightScaleMatchCompare">&#27604;&#36611;&#12395;&#21512;&#12431;&#12379;&#12427;</button>
              <button type="button" id="heightScaleClear" class="secondary" data-i18n="heightScaleClear">&#35299;&#38500;</button>
            </div>
            <div class="height-scale-note" id="heightScaleNote"></div>
          </div>
          <div class="model-color-row background-color-row">
            <label class="model-color"><span data-i18n="viewerBgColor">背景色</span><input type="color" id="viewerBgColor" value="#ffffff"></label>
          </div>
          <div class="option-select" style="margin-top:4px;">
            <label data-i18n="compareLayout">比較配置</label>
            <select id="compareLayout">
              <option value="separate" selected data-i18n="sideBySide">並べる</option>
              <option value="overlay" data-i18n="overlay">重ねる</option>
            </select>
          </div>
        </div>
      </div>
      <div class="advanced-line-settings" aria-hidden="true">
        <label>ラインCSV/JSON</label>
        <input name="lines_file" id="linesFile" type="file" accept=".csv,.json" tabindex="-1">
        <label>ライン定義パス</label>
        <input name="lines_path" id="linesPath" value="templates/hana_DF_lines.csv" tabindex="-1">
        <div class="row">
          <div><label>表示身長 cm</label><input name="display_height_cm" value="155" tabindex="-1"></div>
          <div><label>出力名</label><input name="output_stem" value="interactive_sections" tabindex="-1"></div>
        </div>
        <label>タイトル</label>
        <input name="title" value="上面断面図" tabindex="-1">
      </div>
      <div class="panel-section">
        <h2 data-i18n="viewControls">ビュー操作</h2>
        <div class="control-group">
          <div class="control-group-title" data-i18n="maskTools">マスク</div>
          <div class="option-toggle-grid">
            <label class="option-toggle chip-toggle"><input type="checkbox" id="maskDrawMode"><span data-i18n="makeMask">マスク作成</span></label>
            <label class="option-toggle chip-toggle"><input type="checkbox" id="showMaskRects" checked><span data-i18n="showMaskRects">&#12510;&#12473;&#12463;&#26528;&#12434;&#34920;&#31034;</span></label>
          </div>
          <div class="option-select mask-shape-select">
            <label data-i18n="maskShape">&#12510;&#12473;&#12463;&#24418;&#29366;</label>
            <select id="maskShapeMode">
              <option value="rect" selected data-i18n="maskRect">&#30697;&#24418;</option>
              <option value="lasso" data-i18n="maskLasso">&#25237;&#12370;&#12394;&#12431;</option>
            </select>
          </div>
          <button type="button" id="clearMasksButton" class="secondary compact-button mask-clear" data-i18n="clearMasks">マスク削除</button>
        </div>
        <div class="control-group">
          <div class="control-group-title" data-i18n="displayAids">表示補助</div>
          <div class="option-toggle-grid single">
            <label class="option-toggle chip-toggle"><input type="checkbox" id="lineGuideOnly" checked><span data-i18n="lineOnly">&#26029;&#38754;&#12460;&#12452;&#12489;&#12434;&#32218;&#12384;&#12369;&#34920;&#31034;</span></label>
          </div>
        </div>
        <div class="option-select">
          <label data-i18n="renderMode">&#34920;&#31034;&#12514;&#12540;&#12489;</label>
          <select id="renderMode">
            <option value="silhouette" selected data-i18n="silhouetteMode">&#12471;&#12523;&#12456;&#12483;&#12488;&#34920;&#31034;</option>
            <option value="outline" data-i18n="outlineMode">&#22806;&#21608;&#32218;&#34920;&#31034;</option>
          </select>
        </div>
        <div class="option-select">
          <label><span data-i18n="yaw">水平回転</span> <span id="viewYawValue">90°</span></label>
          <input id="viewYawSlider" type="range" min="-180" max="180" step="1" value="90">
        </div>
        <div class="control-group">
          <div class="control-group-title" data-i18n="rotationAssist">回転補助</div>
          <div class="option-toggle-grid">
            <label class="option-toggle chip-toggle"><input type="checkbox" id="invertPitch"><span data-i18n="invertPitch">&#19978;&#19979;&#22238;&#36578;&#12434;&#21453;&#36578;</span></label>
            <label class="option-toggle chip-toggle"><input type="checkbox" id="invertYaw"><span data-i18n="invertYaw">&#24038;&#21491;&#22238;&#36578;&#12434;&#21453;&#36578;</span></label>
          </div>
        </div>
      </div>
    </form>
    <details class="help-details">
      <summary data-i18n="helpSummary">操作メモ</summary>
      <p class="hint" data-i18n="helpText">左ドラッグで回転。水平回転スライダーで前後左右を確認できます。「マスク作成」ON時は左ドラッグで矩形マスクを作成。断面位置は左右の縦バー上の点をドラッグして上下移動。中ボタン・右ドラッグ・Shift+左ドラッグでパン、ホイールで拡大縮小。「表示を合わせる」で現在の向きのまま全体を収めます。ビューキー: 2=正面、8=背面、4/6=左右、5=上面、P=正投影/透視切替。</p>
    </details>
  </section>
  <section class="panel viewer-panel">
        <div id="viewerWrap">
      <canvas id="webglViewer" width="940" height="690"></canvas>
      <canvas id="viewer" width="940" height="690"></canvas>
      <input class="view-state-input" type="checkbox" id="showFloorGrid" checked>
      <input class="view-state-input" type="checkbox" id="showCenterLines" checked>
      <div class="view-quick-controls" aria-label="View display controls">
        <button type="button" id="floorGridQuickButton" class="view-icon-button" aria-pressed="true" title="Grid">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16M6 4v16M12 4v16M18 4v16"/></svg>
        </button>
        <button type="button" id="centerLinesQuickButton" class="view-icon-button" aria-pressed="true" title="Center lines">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18"/><circle cx="12" cy="12" r="3.5"/></svg>
        </button>
        <button type="button" id="projectionQuickButton" class="view-icon-button" aria-pressed="false" title="Projection">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h10l4 4v7H9l-4-4z"/><path d="M15 7v7H5M15 14l4 4M9 14v4"/></svg>
        </button>
        <button type="button" id="fitViewQuickButton" class="view-icon-button" aria-pressed="false" title="Fit view">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H4v4M16 3h4v4M20 17v4h-4M4 17v4h4"/><path d="M9 12h6M12 9v6"/></svg>
        </button>
      </div>
    </div>
  </section>
  <section class="panel right-panel">
    <button type="button" class="panel-resizer right-panel-resizer" id="rightPanelResizer" aria-label="Resize right panel" title="Drag to resize / double-click to reset"></button>
    <div class="import-name-row" aria-label="Imported OBJ file names">
      <div id="primaryFileName" class="import-name-cell" title="">主: --</div>
      <div id="compareFileName" class="import-name-cell" title="">比較: --</div>
    </div>
    <div class="compact-import-row" aria-label="OBJインポート">
      <label class="import-chip primary" for="meshFile" data-i18n="importPrimary">主 OBJインポート</label>
      <label class="import-chip" for="compareMeshFile" data-i18n="importCompare">比較 OBJインポート</label>
    </div>
    <div id="meshMeta" class="hint"></div>

    <div id="modelDiagnostics" class="model-diagnostics" aria-live="polite"></div>
    <div class="line-target-tabs" id="lineTargetTabs">
      <button type="button" data-target="primary" class="active" data-i18n="primaryModel">&#20027;&#12514;&#12487;&#12523;</button>
      <button type="button" data-target="compare" disabled data-i18n="compareModel">&#27604;&#36611;&#12514;&#12487;&#12523;</button>
    </div>
    <div class="line-list" id="lineList"></div>
    <div class="status" id="resultStatus"></div>
    <div id="outputLinks"></div>
    <img id="outputImage" class="preview-img" style="display:none" alt="">
    <table id="resultTable"></table>
  </section>
</main>
<script src="/asset/three.min.js"></script>
<script>
const form = document.getElementById('toolForm');
const statusEl = document.getElementById('status');
const resultStatus = document.getElementById('resultStatus');
window.addEventListener('error', e => { statusEl.textContent = textFor('screenError') + (e.message || e.error?.message || e); });
window.addEventListener('unhandledrejection', e => { statusEl.textContent = textFor('processError') + (e.reason?.message || e.reason || e); });
const glCanvas = document.getElementById('webglViewer');
const canvas = document.getElementById('viewer');
const viewerWrap = document.getElementById('viewerWrap');
const floorGridQuickButton = document.getElementById('floorGridQuickButton');
const centerLinesQuickButton = document.getElementById('centerLinesQuickButton');
const projectionQuickButton = document.getElementById('projectionQuickButton');
const fitViewQuickButton = document.getElementById('fitViewQuickButton');
const leftPanelResizer = document.getElementById('leftPanelResizer');
const rightPanelResizer = document.getElementById('rightPanelResizer');
const ctx = canvas.getContext('2d');
const lineList = document.getElementById('lineList');
const meshMeta = document.getElementById('meshMeta');
const modelDiagnostics = document.getElementById('modelDiagnostics');
const primaryFileNameEl = document.getElementById('primaryFileName');
const compareFileNameEl = document.getElementById('compareFileName');
const isoGuideText = document.getElementById('isoGuideText');
const isoGuideTitle = document.getElementById('isoGuideTitle');
const isoGuideNote = document.getElementById('isoGuideNote');
const languageToggleButton = document.getElementById('languageToggleButton');
const doodlePenButton = document.getElementById('doodlePenButton');
const doodleCircleButton = document.getElementById('doodleCircleButton');
const doodleClearButton = document.getElementById('doodleClearButton');
const doodleColorInput = document.getElementById('doodleColorInput');
const appSubtitle = document.getElementById('appSubtitle');
const lineTargetButtons = [...document.querySelectorAll('#lineTargetTabs button')];
const outputImage = document.getElementById('outputImage');
const outputLinks = document.getElementById('outputLinks');
const resultTable = document.getElementById('resultTable');
const hideArmsInput = document.getElementById('hideArms') || { checked:false, addEventListener(){} };
const maskDrawModeInput = document.getElementById('maskDrawMode');
const maskShapeModeInput = document.getElementById('maskShapeMode');
const clearMasksButton = document.getElementById('clearMasksButton');
const showMaskRectsInput = document.getElementById('showMaskRects');
const lineGuideOnlyInput = document.getElementById('lineGuideOnly');
const showFloorGridInput = document.getElementById('showFloorGrid');
const showCenterLinesInput = document.getElementById('showCenterLines');
const pickSectionHeightInput = document.getElementById('pickSectionHeight') || { checked:false, addEventListener(){} };
const renderModeInput = document.getElementById('renderMode');
const viewYawSlider = document.getElementById('viewYawSlider');
const viewYawValue = document.getElementById('viewYawValue');
const invertPitchInput = document.getElementById('invertPitch');
const invertYawInput = document.getElementById('invertYaw');
const showPrimaryModelInput = document.getElementById('showPrimaryModel');
const showCompareModelInput = document.getElementById('showCompareModel');
const deletePrimaryModelButton = document.getElementById('deletePrimaryModel');
const deleteCompareModelButton = document.getElementById('deleteCompareModel');
const compareLayoutInput = document.getElementById('compareLayout');
const exportSilhouetteButton = document.getElementById('exportSilhouetteButton');
const underlayImageFileInput = document.getElementById('underlayImageFile');
const moveUnderlayButton = document.getElementById('moveUnderlayButton');
const deleteUnderlayButton = document.getElementById('deleteUnderlayButton');
const undoButton = document.getElementById('undoButton');
const redoButton = document.getElementById('redoButton');
const primarySilhouetteColorInput = document.getElementById('primarySilhouetteColor');
const compareSilhouetteColorInput = document.getElementById('compareSilhouetteColor');
const primaryModelOpacityInput = document.getElementById('primaryModelOpacity');
const compareModelOpacityInput = document.getElementById('compareModelOpacity');
const primaryModelOpacityValue = document.getElementById('primaryModelOpacityValue');
const compareModelOpacityValue = document.getElementById('compareModelOpacityValue');
const heightScaleMatchPrimaryButton = document.getElementById('heightScaleMatchPrimary');
const heightScaleMatchCompareButton = document.getElementById('heightScaleMatchCompare');
const heightScaleClearButton = document.getElementById('heightScaleClear');
const heightScaleNote = document.getElementById('heightScaleNote');
const viewerBgColorInput = document.getElementById('viewerBgColor');
const brandSoundButton = document.getElementById('brandSoundButton');
const duckQuackAudio = typeof Audio !== 'undefined' ? new Audio('/asset/duck-quacking-37392.mp3') : null;
if (duckQuackAudio) {
  duckQuackAudio.preload = 'auto';
  duckQuackAudio.volume = 0.55;
}

const UI_TEXT = {
  ja: {
    toggle:'English', subtitle:'OBJ体型断面ツール', generate:'生成', displayModels:'表示モデル', viewControls:'ビュー操作',
    fitView:'表示を合わせる', makeTopView:'上面図生成', makeCompare:'比較図生成', exportSilhouette:'シルエットPNG',
    underlayImage:'画像', moveUnderlay:'下絵を動かす', deleteUnderlay:'下絵削除', underlayLoaded:'下絵を読み込みました。', underlayDeleted:'下絵を削除しました。', underlayMoveHint:'下絵移動中: 左ドラッグで移動、黄色い角ハンドル/ホイールで拡大縮小、上の丸ハンドルで回転。', underlayNoImage:'先に下絵画像を読み込んでください。', importPrimary:'主 OBJインポート', importCompare:'比較 OBJインポート',
    showPrimary:'主モデルを表示', showCompare:'比較モデルを表示', delete:'削除', primaryColor:'主モデル色', compareColor:'比較色', primaryOpacity:'主モデル濃さ', compareOpacity:'比較濃さ', viewerBgColor:'背景色', compareLayout:'比較配置', sideBySide:'並べる', overlay:'重ねる', makeMask:'マスク作成', maskShape:'\u30de\u30b9\u30af\u5f62\u72b6', maskRect:'\u77e9\u5f62', maskLasso:'\u6295\u3052\u306a\u308f', clearMasks:'マスク削除', showMaskRects:'マスク枠を表示', lineOnly:'断面ガイドを線だけ表示', hideAllGuides:'断面ガイドを一括非表示', showAllGuides:'断面ガイドを一括表示', floorGrid:'床グリッドを表示', centerLines:'中心線を表示', maskTools:'マスク', displayAids:'表示補助', rotationAssist:'回転補助', helpSummary:'操作メモ', renderMode:'表示モード', silhouetteMode:'シルエット表示', outlineMode:'外周線表示', yaw:'水平回転', invertPitch:'上下回転を反転', invertYaw:'左右回転を反転',
    primaryModel:'主モデル', compareModel:'比較モデル', show:'表示', lineName:'断面名', perimeterLabel:'周囲計', moveHandle:'動かす', lineEmpty:'モデルを読み込むと断面位置を編集できます。', noModel:'モデルを読み込んでください',
    isoDefaultTitle:'', isoDefaultText:'先に主モデルを読み込んでください。', isoNote:'OBJを読み込むと、次の操作をここに表示します。', selectedPrefix:'編集中: ', genericLine:'断面ライン', genericGuide:'断面位置を調整し、必要なら比較モデルや下絵を追加できます。',
    meshVertices:'頂点', meshFaces:'総三角面', meshShown:'プレビュー表示', meshDecimated:'間引き', meshSilhouette:'シルエット', meshFormat:'形式', meshHeight:'身長', screenError:'画面エラー: ', processError:'処理エラー: ', modelLoading:'モデル読み込み中', modelLinesLoading:'モデル読み込み完了。ライン読み込み中', loadComplete:'読み込み完了', loadCompleteCompare:'読み込み完了。比較表示中', modelLoadedLineError:'モデルは読み込み完了。ライン定義: ', mainFirst:'先に主モデルを読み込んでください', compareLoading:'比較モデル読み込み中', compareLoaded:'比較モデル読み込み完了。比較モデルの断面位置を編集中です。', deleteCompareStatus:'比較モデルを削除しました。', deletePrimaryStatus:'主モデルを削除しました。', duckMuted:'今は音を鳴らせませんでした。', masksCleared:'マスクを削除しました。',
    maskCreated:'選択メッシュ面をマスクしました。', needBothModels:'主モデルと比較モデルを読み込んでください', helpText:'左ドラッグで回転。水平回転スライダーで前後左右を確認できます。「マスク作成」ON時は左ドラッグで矩形または投げなわマスクを作成。断面位置は左右の縦バー上の点をドラッグして上下移動。中ボタン・右ドラッグ・Shift+左ドラッグでパン、ホイールで拡大縮小。「表示を合わせる」で現在の向きのまま全体を収めます。ビューキー: 2=正面、8=背面、4/6=左右、5=上面、P=正投影/透視切替。', exportNoModel:'モデルを読み込んでから書き出してください。', pngExportFailed:'PNG書き出しに失敗しました。', silhouettePngLink:'PNG', silhouetteExported:'シルエットPNGを作成しました。', compareGenerating:'比較図生成中', compareFailed:'比較図生成に失敗しました。', compareComplete:'比較図生成完了', tableLine:'ライン', tablePrimary:'主 cm', tableCompare:'比較 cm', tableDiff:'差 cm', generating:'生成中', generateFailed:'生成に失敗しました。', done:'完了', tablePerimeter:'外周 cm', tableHeight:'高さ cm', renderError:'3D表示エラー: ', meshNotFound:'メッシュ形状が見つかりません。', objOnlyError:'現在はOBJのみ対応しています。OBJファイルを選択してください。', fbxBinaryUnsupported:'Binary FBXは未対応です。CLO/MarvelousからASCII FBXまたはOBJで書き出してください。', fbxArraysNotFound:'ASCII FBXのVertices / PolygonVertexIndex配列が見つかりません。', fbxVerticesInvalid:'FBX Vertices配列の数が3で割り切れません。', fileRequired:'OBJパスまたはOBJファイルを指定してください。', resetStatus:'リセットしました。OBJを読み込んでください。', initialStatus:'モデルは未読み込みです。OBJをインポートしてください。'
  },
  en: {
    toggle:'日本語', subtitle:'OBJ body section tool', generate:'Output', displayModels:'Models', viewControls:'View controls', fitView:'Fit view', makeTopView:'Top view', makeCompare:'Compare report', exportSilhouette:'Silhouette PNG',
    underlayImage:'Image', moveUnderlay:'Move underlay', deleteUnderlay:'Delete underlay', underlayLoaded:'Underlay image loaded.', underlayDeleted:'Underlay image deleted.', underlayMoveHint:'Underlay move mode: left drag to move; drag yellow corners or wheel to scale; drag the top round handle to rotate.', underlayNoImage:'Import an underlay image first.', importPrimary:'Primary OBJ', importCompare:'Compare OBJ',
    showPrimary:'Show primary', showCompare:'Show compare', delete:'Delete', primaryColor:'Primary color', compareColor:'Compare color', primaryOpacity:'Primary opacity', compareOpacity:'Compare opacity', viewerBgColor:'Background color', compareLayout:'Compare layout', sideBySide:'Side by side', overlay:'Overlay', makeMask:'Draw mask', maskShape:'Mask shape', maskRect:'Rectangle', maskLasso:'Lasso', clearMasks:'Clear masks', showMaskRects:'Show mask boxes', lineOnly:'Guide lines only', hideAllGuides:'Hide all section guides', showAllGuides:'Show all section guides', floorGrid:'Show floor grid', centerLines:'Show center lines', maskTools:'Mask', displayAids:'Display aids', rotationAssist:'Rotation assist', helpSummary:'Operation notes', renderMode:'View mode', silhouetteMode:'Silhouette', outlineMode:'Outline', yaw:'Horizontal rotation', invertPitch:'Invert up/down rotation', invertYaw:'Invert left/right rotation', primaryModel:'Primary model', compareModel:'Compare model', show:'Show', lineName:'Section name', perimeterLabel:'Perimeter', moveHandle:'Move', lineEmpty:'Load a model to edit section positions.', noModel:'Load a model',
    isoDefaultTitle:'', isoDefaultText:'Import the primary OBJ first.', isoNote:'Next steps appear here as you work.', selectedPrefix:'Editing: ', genericLine:'Section line', genericGuide:'Adjust section positions, then add a compare model or underlay if needed.', meshVertices:'vertices', meshFaces:'total triangles', meshShown:'preview', meshDecimated:'decimated', meshSilhouette:'silhouette', meshFormat:'format', meshHeight:'height', screenError:'Screen error: ', processError:'Process error: ', modelLoading:'Loading model', modelLinesLoading:'Model loaded. Loading section lines', loadComplete:'Loaded', loadCompleteCompare:'Loaded. Showing two-model comparison', modelLoadedLineError:'Model loaded. Line definition: ', mainFirst:'Load a primary model first', compareLoading:'Loading compare model', compareLoaded:'Compare model loaded. Editing compare-model section positions', deleteCompareStatus:'Compare model deleted', deletePrimaryStatus:'Primary model deleted', duckMuted:'The duck could not quack right now.', masksCleared:'Masks cleared',
    maskCreated:'Masked selected mesh faces', needBothModels:'Load both primary and compare models', helpText:'Left drag to rotate. Use horizontal rotation to inspect front, back, and sides. When Draw mask is on, left drag creates a rectangular or lasso mask. Drag the colored dots on the side height bars to move section positions vertically. Middle mouse, right drag, or Shift+left drag pans. Mouse wheel zooms. Fit view frames the current view. View keys follow CLO/Marvelous style: 2 front, 8 back, 4/6 sides, 5 top. Press P to switch orthographic/perspective.', exportNoModel:'Load a model before exporting.', pngExportFailed:'PNG export failed.', silhouettePngLink:'PNG', silhouetteExported:'Silhouette PNG is ready.', compareGenerating:'Generating compare report', compareFailed:'Compare report failed.', compareComplete:'Compare report complete', tableLine:'Line', tablePrimary:'Primary cm', tableCompare:'Compare cm', tableDiff:'Diff cm', generating:'Generating', generateFailed:'Generation failed', done:'Done', tablePerimeter:'Perimeter cm', tableHeight:'Height cm', renderError:'3D display error: ', meshNotFound:'Mesh geometry was not found.', objOnlyError:'Only OBJ files are supported for now. Choose an OBJ file.', fbxBinaryUnsupported:'Binary FBX is not supported yet. Export ASCII FBX or OBJ from CLO/Marvelous.', fbxArraysNotFound:'Vertices / PolygonVertexIndex arrays were not found in the ASCII FBX.', fbxVerticesInvalid:'The FBX Vertices array count is not divisible by 3.', fileRequired:'Specify an OBJ path or choose an OBJ file.', resetStatus:'Reset. Import an OBJ to begin.', initialStatus:'No model loaded. Import an OBJ to begin.'
  }
};
Object.assign(UI_TEXT.ja, {
  heightScaleTitle:'\u8868\u793a\u8eab\u9577\u5408\u308f\u305b',
  heightScaleMatchPrimary:'\u4e3b\u306b\u5408\u308f\u305b\u308b',
  heightScaleMatchCompare:'\u6bd4\u8f03\u306b\u5408\u308f\u305b\u308b',
  heightScaleClear:'\u89e3\u9664',
  heightScaleViewOnly:'\u8868\u793a\u3068\u65ad\u9762\u56f3\u306e\u5bf8\u6cd5\u306b\u53cd\u6620\u3057\u307e\u3059\u3002\u5143OBJ\u306f\u5909\u66f4\u3057\u307e\u305b\u3093\u3002',
  heightScaleNoModel:'OBJ\u3092\u8aad\u307f\u8fbc\u3080\u3068\u8eab\u9577\u5408\u308f\u305b\u3092\u4f7f\u3048\u307e\u3059\u3002',
  heightScaleNeedBoth:'\u4e3b\u30fb\u6bd4\u8f03\u306e\u4e21\u65b9\u3092\u8aad\u307f\u8fbc\u3080\u3068\u4f7f\u3048\u307e\u3059\u3002',
  heightScaleApplied:'\u8868\u793a\u8eab\u9577\u5408\u308f\u305b\u3092\u66f4\u65b0\u3057\u307e\u3057\u305f\u3002'
});
Object.assign(UI_TEXT.en, {
  heightScaleTitle:'Display height match',
  heightScaleMatchPrimary:'Match primary',
  heightScaleMatchCompare:'Match compare',
  heightScaleClear:'Clear',
  heightScaleViewOnly:'Applies to display and section output dimensions. Source OBJ files are not changed.',
  heightScaleNoModel:'Load an OBJ to use height matching.',
  heightScaleNeedBoth:'Load both primary and compare models to use height matching.',
  heightScaleApplied:'Display height matching updated.'
});
let currentLanguage = localStorage.getItem('quackContourLanguage') || 'ja';
function textFor(key) { return (UI_TEXT[currentLanguage] && UI_TEXT[currentLanguage][key]) || UI_TEXT.ja[key] || key; }
function historyText(key) {
  const text = {
    ja: { undoTitle:'1つ戻す', redoTitle:'やり直す', undoEmpty:'戻せる操作がありません。', redoEmpty:'やり直せる操作がありません。', undoDone:'1つ戻しました。', redoDone:'1つやり直しました。' },
    en: { undoTitle:'Undo', redoTitle:'Redo', undoEmpty:'Nothing to undo.', redoEmpty:'Nothing to redo.', undoDone:'Undone.', redoDone:'Redone.' }
  };
  return (text[currentLanguage] && text[currentLanguage][key]) || text.en[key] || key;
}
function applyLanguage() {
  document.documentElement.lang = currentLanguage;
  if (languageToggleButton) languageToggleButton.textContent = textFor('toggle');
  if (appSubtitle) appSubtitle.textContent = textFor('subtitle');
  if (isoGuideNote) isoGuideNote.textContent = textFor('isoNote');
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = textFor(el.dataset.i18n); });
  updateModelOpacityLabels();
  updateLineTargetTabs();
  updateMeshMeta();
  drawIsoGuide();
  renderLineList();
  updateUndoButtons();
  updateDoodleButtons();
  updateDoodleColorControl();
  updateHeightScaleControls();
  draw();
}
function switchLanguage() {
  currentLanguage = currentLanguage === 'ja' ? 'en' : 'ja';
  localStorage.setItem('quackContourLanguage', currentLanguage);
  applyLanguage();
}
let mesh = null;
let compareMesh = null;
let primaryVisible = true;
let compareVisible = true;
let primaryDisplayScale = 1;
let compareDisplayScale = 1;
let lines = [];
let compareLines = [];
let loadedLineRows = [];
let activeLineTarget = 'primary';
let activeIndex = 0;
let yaw = Math.PI / 2, pitch = 0, zoom = 1.0;
let orthographic = true;
let panX = 0, panY = 0;
let dragging = false, dragMode = 'rotate', lastX = 0, lastY = 0;
let underlayScaleStart = null;
let lastClientX = 0, lastClientY = 0;
let panPreviewX = 0, panPreviewY = 0;
let dragDistance = 0;
let isInteracting = false;
let heightBar = null;
let armMaskCache = null;
let renderer = null;
let scene = null;
let camera = null;
let meshObject = null;
let floorGridObject = null;
let suppressFloorGrid = false;
let forceSeparateOffsetsForOutline = false;
let forceOpaqueModelRender = false;
let geometryDirty = true;
let drawPending = false;
let overlayDrawPending = false;
let silhouetteExportUrl = null;
let screenMasks = [];
let maskDraft = null;
let underlay = { img:null, src:null, x:0, y:0, scale:1, rotation:0 };
let underlayMoveMode = false;
let doodleMode = 'none';
let doodleShapes = [];
let doodleDraft = null;
let doodleColor = '#2f80ed';
const DOODLE_WIDTH = 3;
function makeSvgCursor(svg, hotspotX, hotspotY, fallback = 'crosshair') {
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") ${hotspotX} ${hotspotY}, ${fallback}`;
}
const DOODLE_CURSORS = {
  pen: makeSvgCursor('<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28"><path d="M21 3l4 4-15 15-5 1 1-5L21 3z" fill="#f4bf24" stroke="#111820" stroke-width="2" stroke-linejoin="round"/><path d="M18.5 5.5l4 4" stroke="#111820" stroke-width="2" stroke-linecap="round"/></svg>', 5, 23, 'crosshair'),
  circle: makeSvgCursor('<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26"><circle cx="13" cy="13" r="8" fill="rgba(244,191,36,.18)" stroke="#111820" stroke-width="4"/><circle cx="13" cy="13" r="8" fill="none" stroke="#f4bf24" stroke-width="2"/><path d="M13 5v16M5 13h16" stroke="#111820" stroke-width="1.5" stroke-linecap="round" opacity=".7"/></svg>', 13, 13, 'crosshair')
};
const HISTORY_LIMIT = 80;
let undoStack = [];
let redoStack = [];
let pendingHistorySnapshot = null;
let underlayWheelHistoryTimer = null;
const UNDERLAY_GIZMO_HANDLE_SIZE = 14;

function clearPanPreviewTransform() {
  panPreviewX = 0;
  panPreviewY = 0;
  if (canvas) canvas.style.transform = '';
  if (glCanvas) glCanvas.style.transform = '';
}

const PANEL_WIDTH_DEFAULTS = {left: 280, right: 320};
const PANEL_WIDTH_LIMITS = {left: {min: 240, max: 460}, right: {min: 300, max: 600}};
const PANEL_WIDTH_STORAGE = {left: 'quackContourLeftPanelWidth', right: 'quackContourRightPanelWidth'};

function panelWidthCssVar(side) {
  return side === 'left' ? '--left-panel-width' : '--right-panel-width';
}

function readPanelWidth(side) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(panelWidthCssVar(side));
  const parsed = parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : PANEL_WIDTH_DEFAULTS[side];
}

function clampPanelWidth(side, width) {
  const limits = PANEL_WIDTH_LIMITS[side];
  const otherSide = side === 'left' ? 'right' : 'left';
  const otherWidth = readPanelWidth(otherSide);
  const minViewer = window.innerWidth >= 1500 ? 620 : 520;
  const viewportMax = window.innerWidth - otherWidth - minViewer - 34;
  const max = Math.max(limits.min, Math.min(limits.max, viewportMax));
  return Math.round(Math.min(Math.max(width, limits.min), max));
}

function setPanelWidth(side, width, save = true) {
  const clamped = clampPanelWidth(side, width);
  document.documentElement.style.setProperty(panelWidthCssVar(side), `${clamped}px`);
  if (save) localStorage.setItem(PANEL_WIDTH_STORAGE[side], String(clamped));
  requestDraw();
  return clamped;
}

function loadPanelWidths() {
  for (const side of ['left', 'right']) {
    const stored = parseFloat(localStorage.getItem(PANEL_WIDTH_STORAGE[side]) || '');
    setPanelWidth(side, Number.isFinite(stored) ? stored : PANEL_WIDTH_DEFAULTS[side], false);
  }
}

function initPanelResizeHandle(handle, side) {
  if (!handle) return;
  handle.addEventListener('pointerdown', event => {
    if (window.matchMedia('(max-width: 1200px)').matches) return;
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startWidth = readPanelWidth(side);
    const direction = side === 'left' ? 1 : -1;
    handle.classList.add('dragging');
    document.body.classList.add('panel-resizing');
    try { handle.setPointerCapture(event.pointerId); } catch (_) {}

    const move = moveEvent => {
      moveEvent.preventDefault();
      setPanelWidth(side, startWidth + (moveEvent.clientX - startX) * direction);
    };
    const finish = upEvent => {
      try { handle.releasePointerCapture(upEvent.pointerId); } catch (_) {}
      handle.classList.remove('dragging');
      document.body.classList.remove('panel-resizing');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
      draw();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', finish);
    window.addEventListener('pointercancel', finish);
  });
  handle.addEventListener('dblclick', event => {
    event.preventDefault();
    event.stopPropagation();
    setPanelWidth(side, PANEL_WIDTH_DEFAULTS[side]);
    draw();
  });
}

function initResizablePanels() {
  loadPanelWidths();
  initPanelResizeHandle(leftPanelResizer, 'left');
  initPanelResizeHandle(rightPanelResizer, 'right');
}
function updateUnderlayMoveButton() {
  if (!moveUnderlayButton) return;
  moveUnderlayButton.classList.toggle('active', underlayMoveMode);
  moveUnderlayButton.setAttribute('aria-pressed', underlayMoveMode ? 'true' : 'false');
}

function underlayRotation() {
  return Number.isFinite(underlay.rotation) ? underlay.rotation : 0;
}

function underlayImageSize(scale = underlay.scale || 1) {
  if (!underlay.img) return null;
  const iw = underlay.img.naturalWidth || underlay.img.width || 1;
  const ih = underlay.img.naturalHeight || underlay.img.height || 1;
  return {iw, ih, w: iw * scale, h: ih * scale};
}

function underlayCenter(scale = underlay.scale || 1) {
  const size = underlayImageSize(scale);
  if (!size) return {x:0, y:0};
  return {x: underlay.x + size.w / 2, y: underlay.y + size.h / 2};
}

function underlayPointFromLocal(localX, localY, scale = underlay.scale || 1, rotation = underlayRotation(), center = underlayCenter(scale)) {
  const cos = Math.cos(rotation);
  const sin = Math.sin(rotation);
  return {x: center.x + localX * scale * cos - localY * scale * sin, y: center.y + localX * scale * sin + localY * scale * cos};
}

function fitUnderlayToViewer() {
  if (!underlay.img) return;
  const iw = underlay.img.naturalWidth || underlay.img.width || 1;
  const ih = underlay.img.naturalHeight || underlay.img.height || 1;
  const fit = Math.min(canvas.width * 0.78 / iw, canvas.height * 0.78 / ih, 1);
  underlay.scale = Math.max(0.05, fit);
  underlay.rotation = 0;
  underlay.x = (canvas.width - iw * underlay.scale) / 2;
  underlay.y = (canvas.height - ih * underlay.scale) / 2;
}

function setUnderlayCenter(centerX, centerY, scale = underlay.scale || 1) {
  const size = underlayImageSize(scale);
  if (!size) return;
  underlay.x = centerX - size.w / 2;
  underlay.y = centerY - size.h / 2;
}

function scaleUnderlayAroundPoint(pointX, pointY, newScale) {
  const oldScale = underlay.scale || 1;
  const center = underlayCenter(oldScale);
  const rotation = underlayRotation();
  const cos = Math.cos(-rotation);
  const sin = Math.sin(-rotation);
  const dx = pointX - center.x;
  const dy = pointY - center.y;
  const localX = (dx * cos - dy * sin) / oldScale;
  const localY = (dx * sin + dy * cos) / oldScale;
  const nextCenter = {
    x: pointX - (localX * newScale * Math.cos(rotation) - localY * newScale * Math.sin(rotation)),
    y: pointY - (localX * newScale * Math.sin(rotation) + localY * newScale * Math.cos(rotation)),
  };
  underlay.scale = newScale;
  setUnderlayCenter(nextCenter.x, nextCenter.y, newScale);
}

function drawUnderlay() {
  if (!underlay.img) return;
  const size = underlayImageSize();
  const center = underlayCenter();
  ctx.save();
  ctx.globalAlpha = 0.42;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.translate(center.x, center.y);
  ctx.rotate(underlayRotation());
  ctx.drawImage(underlay.img, -size.w / 2, -size.h / 2, size.w, size.h);
  ctx.restore();
}

function underlayCorners(scale = underlay.scale || 1, rotation = underlayRotation(), center = underlayCenter(scale)) {
  const size = underlayImageSize(scale);
  if (!size) return [];
  const hx = size.iw / 2;
  const hy = size.ih / 2;
  return [
    {name:'nw', localX:-hx, localY:-hy, cursor:'nwse-resize'},
    {name:'ne', localX:hx, localY:-hy, cursor:'nesw-resize'},
    {name:'se', localX:hx, localY:hy, cursor:'nwse-resize'},
    {name:'sw', localX:-hx, localY:hy, cursor:'nesw-resize'},
  ].map(corner => ({...corner, ...underlayPointFromLocal(corner.localX, corner.localY, scale, rotation, center)}));
}

function underlayBounds() {
  if (!underlay.img) return null;
  const size = underlayImageSize();
  const center = underlayCenter();
  const rotation = underlayRotation();
  const corners = underlayCorners(underlay.scale || 1, rotation, center);
  const xs = corners.map(p => p.x);
  const ys = corners.map(p => p.y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  const right = Math.max(...xs);
  const bottom = Math.max(...ys);
  return {x, y, w:right - x, h:bottom - y, right, bottom, center, rotation, corners, imageW:size.w, imageH:size.h};
}

function underlayGizmoHandles() {
  const b = underlayBounds();
  if (!b) return [];
  const opposite = {nw:'se', ne:'sw', se:'nw', sw:'ne'};
  const corners = b.corners.map(corner => {
    const anchor = b.corners.find(other => other.name === opposite[corner.name]);
    return {...corner, type:'scale', anchorX:anchor.x, anchorY:anchor.y, anchorLocalX:anchor.localX, anchorLocalY:anchor.localY};
  });
  const size = underlayImageSize();
  const rotateHandle = underlayPointFromLocal(0, -size.ih / 2 - 34 / Math.max(0.05, underlay.scale || 1), underlay.scale || 1, b.rotation, b.center);
  return [...corners, {name:'rotate', type:'rotate', x:rotateHandle.x, y:rotateHandle.y, centerX:b.center.x, centerY:b.center.y, cursor:'grab'}];
}

function hitUnderlayGizmo(x, y) {
  if (!underlayMoveMode || !underlay.img) return null;
  for (const handle of underlayGizmoHandles()) {
    if (handle.type === 'rotate') {
      if (Math.hypot(x - handle.x, y - handle.y) <= UNDERLAY_GIZMO_HANDLE_SIZE) return handle;
    } else {
      const half = UNDERLAY_GIZMO_HANDLE_SIZE;
      if (Math.abs(x - handle.x) <= half && Math.abs(y - handle.y) <= half) return handle;
    }
  }
  return null;
}

function drawUnderlayScaleGizmo() {
  if (!underlayMoveMode || !underlay.img) return;
  const b = underlayBounds();
  if (!b || b.w < 8 || b.h < 8) return;
  const handles = underlayGizmoHandles();
  const rotateHandle = handles.find(handle => handle.type === 'rotate');
  ctx.save();
  ctx.lineWidth = 2;
  ctx.strokeStyle = 'rgba(242, 192, 55, 0.95)';
  ctx.setLineDash([8, 5]);
  ctx.beginPath();
  b.corners.forEach((corner, index) => index ? ctx.lineTo(corner.x, corner.y) : ctx.moveTo(corner.x, corner.y));
  ctx.closePath();
  ctx.stroke();
  ctx.setLineDash([]);
  if (rotateHandle) {
    const topMid = underlayPointFromLocal(0, -(underlay.img.naturalHeight || underlay.img.height || 1) / 2, underlay.scale || 1, b.rotation, b.center);
    ctx.beginPath();
    ctx.moveTo(topMid.x, topMid.y);
    ctx.lineTo(rotateHandle.x, rotateHandle.y);
    ctx.strokeStyle = 'rgba(242, 192, 55, 0.85)';
    ctx.stroke();
  }
  for (const handle of handles) {
    ctx.fillStyle = '#101114';
    ctx.strokeStyle = 'rgba(242, 192, 55, 0.95)';
    ctx.lineWidth = 2;
    if (handle.type === 'rotate') {
      ctx.beginPath();
      ctx.arc(handle.x, handle.y, UNDERLAY_GIZMO_HANDLE_SIZE / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(handle.x, handle.y, 2.7, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(242, 192, 55, 0.95)';
      ctx.fill();
    } else {
      const s = UNDERLAY_GIZMO_HANDLE_SIZE;
      ctx.fillRect(handle.x - s / 2, handle.y - s / 2, s, s);
      ctx.strokeRect(handle.x - s / 2, handle.y - s / 2, s, s);
      ctx.beginPath();
      ctx.arc(handle.x, handle.y, 2.4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(242, 192, 55, 0.95)';
      ctx.fill();
    }
  }
  const label = currentLanguage === 'ja' ? '\u4e38\u3067\u56de\u8ee2 / \u89d2\u3067\u62e1\u5927\u7e2e\u5c0f' : 'Round handle rotates / corners scale';
  ctx.font = '700 12px Meiryo, sans-serif';
  const paddingX = 8;
  const labelW = ctx.measureText(label).width + paddingX * 2;
  const labelH = 24;
  const lx = Math.max(8, Math.min(canvas.width - labelW - 8, b.right - labelW));
  const ly = Math.max(8, b.y - labelH - 8);
  ctx.fillStyle = 'rgba(16, 17, 20, 0.9)';
  ctx.strokeStyle = 'rgba(242, 192, 55, 0.75)';
  ctx.lineWidth = 1;
  drawRoundedRect(lx, ly, labelW, labelH, 5);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = 'rgba(242, 192, 55, 0.98)';
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'left';
  ctx.fillText(label, lx + paddingX, ly + labelH / 2 + 0.5);
  ctx.restore();
}
function loadUnderlayFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      pushHistory();
      underlay = { img, src:reader.result, x:0, y:0, scale:1, rotation:0 };
      fitUnderlayToViewer();
      statusEl.textContent = '';
      draw();
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

function clonePlain(value) {
  return JSON.parse(JSON.stringify(value));
}

function snapshotUnderlay() {
  return underlay.img ? { img:underlay.img, src:underlay.src, x:underlay.x, y:underlay.y, scale:underlay.scale, rotation:underlayRotation() } : { img:null, src:null, x:0, y:0, scale:1, rotation:0 };
}

function snapshotMasks() {
  return screenMasks.map(mask => ({
    targetName: mask.targetName,
    vertices: [...(mask.vertices || [])],
    rect: mask.rect ? {...mask.rect} : null,
    type: mask.type || 'rect',
    points: mask.points ? mask.points.map(point => ({...point})) : null,
  }));
}

function restoreMasks(maskList) {
  screenMasks = (maskList || []).map(mask => {
    const restored = {
      targetName: mask.targetName,
      vertices: [...(mask.vertices || [])],
      rect: mask.rect ? {...mask.rect} : null,
    type: mask.type || 'rect',
    points: mask.points ? mask.points.map(point => ({...point})) : null,
    };
    restored._vertexSet = new Set(restored.vertices);
    return restored;
  });
}

function snapshotContour() {
  return {
    mesh,
    compareMesh,
    primaryVisible,
    compareVisible,
    primaryDisplayScale,
    compareDisplayScale,
    lines: clonePlain(lines),
    compareLines: clonePlain(compareLines),
    loadedLineRows: clonePlain(loadedLineRows),
    activeLineTarget,
    activeIndex,
    yaw,
    pitch,
    zoom,
    orthographic,
    panX,
    panY,
    underlay: snapshotUnderlay(),
    underlayMoveMode,
    screenMasks: snapshotMasks(),
    doodleShapes: clonePlain(doodleShapes),
    controls: {
      primaryColor: primarySilhouetteColorInput ? primarySilhouetteColorInput.value : '#1f1f1f',
      compareColor: compareSilhouetteColorInput ? compareSilhouetteColorInput.value : '#8c939b',
      primaryOpacity: primaryModelOpacityInput ? primaryModelOpacityInput.value : '100',
      compareOpacity: compareModelOpacityInput ? compareModelOpacityInput.value : '100',
      viewerBg: viewerBgColorInput ? viewerBgColorInput.value : '#ffffff',
      compareLayout: compareLayoutInput ? compareLayoutInput.value : 'separate',
      renderMode: renderModeInput ? renderModeInput.value : 'silhouette',
      lineGuideOnly: lineGuideOnlyInput ? lineGuideOnlyInput.checked : false,
      showFloorGrid: showFloorGridInput ? showFloorGridInput.checked : true,
      showCenterLines: showCenterLinesInput ? showCenterLinesInput.checked : true,
      maskDrawMode: maskDrawModeInput ? maskDrawModeInput.checked : false,
      maskShapeMode: maskShapeModeInput ? maskShapeModeInput.value : 'rect',
      pickSectionHeight: pickSectionHeightInput ? pickSectionHeightInput.checked : false,
      invertPitch: invertPitchInput ? invertPitchInput.checked : false,
      invertYaw: invertYawInput ? invertYawInput.checked : false,
    }
  };
}

function restoreContour(snapshot) {
  mesh = snapshot.mesh || null;
  compareMesh = snapshot.compareMesh || null;
  primaryVisible = snapshot.primaryVisible !== false && !!mesh;
  compareVisible = snapshot.compareVisible !== false && !!compareMesh;
  primaryDisplayScale = Number.isFinite(snapshot.primaryDisplayScale) ? snapshot.primaryDisplayScale : 1;
  compareDisplayScale = Number.isFinite(snapshot.compareDisplayScale) ? snapshot.compareDisplayScale : 1;
  lines = clonePlain(snapshot.lines || []);
  compareLines = clonePlain(snapshot.compareLines || []);
  loadedLineRows = clonePlain(snapshot.loadedLineRows || []);
  activeLineTarget = snapshot.activeLineTarget || 'primary';
  activeIndex = snapshot.activeIndex || 0;
  yaw = Number.isFinite(snapshot.yaw) ? snapshot.yaw : Math.PI / 2;
  pitch = Number.isFinite(snapshot.pitch) ? snapshot.pitch : 0;
  zoom = Number.isFinite(snapshot.zoom) ? snapshot.zoom : 1.0;
  orthographic = snapshot.orthographic !== false;
  panX = Number.isFinite(snapshot.panX) ? snapshot.panX : 0;
  panY = Number.isFinite(snapshot.panY) ? snapshot.panY : 0;
  panPreviewX = 0;
  panPreviewY = 0;
  underlay = snapshot.underlay ? {...snapshot.underlay, rotation:snapshot.underlay.rotation || 0} : { img:null, src:null, x:0, y:0, scale:1, rotation:0 };
  underlayMoveMode = !!snapshot.underlayMoveMode && !!underlay.img;
  restoreMasks(snapshot.screenMasks);
  doodleShapes = clonePlain(snapshot.doodleShapes || []);
  doodleDraft = null;
  const controls = snapshot.controls || {};
  if (primarySilhouetteColorInput && controls.primaryColor) primarySilhouetteColorInput.value = controls.primaryColor;
  if (compareSilhouetteColorInput && controls.compareColor) compareSilhouetteColorInput.value = controls.compareColor;
  if (primaryModelOpacityInput && controls.primaryOpacity) primaryModelOpacityInput.value = controls.primaryOpacity;
  if (compareModelOpacityInput && controls.compareOpacity) compareModelOpacityInput.value = controls.compareOpacity;
  updateModelOpacityLabels();
  if (viewerBgColorInput && controls.viewerBg) viewerBgColorInput.value = controls.viewerBg;
  if (compareLayoutInput && controls.compareLayout) compareLayoutInput.value = controls.compareLayout;
  if (renderModeInput && controls.renderMode) renderModeInput.value = controls.renderMode;
  if (lineGuideOnlyInput) lineGuideOnlyInput.checked = !!controls.lineGuideOnly;
  if (showFloorGridInput) showFloorGridInput.checked = controls.showFloorGrid !== false;
  if (showCenterLinesInput) showCenterLinesInput.checked = controls.showCenterLines !== false;
  if (maskDrawModeInput) maskDrawModeInput.checked = !!controls.maskDrawMode;
  if (maskShapeModeInput) maskShapeModeInput.value = controls.maskShapeMode || 'rect';
  if (pickSectionHeightInput) pickSectionHeightInput.checked = !!controls.pickSectionHeight;
  if (invertPitchInput) invertPitchInput.checked = !!controls.invertPitch;
  if (invertYawInput) invertYawInput.checked = !!controls.invertYaw;
  if (showPrimaryModelInput) showPrimaryModelInput.checked = primaryVisible;
  if (showCompareModelInput) showCompareModelInput.checked = compareVisible;
  maskDraft = null;
  underlayScaleStart = null;
  armMaskCache = null;
  suppressFloorGrid = false;
  geometryDirty = true;
  clearPanPreviewTransform();
  if (hideArmsInput.checked) rebuildArmMask();
  syncYawSlider();
  updateUnderlayMoveButton();
  updateDoodleButtons();
  updateViewerCursor();
  updateLineTargetTabs();
  renderLineList();
  updateMeshMeta();
  updateModelControls();
  applyViewerBackground();
  drawIsoGuide();
  draw();
}

function pushBoundedHistory(stack, snapshot) {
  stack.push(snapshot);
  if (stack.length > HISTORY_LIMIT) stack.shift();
}

function updateUndoButtons() {
  if (undoButton) {
    undoButton.disabled = undoStack.length === 0;
    undoButton.title = historyText('undoTitle');
    undoButton.setAttribute('aria-label', historyText('undoTitle'));
  }
  if (redoButton) {
    redoButton.disabled = redoStack.length === 0;
    redoButton.title = historyText('redoTitle');
    redoButton.setAttribute('aria-label', historyText('redoTitle'));
  }
}

function pushHistory(snapshot = snapshotContour()) {
  pushBoundedHistory(undoStack, snapshot);
  redoStack = [];
  updateUndoButtons();
}

function beginHistory() {
  if (!pendingHistorySnapshot) pendingHistorySnapshot = snapshotContour();
}

function commitHistory() {
  if (!pendingHistorySnapshot) return;
  pushHistory(pendingHistorySnapshot);
  pendingHistorySnapshot = null;
}

function cancelHistory() {
  pendingHistorySnapshot = null;
}

function clearUndoHistory() {
  undoStack = [];
  redoStack = [];
  pendingHistorySnapshot = null;
  if (underlayWheelHistoryTimer) window.clearTimeout(underlayWheelHistoryTimer);
  underlayWheelHistoryTimer = null;
  updateUndoButtons();
}

function undoContour() {
  if (underlayWheelHistoryTimer) {
    window.clearTimeout(underlayWheelHistoryTimer);
    underlayWheelHistoryTimer = null;
    commitHistory();
  }
  const snapshot = undoStack.pop();
  if (!snapshot) {
    statusEl.textContent = '';
    updateUndoButtons();
    return;
  }
  pushBoundedHistory(redoStack, snapshotContour());
  restoreContour(snapshot);
  updateUndoButtons();
  statusEl.textContent = '';
}

function redoContour() {
  if (underlayWheelHistoryTimer) {
    window.clearTimeout(underlayWheelHistoryTimer);
    underlayWheelHistoryTimer = null;
    commitHistory();
  }
  const snapshot = redoStack.pop();
  if (!snapshot) {
    statusEl.textContent = '';
    updateUndoButtons();
    return;
  }
  pushBoundedHistory(undoStack, snapshotContour());
  restoreContour(snapshot);
  updateUndoButtons();
  statusEl.textContent = '';
}

function yawToSliderDegrees() {
  let deg = yaw * 180 / Math.PI;
  while (deg > 180) deg -= 360;
  while (deg < -180) deg += 360;
  return Math.round(deg);
}

function syncYawSlider() {
  const deg = yawToSliderDegrees();
  viewYawSlider.value = String(deg);
  viewYawValue.textContent = `${deg}°`;
}

function setYawFromSlider() {
  yaw = parseFloat(viewYawSlider.value) * Math.PI / 180;
  geometryDirty = true;
  syncYawSlider();
  requestDraw();
}
function requestDraw() {
  if (drawPending) return;
  drawPending = true;
  window.requestAnimationFrame(() => {
    drawPending = false;
    draw();
  });
}

function requestOverlayDraw() {
  if (overlayDrawPending) return;
  overlayDrawPending = true;
  window.requestAnimationFrame(() => {
    overlayDrawPending = false;
    drawOverlaysFromBase();
  });
}



const ISO_GUIDES = {
  neck: {
    title: '首付け根',
    lines: [
      'ISO 8559-1参考: 首付け根まわり。',
      '上面図では水平近似として扱います。'
    ]
  },
  shoulder: {
    title: '肩 / 肩甲骨高',
    lines: [
      '主なISO周径ではなく、形状観察用の補助ライン。',
      '肩甲骨の張りと肩先への移行を確認します。'
    ]
  },
  bust: {
    title: 'バスト',
    lines: [
      'ISO 8559-1参考: 乳頭点を通る水平周径。',
      '前後左右に傾けず、水平に切ることを優先します。'
    ]
  },
  underbust: {
    title: 'アンダーバスト',
    lines: [
      '乳房直下を通る水平周径。',
      'CLOの値やブララインとの比較に使います。'
    ]
  },
  waist: {
    title: 'ウエスト',
    lines: [
      'ISO 8559-1参考: 胴の自然なくびれ、最小周径位置。',
      '腹部のくびれと水平性を確認します。'
    ]
  },
  upperhip: {
    title: 'ヒップ上部',
    lines: [
      '腰骨から上臀部の張りを見る補助ライン。',
      'ヒップ最大周径とは分けて確認します。'
    ]
  },
  hip: {
    title: 'ヒップ',
    lines: [
      'ISO 8559-1参考: 臀部の最大突出部を通る水平周径。',
      '前後差と左右の張りを確認します。'
    ]
  }
};
const ISO_GUIDES_EN = {
  neck: {
    title: 'Neck base',
    lines: [
      'ISO 8559-1 reference: neck-base girth.',
      'Treat as a horizontal approximation in top view.'
    ]
  },
  shoulder: {
    title: 'Shoulder / scapula height',
    lines: [
      'Auxiliary line for observing shape, not a main ISO girth.',
      'Check the scapula protrusion and shoulder transition.'
    ]
  },
  bust: {
    title: 'Bust',
    lines: [
      'ISO 8559-1 reference: horizontal girth through the bust points.',
      'Prefer a level horizontal slice without front/back or side tilt.'
    ]
  },
  underbust: {
    title: 'Underbust',
    lines: [
      'Horizontal girth immediately below the bust.',
      'Useful for comparison with CLO values or bra-line placement.'
    ]
  },
  waist: {
    title: 'Waist',
    lines: [
      'ISO 8559-1 reference: natural minimum girth of the torso.',
      'Check the waist indentation and horizontal level.'
    ]
  },
  upperhip: {
    title: 'Upper hip',
    lines: [
      'Auxiliary line to observe the upper-hip fullness.',
      'Review separately from the maximum hip girth.'
    ]
  },
  hip: {
    title: 'Hip',
    lines: [
      'ISO 8559-1 reference: horizontal girth through maximum buttock prominence.',
      'Check front/back depth and side fullness.'
    ]
  }
};

function parseCsv(text) {
  const rows = text.trim().split(/\\r?\\n/).filter(Boolean).map(row => row.split(','));
  const header = rows.shift().map(s => s.trim());
  return rows.map(cells => {
    const row = {};
    header.forEach((h,i) => row[h] = (cells[i] || '').trim());
    return row;
  });
}

function csvEscape(value) {
  const text = String(value ?? '');
  const needsQuote = text.includes(',') || text.includes('"') || text.includes(String.fromCharCode(10)) || text.includes(String.fromCharCode(13));
  return needsQuote ? '"' + text.replaceAll('"', '""') + '"' : text;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
function outputLinesForExport(sourceLines = lines, options = {}) {
  const visibleOnly = options.visibleOnly !== false;
  const filtered = visibleOnly ? sourceLines.filter(line => line && line.preview_visible !== false) : sourceLines.filter(Boolean);
  return filtered;
}

function csvFromLines(sourceLines = lines, options = {}) {
  const header = ['name','label','color','height_from_floor_cm','height_ratio','expected_cm','scan_around_cm','scan_step_cm','select','scan'];
  const exportLines = outputLinesForExport(sourceLines, options);
  if (!exportLines.length) return '';
  const rows = [header.join(',')];
  for (const l of exportLines) {
    const row = {...l, scan: 'false'};
    const expected = parseFloat(row.expected_cm || '');
    if (!Number.isFinite(expected)) {
      row.expected_cm = '';
      row.select = row.select === 'closest' ? 'largest' : (row.select || 'largest');
      row.scan = 'false';
    }
    rows.push(header.map(h => csvEscape(row[h] ?? '')).join(','));
  }
  return rows.join(String.fromCharCode(10));
}
function noVisibleLinesMessage() {
  return currentLanguage === 'ja' ? '\u51fa\u529b\u3059\u308b\u65ad\u9762\u30921\u3064\u4ee5\u4e0a\u30c1\u30a7\u30c3\u30af\u3057\u3066\u304f\u3060\u3055\u3044\u3002' : 'Check at least one section line to export.';
}

function csvFromVisibleLinesOrThrow(sourceLines = lines) {
  const csv = csvFromLines(sourceLines);
  if (!csv) throw new Error(noVisibleLinesMessage());
  return csv;
}
function normalizeLines(rows, targetMesh = mesh) {
  if (!targetMesh) return rows;
  const h = targetMesh.height_cm;
  return rows.map((row, i) => {
    const out = {...row};
    if (!out.color) out.color = ['#6F4E37','#8D4ED8','#C00028','#E67E22','#0077B6','#2E8B57','#1E1E1E'][i % 7];
    if (!out.label) out.label = out.name;
    if (!out.height_from_floor_cm) {
      const ratio = parseFloat(out.height_ratio || '0.6');
      out.height_from_floor_cm = (h * ratio).toFixed(2);
    }
    if (!out.scan_step_cm) out.scan_step_cm = '0.25';
    if (!out.scan_around_cm) out.scan_around_cm = '4.0';
    if (!out.select) out.select = out.expected_cm ? 'closest' : 'largest';
    if (!out.scan) out.scan = out.expected_cm ? 'true' : 'false';
    if (out.select === 'closest' && !Number.isFinite(parseFloat(out.expected_cm || ''))) {
      out.select = 'largest';
      out.scan = 'false';
    }
    return out;
  });
}

function modelIsVisible(targetName) {
  return targetName === 'compare' ? compareVisible && !!compareMesh : primaryVisible && !!mesh;
}

function modelColorInput(targetName) {
  return targetName === 'compare' ? compareSilhouetteColorInput : primarySilhouetteColorInput;
}

function modelColor(targetName) {
  const input = modelColorInput(targetName);
  return input && input.value ? input.value : (targetName === 'compare' ? '#8c939b' : '#1f1f1f');
}

function modelOpacityInput(targetName) {
  return targetName === 'compare' ? compareModelOpacityInput : primaryModelOpacityInput;
}

function modelOpacityPercent(targetName) {
  const fallback = targetName === 'compare' ? 70 : 100;
  const input = modelOpacityInput(targetName);
  const value = input ? parseFloat(input.value) : fallback;
  return Math.max(10, Math.min(100, Number.isFinite(value) ? value : fallback));
}

function modelOpacity(targetName) {
  if (forceOpaqueModelRender) return 1;
  return modelOpacityPercent(targetName) / 100;
}

function displayScaleForTarget(targetName) {
  const value = targetName === 'compare' ? compareDisplayScale : primaryDisplayScale;
  return Number.isFinite(value) && value > 0 ? value : 1;
}

function displayHeightForTarget(targetName) {
  const targetMesh = meshForLineTarget(targetName);
  return targetMesh ? targetMesh.height_cm * displayScaleForTarget(targetName) : 0;
}

function applyDisplayScaleState(options = {}) {
  geometryDirty = true;
  updateHeightScaleControls();
  updateMeshMeta();
  renderLineList();
  if (heightScaleNote) heightScaleNote.textContent = textFor('heightScaleViewOnly');
  if (options.fit !== false) fitViewToModels();
  else draw();
}

function setDisplayScaleForTarget(targetName, scale, options = {}) {
  const targetMesh = meshForLineTarget(targetName);
  if (!targetMesh) return false;
  const next = Math.max(0.05, Math.min(8, Number.isFinite(scale) ? scale : 1));
  if (options.push !== false) pushHistory();
  if (targetName === 'compare') compareDisplayScale = next;
  else primaryDisplayScale = next;
  applyDisplayScaleState(options);
  return true;
}

function resetAllDisplayScales(options = {}) {
  if (!mesh && !compareMesh) return false;
  if (options.push !== false) pushHistory();
  primaryDisplayScale = 1;
  compareDisplayScale = 1;
  applyDisplayScaleState(options);
  return true;
}

function setHeightScaleMode(mode) {
  if (mode === 'clear') return resetAllDisplayScales();
  if (!mesh || !compareMesh) return false;
  pushHistory();
  if (mode === 'primary') {
    primaryDisplayScale = 1;
    compareDisplayScale = mesh.height_cm > 0 && compareMesh.height_cm > 0 ? mesh.height_cm / compareMesh.height_cm : 1;
  } else if (mode === 'compare') {
    compareDisplayScale = 1;
    primaryDisplayScale = mesh.height_cm > 0 && compareMesh.height_cm > 0 ? compareMesh.height_cm / mesh.height_cm : 1;
  } else {
    primaryDisplayScale = 1;
    compareDisplayScale = 1;
  }
  applyDisplayScaleState();
  return true;
}

function currentHeightScaleMode() {
  const primaryScale = displayScaleForTarget('primary');
  const compareScale = displayScaleForTarget('compare');
  if (Math.abs(primaryScale - 1) < 0.001 && Math.abs(compareScale - 1) < 0.001) return 'clear';
  if (mesh && compareMesh) {
    const primaryHeight = displayHeightForTarget('primary');
    const compareHeight = displayHeightForTarget('compare');
    if (Math.abs(primaryHeight - compareHeight) < 0.05) {
      if (Math.abs(primaryScale - 1) < 0.001) return 'primary';
      if (Math.abs(compareScale - 1) < 0.001) return 'compare';
    }
  }
  return 'custom';
}

function scaledPointForTarget(targetMesh, targetName, v, offset = zeroOffset()) {
  const b = targetMesh.bounds;
  const s = displayScaleForTarget(targetName);
  const o = normalizeOffset(offset);
  const cx = (b.min[0] + b.max[0]) / 2;
  const cz = (b.min[2] + b.max[2]) / 2;
  const floor = b.min[1];
  return [
    cx + (v[0] - cx) * s + o.x,
    floor + (v[1] - floor) * s + o.y,
    cz + (v[2] - cz) * s + o.z,
  ];
}

function scaledBoundsForTarget(targetMesh, targetName, offset = zeroOffset()) {
  const b = targetMesh.bounds;
  const s = displayScaleForTarget(targetName);
  const o = normalizeOffset(offset);
  const cx = (b.min[0] + b.max[0]) / 2;
  const cz = (b.min[2] + b.max[2]) / 2;
  const x0 = cx + (b.min[0] - cx) * s + o.x;
  const x1 = cx + (b.max[0] - cx) * s + o.x;
  const z0 = cz + (b.min[2] - cz) * s + o.z;
  const z1 = cz + (b.max[2] - cz) * s + o.z;
  return {
    min:[Math.min(x0, x1), b.min[1] + o.y, Math.min(z0, z1)],
    max:[Math.max(x0, x1), b.min[1] + (b.max[1] - b.min[1]) * s + o.y, Math.max(z0, z1)],
  };
}

function lineWorldY(targetMesh, targetName, line, offset = zeroOffset()) {
  const o = normalizeOffset(offset);
  const h = parseFloat(line.height_from_floor_cm || '0');
  return targetMesh.bounds.min[1] + (Number.isFinite(h) ? h : 0) * displayScaleForTarget(targetName) + o.y;
}

function projectPointForTarget(v, targetMesh, targetName, offset = zeroOffset()) {
  return projectPoint(scaledPointForTarget(targetMesh, targetName, v, offset));
}

function updateHeightScaleControls() {
  const hasPrimary = !!mesh;
  const hasCompare = !!compareMesh;
  const mode = currentHeightScaleMode();
  const buttons = [
    [heightScaleMatchPrimaryButton, 'primary'],
    [heightScaleMatchCompareButton, 'compare'],
    [heightScaleClearButton, 'clear'],
  ];
  for (const [button, buttonMode] of buttons) {
    if (!button) continue;
    button.disabled = buttonMode === 'clear' ? !(hasPrimary || hasCompare) : !(hasPrimary && hasCompare);
    button.classList.toggle('active', mode === buttonMode);
  }
  if (!heightScaleNote) return;
  if (!hasPrimary && !hasCompare) {
    heightScaleNote.textContent = textFor('heightScaleNoModel');
  } else if (!(hasPrimary && hasCompare)) {
    heightScaleNote.textContent = textFor('heightScaleNeedBoth');
  } else {
    heightScaleNote.textContent = textFor('heightScaleViewOnly');
  }
}
function isOverlayLayout() {
  return !!(compareLayoutInput && compareLayoutInput.value === 'overlay');
}

function overlayPerspectiveWarning() {
  return currentLanguage === 'ja'
    ? '重ねる表示では、パースを切ると比較しやすくなります。'
    : 'Overlay comparison is easier to read in orthographic view.';
}

function updateModelOpacityLabels() {
  if (primaryModelOpacityValue) primaryModelOpacityValue.textContent = Math.round(modelOpacityPercent('primary')) + '%';
  if (compareModelOpacityValue) compareModelOpacityValue.textContent = Math.round(modelOpacityPercent('compare')) + '%';
}

function viewerBackgroundColor() {
  return viewerBgColorInput && viewerBgColorInput.value ? viewerBgColorInput.value : '#ffffff';
}

function applyViewerBackground() {
  const color = viewerBackgroundColor();
  document.documentElement.style.setProperty('--viewer', color);
  if (viewerWrap) viewerWrap.style.backgroundColor = color;
  if (canvas) canvas.style.backgroundColor = 'transparent';
  if (glCanvas) glCanvas.style.backgroundColor = color;
  if (renderer && typeof THREE !== 'undefined') {
    renderer.setClearColor(new THREE.Color(color), 1);
  }
  if (scene && typeof THREE !== 'undefined') {
    scene.background = new THREE.Color(color);
  }
}

function updateModelControls() {
  showPrimaryModelInput.disabled = !mesh;
  deletePrimaryModelButton.disabled = !mesh;
  showCompareModelInput.disabled = !compareMesh;
  deleteCompareModelButton.disabled = !compareMesh;
  showPrimaryModelInput.checked = primaryVisible && !!mesh;
  showCompareModelInput.checked = compareVisible && !!compareMesh;
  updateHeightScaleControls();
}
function meshForLineTarget(targetName) {
  return targetName === 'compare' ? compareMesh : mesh;
}

function linesForLineTarget(targetName) {
  return targetName === 'compare' ? compareLines : lines;
}

function currentMesh() {
  return meshForLineTarget(activeLineTarget);
}

function currentLines() {
  return linesForLineTarget(activeLineTarget);
}

function sectionPlaneY(targetMesh, line) {
  const h = parseFloat(line && line.height_from_floor_cm || '0');
  return targetMesh.bounds.min[1] + (Number.isFinite(h) ? h : 0);
}

function distance2d(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

function addUniqueSectionPoint(points, point, eps = 1e-5) {
  for (const existing of points) {
    if (Math.hypot(existing[0] - point[0], existing[1] - point[1]) <= eps) return;
  }
  points.push(point);
}

function segmentForTriangleAtY(vertices, face, y) {
  const pts = [];
  const tri = [vertices[face[0]], vertices[face[1]], vertices[face[2]]];
  if (!tri[0] || !tri[1] || !tri[2]) return null;
  const eps = 1e-6;
  for (let i = 0; i < 3; i++) {
    const a = tri[i];
    const b = tri[(i + 1) % 3];
    const da = a[1] - y;
    const db = b[1] - y;
    if (Math.abs(da) <= eps && Math.abs(db) <= eps) {
      addUniqueSectionPoint(pts, [a[0], a[2]], eps);
      addUniqueSectionPoint(pts, [b[0], b[2]], eps);
    } else if (Math.abs(da) <= eps) {
      addUniqueSectionPoint(pts, [a[0], a[2]], eps);
    } else if (Math.abs(db) <= eps) {
      addUniqueSectionPoint(pts, [b[0], b[2]], eps);
    } else if (da * db < 0) {
      const t = da / (da - db);
      addUniqueSectionPoint(pts, [
        a[0] + (b[0] - a[0]) * t,
        a[2] + (b[2] - a[2]) * t
      ], eps);
    }
  }
  if (pts.length < 2) return null;
  if (pts.length === 2) return pts;
  let best = null;
  let bestLength = -1;
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const length = distance2d(pts[i], pts[j]);
      if (length > bestLength) {
        bestLength = length;
        best = [pts[i], pts[j]];
      }
    }
  }
  return best;
}

function quantizeSectionPoint(point, tolerance) {
  return `${Math.round(point[0] / tolerance)},${Math.round(point[1] / tolerance)}`;
}

function canonicalSectionEdge(a, b) {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

function buildSectionPaths(segments, tolerance = 1e-4) {
  const points = new Map();
  const adjacency = new Map();
  const unusedEdges = new Set();
  const addNeighbor = (a, b) => {
    if (!adjacency.has(a)) adjacency.set(a, []);
    adjacency.get(a).push(b);
  };
  for (const [a, b] of segments) {
    const qa = quantizeSectionPoint(a, tolerance);
    const qb = quantizeSectionPoint(b, tolerance);
    if (qa === qb) continue;
    if (!points.has(qa)) points.set(qa, a);
    if (!points.has(qb)) points.set(qb, b);
    addNeighbor(qa, qb);
    addNeighbor(qb, qa);
    unusedEdges.add(canonicalSectionEdge(qa, qb));
  }

  const paths = [];
  while (unusedEdges.size) {
    const firstEdge = unusedEdges.values().next().value;
    unusedEdges.delete(firstEdge);
    let [start, current] = firstEdge.split('|');
    let previous = start;
    const pathKeys = [start, current];

    while (true) {
      const neighbors = adjacency.get(current) || [];
      let candidates = neighbors.filter(node => unusedEdges.has(canonicalSectionEdge(current, node)) && node !== previous);
      if (!candidates.length) candidates = neighbors.filter(node => unusedEdges.has(canonicalSectionEdge(current, node)));
      if (!candidates.length) break;
      const next = candidates[0];
      unusedEdges.delete(canonicalSectionEdge(current, next));
      previous = current;
      current = next;
      pathKeys.push(current);
      if (current === start) break;
    }

    const coords = pathKeys.map(key => points.get(key)).filter(Boolean);
    if (coords.length < 2) continue;
    const closed = coords.length > 2 && distance2d(coords[0], coords[coords.length - 1]) <= tolerance * 2;
    let perimeter = 0;
    for (let i = 0; i < coords.length - 1; i++) perimeter += distance2d(coords[i], coords[i + 1]);
    if (closed && coords.length > 2) perimeter += distance2d(coords[0], coords[coords.length - 1]);
    const xs = coords.map(p => p[0]);
    const zs = coords.map(p => p[1]);
    paths.push({
      perimeter,
      closed,
      point_count: coords.length,
      centroid: [xs.reduce((sum, v) => sum + v, 0) / coords.length, zs.reduce((sum, v) => sum + v, 0) / coords.length],
      bbox: [Math.min(...xs), Math.min(...zs), Math.max(...xs), Math.max(...zs)]
    });
  }
  return paths.sort((a, b) => b.perimeter - a.perimeter);
}

function selectSectionPath(paths, line) {
  if (!paths.length) return null;
  const closedPaths = paths.filter(path => path.closed);
  const usable = closedPaths.length ? closedPaths : paths;
  const expected = parseFloat(line && line.expected_cm || '');
  const mode = line && line.select ? line.select : (Number.isFinite(expected) ? 'closest' : 'largest');
  if (mode === 'closest' && Number.isFinite(expected)) {
    return usable.reduce((best, path) => Math.abs(path.perimeter - expected) < Math.abs(best.perimeter - expected) ? path : best, usable[0]);
  }
  if (mode === 'center') {
    return usable.reduce((best, path) => Math.hypot(path.centroid[0], path.centroid[1]) < Math.hypot(best.centroid[0], best.centroid[1]) ? path : best, usable[0]);
  }
  return usable.reduce((best, path) => path.perimeter > best.perimeter ? path : best, usable[0]);
}

function sectionPathInfo(targetMesh, line) {
  if (!targetMesh || !line || !targetMesh.vertices || !targetMesh.faces) return null;
  const h = parseFloat(line.height_from_floor_cm || '0');
  if (!Number.isFinite(h)) return null;
  const faceList = targetMesh.silhouette_faces || targetMesh.faces || [];
  const expected = parseFloat(line.expected_cm || '');
  const select = line.select || (Number.isFinite(expected) ? 'closest' : 'largest');
  const cacheKey = `${h.toFixed(2)}:${select}:${Number.isFinite(expected) ? expected.toFixed(2) : ''}:${faceList.length}:${targetMesh.vertices.length}`;
  if (!targetMesh._sectionInfoCache) targetMesh._sectionInfoCache = new Map();
  if (targetMesh._sectionInfoCache.has(cacheKey)) return targetMesh._sectionInfoCache.get(cacheKey);
  const y = sectionPlaneY(targetMesh, line);
  const segments = [];
  for (const face of faceList) {
    if (!face || face.length < 3) continue;
    const seg = segmentForTriangleAtY(targetMesh.vertices, face, y);
    if (!seg) continue;
    if (distance2d(seg[0], seg[1]) > 1e-6) segments.push(seg);
  }
  const paths = buildSectionPaths(segments, 1e-4);
  const selected = selectSectionPath(paths, line);
  const value = selected && Number.isFinite(selected.perimeter) && selected.perimeter > 0 ? {...selected, loop_count:paths.length} : null;
  targetMesh._sectionInfoCache.set(cacheKey, value);
  return value;
}

function sectionPerimeterCm(targetMesh, line) {
  const info = sectionPathInfo(targetMesh, line);
  return info ? info.perimeter : null;
}

function sectionWarnings(targetName, line, index) {
  const targetMesh = meshForLineTarget(targetName);
  if (!targetMesh || !line) return [];
  const info = sectionPathInfo(targetMesh, line);
  const warnings = [];
  if (!info) {
    warnings.push(currentLanguage === 'ja' ? '断面ループが見つかりません' : 'No section loop found');
    return warnings;
  }
  if (!info.closed) warnings.push(currentLanguage === 'ja' ? '断面が閉じていません' : 'Section loop is open');
  if (info.loop_count > 3) warnings.push(currentLanguage === 'ja' ? `ループが ${info.loop_count} 個あります（腕や髪を拾っているかも）` : `${info.loop_count} loops found; check arms/hair`);
  const expected = parseFloat(line.expected_cm || '');
  if (Number.isFinite(expected) && expected > 0) {
    const diff = Math.abs(info.perimeter - expected);
    if (diff > Math.max(3, expected * 0.08)) warnings.push(currentLanguage === 'ja' ? `想定値と ${diff.toFixed(1)} cm 差があります` : `${diff.toFixed(1)} cm from expected`);
  }
  const set = linesForLineTarget(targetName);
  const prev = index > 0 ? sectionPerimeterCm(targetMesh, set[index - 1]) : null;
  if (prev && info.perimeter && (info.perimeter > prev * 1.55 || info.perimeter < prev * 0.55)) {
    warnings.push(currentLanguage === 'ja' ? '前の断面から周囲長が大きく変化しています' : 'Large jump from previous section');
  }
  return warnings;
}

function sectionValueText(targetName, line) {
  const targetMesh = meshForLineTarget(targetName);
  const perimeter = sectionPerimeterCm(targetMesh, line);
  if (perimeter == null) return '--';
  const displayed = perimeter * displayScaleForTarget(targetName);
  return `${displayed.toFixed(2)} cm`;
}

function zeroOffset() {
  return {x:0, y:0, z:0};
}

function normalizeOffset(offset) {
  if (!offset || typeof offset === 'number') return {x:0, y:0, z:Number(offset) || 0};
  return {x:Number(offset.x) || 0, y:Number(offset.y) || 0, z:Number(offset.z) || 0};
}

function currentOffset() {
  const offsets = displayOffsets();
  return activeLineTarget === 'compare' ? offsets.compare : offsets.primary;
}

function lineRatioForMesh(line, targetMesh) {
  const h = parseFloat(line.height_from_floor_cm || '');
  if (!mesh || !Number.isFinite(h) || !targetMesh || !mesh.height_cm) return null;
  return h / mesh.height_cm;
}

function cloneLinesForMesh(sourceLines, sourceMesh, targetMesh) {
  if (!sourceLines.length || !sourceMesh || !targetMesh) return [];
  return sourceLines.map(line => {
    const out = {...line};
    const h = parseFloat(line.height_from_floor_cm || '');
    const ratio = Number.isFinite(h) && sourceMesh.height_cm ? h / sourceMesh.height_cm : parseFloat(line.height_ratio || '0.6');
    out.height_from_floor_cm = (targetMesh.height_cm * ratio).toFixed(2);
    out.height_ratio = ratio.toFixed(6);
    return out;
  });
}

function updateLineTargetTabs() {
  for (const button of lineTargetButtons) {
    const isCompare = button.dataset.target === 'compare';
    button.textContent = isCompare ? textFor('compareModel') : textFor('primaryModel');
    button.disabled = isCompare && !compareMesh;
    button.classList.toggle('active', button.dataset.target === activeLineTarget);
  }
}

function fileBaseName(fileId, pathId, fallback) {
  const file = document.getElementById(fileId).files[0];
  const raw = file ? file.name : (document.getElementById(pathId).value || fallback);
  const name = String(raw).split(/[\\/]/).pop().replace(/\.[^.]+$/, '');
  return name || fallback;
}

function importedFileDisplayName(fileId, pathId, loaded) {
  if (!loaded) return '--';
  const file = document.getElementById(fileId).files[0];
  const raw = file ? file.name : document.getElementById(pathId).value;
  const name = String(raw || '').split(/[\\/]/).pop();
  return name || '--';
}

function updateImportedFileNames() {
  const primaryLoaded = !!mesh;
  const compareLoaded = !!compareMesh;
  const primaryName = importedFileDisplayName('meshFile', 'meshPath', primaryLoaded);
  const compareName = importedFileDisplayName('compareMeshFile', 'compareMeshPath', compareLoaded);
  const primaryLabel = textFor('primaryModel') + ': ' + primaryName;
  const compareLabel = textFor('compareModel') + ': ' + compareName;
  const primaryImportChip = document.querySelector('.import-chip[for="meshFile"]');
  const compareImportChip = document.querySelector('.import-chip[for="compareMeshFile"]');
  if (primaryFileNameEl) {
    primaryFileNameEl.textContent = primaryLabel;
    primaryFileNameEl.title = primaryLabel;
    primaryFileNameEl.classList.toggle('loaded', primaryLoaded);
  }
  if (compareFileNameEl) {
    compareFileNameEl.textContent = compareLabel;
    compareFileNameEl.title = compareLabel;
    compareFileNameEl.classList.toggle('loaded', compareLoaded);
  }
  if (primaryImportChip) primaryImportChip.classList.toggle('loaded', primaryLoaded);
  if (compareImportChip) compareImportChip.classList.toggle('loaded', compareLoaded);
}
function lineHeightByKeywords(keywords, fallback, targetLines = lines) {
  const found = targetLines.find(line => {
    const text = String((line.name || '') + ' ' + (line.label || '')).toLowerCase();
    return keywords.some(key => text.includes(key));
  });
  if (!found) return fallback;
  const h = parseFloat(found.height_from_floor_cm || '');
  return Number.isFinite(h) ? h : fallback;
}

function percentile(values, ratio) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a,b) => a-b);
  const index = Math.max(0, Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio)));
  return sorted[index];
}

function centralHalfWidthAtHeight(targetMesh, height, band = 2.4) {
  if (!targetMesh) return 0;
  const b = targetMesh.bounds;
  const centerX = (b.min[0] + b.max[0]) / 2;
  const values = [];
  for (const v of targetMesh.vertices) {
    const floorH = v[1] - b.min[1];
    if (Math.abs(floorH - height) <= band) values.push(Math.abs(v[0] - centerX));
  }
  if (values.length < 12) return Math.max(3, (b.max[0] - b.min[0]) * 0.10);
  const p55 = percentile(values, 0.55);
  const p75 = percentile(values, 0.75);
  const p90 = percentile(values, 0.90);
  // Use a conservative central body width so arm geometry is excluded from torso masks.
  return Math.max(3, Math.min(p75, p55 + 7.0, p90 * 0.78));
}

function estimateTorsoHalfWidth(targetMesh = mesh, targetLines = lines) {
  if (!targetMesh) return 0;
  const b = targetMesh.bounds;
  const sampleHeights = [
    lineHeightByKeywords(['bust', '\u30d0\u30b9\u30c8'], targetMesh.height_cm * 0.72, targetLines),
    lineHeightByKeywords(['underbust', '\u30a2\u30f3\u30c0\u30fc'], targetMesh.height_cm * 0.68, targetLines),
    lineHeightByKeywords(['waist', '\u30a6\u30a8\u30b9\u30c8'], targetMesh.height_cm * 0.62, targetLines),
    lineHeightByKeywords(['upperhip', '\u30d2\u30c3\u30d7\u4e0a'], targetMesh.height_cm * 0.56, targetLines),
    lineHeightByKeywords(['hip', '\u30d2\u30c3\u30d7'], targetMesh.height_cm * 0.50, targetLines),
  ];
  const halfWidths = sampleHeights.map(h => centralHalfWidthAtHeight(targetMesh, h)).filter(v => v > 0);
  return Math.max(...halfWidths, (b.max[0] - b.min[0]) * 0.12);
}

function armCutLimitAtHeight(targetMesh, height, shoulderH, waistH, hipH) {
  const core = centralHalfWidthAtHeight(targetMesh, height, 2.8);
  const nearShoulder = Math.max(0, Math.min(1, (height - waistH) / Math.max(1, shoulderH - waistH)));
  const shoulderAllowance = 1.8 + nearShoulder * 3.0;
  const lowerArmAllowance = height < waistH ? 0.8 : 0;
  return core + shoulderAllowance + lowerArmAllowance;
}

function buildArmMaskForMesh(targetMesh, targetLines) {
  if (!targetMesh) return null;
  const b = targetMesh.bounds;
  const centerX = (b.min[0] + b.max[0]) / 2;
  const shoulderH = lineHeightByKeywords(['shoulder', '\u80a9'], targetMesh.height_cm * 0.76, targetLines);
  const bustH = lineHeightByKeywords(['bust', '\u30d0\u30b9\u30c8'], targetMesh.height_cm * 0.72, targetLines);
  const waistH = lineHeightByKeywords(['waist', '\u30a6\u30a8\u30b9\u30c8'], targetMesh.height_cm * 0.62, targetLines);
  const upperHipH = lineHeightByKeywords(['upperhip', '\u30d2\u30c3\u30d7\u4e0a'], targetMesh.height_cm * 0.56, targetLines);
  const hipH = lineHeightByKeywords(['hip', '\u30d2\u30c3\u30d7'], targetMesh.height_cm * 0.50, targetLines);
  const upperH = Math.max(shoulderH, bustH) + 8.0;
  // Limit arm masking to the upper torso band; keep hip and lower-body sections intact.
  const lowerH = Math.max(0, Math.max(upperHipH + 2.0, waistH - targetMesh.height_cm * 0.08));
  const limitCache = new Map();
  const limitForHeight = (h) => {
    const key = Math.round(h * 2) / 2;
    if (!limitCache.has(key)) limitCache.set(key, armCutLimitAtHeight(targetMesh, key, shoulderH, waistH, hipH));
    return limitCache.get(key);
  };
  const buildMask = (faceList) => faceList.map(face => {
    const verts = face.map(i => targetMesh.vertices[i]);
    const cy = (verts[0][1] + verts[1][1] + verts[2][1]) / 3;
    const h = cy - b.min[1];
    if (h > upperH || h < lowerH) return false;
    const dxValues = verts.map(v => Math.abs(v[0] - centerX));
    const dxAvg = dxValues.reduce((sum, v) => sum + v, 0) / dxValues.length;
    const dxMax = Math.max(...dxValues);
    const limit = limitForHeight(h);
    const lowerBodyArmZone = h < waistH + 6.0;
    if (lowerBodyArmZone) return dxAvg > limit - 1.4 || dxMax > limit + 0.6;
    return dxAvg > limit - 1.0 || dxMax > limit + 2.0;
  });

  const maskByFaces = new Map();
  maskByFaces.set(targetMesh.faces, buildMask(targetMesh.faces));
  if (targetMesh.silhouette_faces && targetMesh.silhouette_faces !== targetMesh.faces) {
    maskByFaces.set(targetMesh.silhouette_faces, buildMask(targetMesh.silhouette_faces));
  }
  return maskByFaces;
}

function rebuildArmMask() {
  armMaskCache = null;
  if (!mesh) return;
  armMaskCache = new Map();
  armMaskCache.set(mesh, buildArmMaskForMesh(mesh, lines));
  if (compareMesh) {
    const compareSet = compareLines.length ? compareLines : cloneLinesForMesh(lines, mesh, compareMesh);
    armMaskCache.set(compareMesh, buildArmMaskForMesh(compareMesh, compareSet));
  }
  geometryDirty = true;
}

async function loadLines() {
  const file = document.getElementById('linesFile').files[0];
  let rows;
  if (file) {
    const text = await file.text();
    const parsed = file.name.endsWith('.json') ? JSON.parse(text) : parseCsv(text);
    rows = parsed.sections || parsed;
  } else {
    const path = encodeURIComponent(document.getElementById('linesPath').value);
    const res = await fetch('/api/lines?path=' + path);
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error);
    rows = payload.lines;
  }
  loadedLineRows = rows.map(row => ({...row}));
  lines = normalizeLines(loadedLineRows, mesh);
  compareLines = compareMesh ? normalizeLines(loadedLineRows, compareMesh) : [];
  activeIndex = Math.min(activeIndex, Math.max(currentLines().length - 1, 0));
  updateLineTargetTabs();
  renderLineList();
  if (hideArmsInput.checked) rebuildArmMask();
}

function triangulateClient(indices) {
  const faces = [];
  if (indices.length < 3) return faces;
  for (let i = 1; i < indices.length - 1; i++) faces.push([indices[0], indices[i], indices[i + 1]]);
  return faces;
}

function meshPayloadFromGeometry(vertices, faces, loader) {
  if (!vertices.length || !faces.length) throw new Error(textFor('meshNotFound'));
  const xs = vertices.map(v=>v[0]), ys = vertices.map(v=>v[1]), zs = vertices.map(v=>v[2]);
  const maxPreviewFaces = 2500;
  const stride = Math.max(1, Math.ceil(faces.length / maxPreviewFaces));
  return {vertices, faces: faces.filter((_,i)=>i%stride===0), silhouette_faces: faces, source_face_count: faces.length, face_stride: stride, loader, bounds:{min:[Math.min(...xs),Math.min(...ys),Math.min(...zs)], max:[Math.max(...xs),Math.max(...ys),Math.max(...zs)]}, height_cm: Math.max(...ys)-Math.min(...ys)};
}

function parseObjClient(text) {
  const vertices = [], faces = [];
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith('v ')) {
      const p = line.trim().split(/\s+/);
      vertices.push([parseFloat(p[1]), parseFloat(p[2]), parseFloat(p[3])]);
    } else if (line.startsWith('f ')) {
      const idx = [];
      for (const item of line.trim().split(/\s+/).slice(1)) {
        let raw = parseInt(item.split('/')[0], 10);
        if (raw < 0) raw = vertices.length + raw + 1;
        idx.push(raw - 1);
      }
      faces.push(...triangulateClient(idx));
    }
  }
  return meshPayloadFromGeometry(vertices, faces, 'obj');
}

function parseAsciiFbxClient(text) {
  if (text.slice(0, 256).includes('Kaydara FBX Binary')) {
    throw new Error(textFor('fbxBinaryUnsupported'));
  }
  const vertexMatch = text.match(/Vertices:\s*\*\d+\s*\{\s*a:\s*([\s\S]*?)\}/);
  const polygonMatch = text.match(/PolygonVertexIndex:\s*\*\d+\s*\{\s*a:\s*([\s\S]*?)\}/);
  if (!vertexMatch || !polygonMatch) throw new Error(textFor('fbxArraysNotFound'));
  const values = vertexMatch[1].match(/[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?/g)?.map(Number) || [];
  if (values.length % 3) throw new Error(textFor('fbxVerticesInvalid'));
  const vertices = [];
  for (let i = 0; i < values.length; i += 3) vertices.push([values[i], values[i + 1], values[i + 2]]);
  const polygonValues = polygonMatch[1].match(/-?\d+/g)?.map(Number) || [];
  const faces = [];
  let current = [];
  for (const value of polygonValues) {
    if (value < 0) {
      current.push(-value - 1);
      faces.push(...triangulateClient(current));
      current = [];
    } else {
      current.push(value);
    }
  }
  if (current.length) faces.push(...triangulateClient(current));
  return meshPayloadFromGeometry(vertices, faces, 'ascii_fbx');
}

function parseMeshClient(text, filename) {
  if (!filename.toLowerCase().endsWith('.obj')) throw new Error(textFor('objOnlyError'));
  return parseObjClient(text);
}

async function loadMeshFromInputs(fileId, pathId) {
  const file = document.getElementById(fileId).files[0];
  if (file) return parseMeshClient(await file.text(), file.name);
  const pathValue = document.getElementById(pathId).value.trim();
  if (!pathValue) throw new Error(textFor('fileRequired'));
  const path = encodeURIComponent(pathValue);
  const res = await fetch('/api/mesh?path=' + path);
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error);
  return payload;
}

function modelDiagnosticTexts(targetMesh) {
  if (!targetMesh || !targetMesh.bounds) return [];
  const b = targetMesh.bounds;
  const width = b.max[0] - b.min[0];
  const height = targetMesh.height_cm || (b.max[1] - b.min[1]);
  const depth = b.max[2] - b.min[2];
  const centerX = (b.min[0] + b.max[0]) / 2;
  const centerZ = (b.min[2] + b.max[2]) / 2;
  const sourceFaces = targetMesh.source_face_count || (targetMesh.faces && targetMesh.faces.length) || 0;
  const warnings = [];
  if (sourceFaces > 300000) warnings.push({level:'danger', text:currentLanguage === 'ja' ? '面数が多いので表示が重くなる可能性があります' : 'Very dense mesh; display may be slow'});
  else if (sourceFaces > 150000) warnings.push({level:'warn', text:currentLanguage === 'ja' ? 'やや重いOBJです' : 'Dense OBJ'});
  if (height < 80 || height > 240) warnings.push({level:'warn', text:currentLanguage === 'ja' ? `身長が通常範囲から外れています: ${height.toFixed(1)} cm` : `Height looks unusual: ${height.toFixed(1)} cm`});
  if (Math.abs(centerX) > Math.max(1, Math.abs(width) * 0.03)) warnings.push({level:'warn', text:currentLanguage === 'ja' ? `左右中心がX=0から ${centerX.toFixed(1)} cm ずれています` : `X center is offset by ${centerX.toFixed(1)} cm`});
  if (Math.abs(b.min[1]) > 0.5) warnings.push({level:'warn', text:currentLanguage === 'ja' ? `床位置がY=0から ${b.min[1].toFixed(1)} cm ずれています` : `Floor Y is offset by ${b.min[1].toFixed(1)} cm`});
  if (Math.abs(centerZ) > Math.max(1, Math.abs(depth) * 0.08)) warnings.push({level:'warn', text:currentLanguage === 'ja' ? `前後中心がZ=0から ${centerZ.toFixed(1)} cm ずれています` : `Z center is offset by ${centerZ.toFixed(1)} cm`});
  return warnings;
}

function updateModelDiagnostics() {
  if (!modelDiagnostics) return;
  const items = [];
  const panelTitle = currentLanguage === 'ja' ? '\u30e2\u30c7\u30eb\u8a3a\u65ad' : 'Model diagnostics';
  const panelHelp = currentLanguage === 'ja' ? '\u9762\u6570\u3001\u8eab\u9577\u30b9\u30b1\u30fc\u30eb\u3001X/Z\u4e2d\u5fc3\u305a\u308c\u3001\u5e8a\u4f4d\u7f6e\u306e\u305a\u308c\u3092\u78ba\u8a8d\u3057\u307e\u3059\u3002' : 'Checks face count, body scale, X/Z center offset, and floor position.';
  const emptyText = currentLanguage === 'ja' ? 'OBJ\u3092\u8aad\u307f\u8fbc\u3080\u3068\u3001\u3053\u3053\u306b\u8a3a\u65ad\u7d50\u679c\u3092\u8868\u793a\u3057\u307e\u3059\u3002' : 'Load an OBJ to show diagnostic results here.';
  const addCard = (label, targetMesh) => {
    if (!targetMesh) return;
    const warnings = modelDiagnosticTexts(targetMesh);
    const level = warnings.some(item => item.level === 'danger') ? 'danger' : (warnings.length ? 'warn' : 'ok');
    const stateText = level === 'ok' ? (currentLanguage === 'ja' ? '\u8a3a\u65adOK' : 'OK') : (currentLanguage === 'ja' ? '\u8981\u78ba\u8a8d' : 'Check');
    const noIssueText = currentLanguage === 'ja' ? '\u7279\u306b\u6c17\u306b\u306a\u308b\u70b9\u306f\u3042\u308a\u307e\u305b\u3093' : 'No obvious issues';
    const linesHtml = warnings.length ? warnings.map(item => `<li>${escapeHtml(item.text)}</li>`).join('') : `<li>${noIssueText}</li>`;
    items.push(`<div class="diagnostic-card ${level === 'ok' ? '' : level}"><div class="diagnostic-title"><span>${escapeHtml(label)}</span><span class="diagnostic-state">${stateText}</span></div><ul class="diagnostic-list">${linesHtml}</ul></div>`);
  };
  addCard(textFor('primaryModel'), mesh);
  addCard(textFor('compareModel'), compareMesh);
  modelDiagnostics.innerHTML = `<div class="diagnostic-panel-title"><span>${escapeHtml(panelTitle)}</span></div><div class="diagnostic-empty">${escapeHtml(panelHelp)}</div>` + (items.length ? items.join('') : `<div class="diagnostic-empty">${escapeHtml(emptyText)}</div>`);
}

function formatMeshSummary(label, targetMesh, targetName = 'primary') {
  if (!targetMesh) return '';
  const sourceFaces = targetMesh.source_face_count || targetMesh.faces.length;
  const scale = displayScaleForTarget(targetName);
  const displayHeight = targetMesh.height_cm * scale;
  const heightText = Math.abs(scale - 1) > 0.001
    ? textFor('meshHeight') + ' ' + targetMesh.height_cm.toFixed(2) + ' cm / ' + (currentLanguage === 'ja' ? '\u8868\u793a' : 'display') + ' ' + displayHeight.toFixed(1) + ' cm (' + Math.round(scale * 100) + '%)'
    : textFor('meshHeight') + ' ' + targetMesh.height_cm.toFixed(2) + ' cm';
  return '<div class="mesh-count">' + escapeHtml(label) + ': ' + textFor('meshVertices') + ' ' + targetMesh.vertices.length.toLocaleString() + ' / ' + textFor('meshFaces') + ' ' + sourceFaces.toLocaleString() + ' / ' + textFor('meshShown') + ' ' + targetMesh.faces.length.toLocaleString() + '</div><div>' + heightText + '</div>';
}

function updateMeshMeta() {
  updateImportedFileNames();
  const parts = [];
  if (mesh) parts.push(formatMeshSummary(textFor('primaryModel'), mesh, 'primary'));
  if (compareMesh) parts.push(formatMeshSummary(textFor('compareModel'), compareMesh, 'compare'));
  meshMeta.innerHTML = parts.join('<hr style="border:0;border-top:1px solid #eee6dc;margin:8px 0;">');
  updateModelDiagnostics();
}

function meshWidthX(targetMesh) {
  return targetMesh ? Math.max(1, targetMesh.bounds.max[0] - targetMesh.bounds.min[0]) : 1;
}

function meshWidthZ(targetMesh) {
  return targetMesh ? Math.max(1, targetMesh.bounds.max[2] - targetMesh.bounds.min[2]) : 1;
}

function displayOffsets() {
  const base = {primary:zeroOffset(), compare:zeroOffset()};
  const includeHiddenPair = forceSeparateOffsetsForOutline;
  if (!mesh || !compareMesh) return base;
  if (!includeHiddenPair && (!primaryVisible || !compareVisible)) return base;
  if (compareLayoutInput && compareLayoutInput.value === 'overlay') return base;
  const {right} = currentViewBasis();
  const rightX = Number.isFinite(right.x) ? right.x : 1;
  const rightZ = Number.isFinite(right.z) ? right.z : 0;
  const primaryBounds = scaledBoundsForTarget(mesh, 'primary');
  const compareBounds = scaledBoundsForTarget(compareMesh, 'compare');
  const widthA = Math.max(1, primaryBounds.max[0] - primaryBounds.min[0]) * Math.abs(rightX) + Math.max(1, primaryBounds.max[2] - primaryBounds.min[2]) * Math.abs(rightZ);
  const widthB = Math.max(1, compareBounds.max[0] - compareBounds.min[0]) * Math.abs(rightX) + Math.max(1, compareBounds.max[2] - compareBounds.min[2]) * Math.abs(rightZ);
  const heightA = primaryBounds.max[1] - primaryBounds.min[1];
  const heightB = compareBounds.max[1] - compareBounds.min[1];
  const bodyScale = Math.max(heightA, heightB);
  const visualWidth = Math.max(widthA, widthB);
  const gap = Math.max(bodyScale * 0.42, visualWidth * 0.56) + 10;
  let primary = {x:rightX * gap / 2, y:0, z:rightZ * gap / 2};
  let compare = {x:-rightX * gap / 2, y:0, z:-rightZ * gap / 2};
  if (camera && canvas.width) {
    const primaryCenter = projectPointForTarget([
      (mesh.bounds.min[0] + mesh.bounds.max[0]) / 2,
      (mesh.bounds.min[1] + mesh.bounds.max[1]) / 2,
      (mesh.bounds.min[2] + mesh.bounds.max[2]) / 2,
    ], mesh, 'primary', primary);
    const compareCenter = projectPointForTarget([
      (compareMesh.bounds.min[0] + compareMesh.bounds.max[0]) / 2,
      (compareMesh.bounds.min[1] + compareMesh.bounds.max[1]) / 2,
      (compareMesh.bounds.min[2] + compareMesh.bounds.max[2]) / 2,
    ], compareMesh, 'compare', compare);
    if (Number.isFinite(primaryCenter[0]) && Number.isFinite(compareCenter[0]) && primaryCenter[0] < compareCenter[0]) {
      primary = {x:-rightX * gap / 2, y:0, z:-rightZ * gap / 2};
      compare = {x:rightX * gap / 2, y:0, z:rightZ * gap / 2};
    }
  }
  return {primary, compare};
}

function boundsWithOffset(targetMesh, offset = zeroOffset(), targetName = 'primary') {
  return scaledBoundsForTarget(targetMesh, targetName, offset);
}

function combinedDisplayBounds() {
  if (!mesh) return null;
  const offsets = displayOffsets();
  const bounds = [];
  const includeHiddenPair = !!(forceSeparateOffsetsForOutline && mesh && compareMesh);
  if ((primaryVisible || includeHiddenPair) && mesh) bounds.push(boundsWithOffset(mesh, offsets.primary, 'primary'));
  if ((compareVisible || includeHiddenPair) && compareMesh) bounds.push(boundsWithOffset(compareMesh, offsets.compare, 'compare'));
  if (!bounds.length) bounds.push(boundsWithOffset(mesh, zeroOffset(), 'primary'));
  return {
    min:[
      Math.min(...bounds.map(b => b.min[0])),
      Math.min(...bounds.map(b => b.min[1])),
      Math.min(...bounds.map(b => b.min[2])),
    ],
    max:[
      Math.max(...bounds.map(b => b.max[0])),
      Math.max(...bounds.map(b => b.max[1])),
      Math.max(...bounds.map(b => b.max[2])),
    ],
  };
}
async function loadModel() {
  statusEl.textContent = '';
  mesh = await loadMeshFromInputs('meshFile', 'meshPath');
  primaryDisplayScale = 1;
  compareDisplayScale = 1;
  primaryVisible = true;
  showPrimaryModelInput.checked = true;
  armMaskCache = null;
  screenMasks = screenMasks.filter(mask => mask.targetName !== 'primary');
  geometryDirty = true;
  yaw = Math.PI / 2;
  pitch = 0;
  orthographic = true;
  panX = 0;
  panY = 0;
  zoom = 1.0;
  syncYawSlider();
  lines = [];
  compareLines = [];
  activeLineTarget = 'primary';
  lineList.innerHTML = '';
  updateLineTargetTabs();
  updateMeshMeta();
  updateModelControls();
  statusEl.textContent = '';
  draw();
  try {
    await loadLines();
    if (hideArmsInput.checked) rebuildArmMask();
    statusEl.textContent = '';
  } catch (err) {
    statusEl.textContent = textFor('modelLoadedLineError') + err.message;
  }
  clearUndoHistory();
  draw();
}

async function loadCompareModel() {
  if (!mesh) throw new Error(textFor('mainFirst'));
  statusEl.textContent = '';
  compareMesh = await loadMeshFromInputs('compareMeshFile', 'compareMeshPath');
  compareDisplayScale = 1;
  compareVisible = true;
  showCompareModelInput.checked = true;
  armMaskCache = null;
  screenMasks = screenMasks.filter(mask => mask.targetName !== 'compare');
  compareLines = loadedLineRows.length ? normalizeLines(loadedLineRows, compareMesh) : cloneLinesForMesh(lines, mesh, compareMesh);
  activeLineTarget = 'compare';
  activeIndex = Math.min(activeIndex, Math.max(compareLines.length - 1, 0));
  geometryDirty = true;
  updateLineTargetTabs();
  renderLineList();
  updateMeshMeta();
  updateModelControls();
  statusEl.textContent = '';
  clearUndoHistory();
  draw();
}

function clearThreeObject() {
  if (meshObject && scene) {
    scene.remove(meshObject);
    disposeObject(meshObject);
    meshObject = null;
  }
  clearFloorGrid();
  geometryDirty = true;
}

function deleteCompareModel() {
  if (compareMesh) pushHistory();
  compareMesh = null;
  compareDisplayScale = 1;
  compareLines = [];
  compareVisible = false;
  if (activeLineTarget === 'compare') activeLineTarget = 'primary';
  armMaskCache = null;
  screenMasks = screenMasks.filter(mask => mask.targetName !== 'compare');
  clearThreeObject();
  updateLineTargetTabs();
  renderLineList();
  updateMeshMeta();
  updateModelControls();
  statusEl.textContent = '';
  draw();
}

function deletePrimaryModel() {
  if (mesh || compareMesh) pushHistory();
  mesh = null;
  compareMesh = null;
  primaryDisplayScale = 1;
  compareDisplayScale = 1;
  lines = [];
  compareLines = [];
  loadedLineRows = [];
  primaryVisible = false;
  compareVisible = false;
  activeLineTarget = 'primary';
  activeIndex = 0;
  armMaskCache = null;
  screenMasks = [];
  doodleShapes = [];
  doodleDraft = null;
  setDoodleMode('none');
  clearThreeObject();
  updateLineTargetTabs();
  renderLineList();
  updateMeshMeta();
  updateModelControls();
  resultStatus.textContent = '';
  outputLinks.innerHTML = '';
  outputImage.style.display = 'none';
  resultTable.innerHTML = '';
  statusEl.textContent = '';
  draw();
}
function playBrandQuack() {
  if (!duckQuackAudio) return;
  duckQuackAudio.pause();
  duckQuackAudio.currentTime = 0;
  duckQuackAudio.play().catch(() => {
    statusEl.textContent = textFor('duckMuted');
  });
}

function resetUiControlsToDefaults() {
  if (primarySilhouetteColorInput) primarySilhouetteColorInput.value = '#1f1f1f';
  if (compareSilhouetteColorInput) compareSilhouetteColorInput.value = '#8c939b';
  if (primaryModelOpacityInput) primaryModelOpacityInput.value = '100';
  if (compareModelOpacityInput) compareModelOpacityInput.value = '100';
  updateModelOpacityLabels();
  if (viewerBgColorInput) viewerBgColorInput.value = '#ffffff';
  if (compareLayoutInput) compareLayoutInput.value = 'separate';
  if (renderModeInput) renderModeInput.value = 'silhouette';
  if (lineGuideOnlyInput) lineGuideOnlyInput.checked = true;
  if (showFloorGridInput) showFloorGridInput.checked = true;
  if (showCenterLinesInput) showCenterLinesInput.checked = true;
  if (maskDrawModeInput) maskDrawModeInput.checked = false;
  if (maskShapeModeInput) maskShapeModeInput.value = 'rect';
  if (pickSectionHeightInput) pickSectionHeightInput.checked = false;
  if (hideArmsInput) hideArmsInput.checked = false;
  if (invertPitchInput) invertPitchInput.checked = false;
  if (invertYawInput) invertYawInput.checked = false;

  for (const id of ['meshFile', 'compareMeshFile', 'linesFile', 'underlayImageFile']) {
    const input = document.getElementById(id);
    if (input) input.value = '';
  }

  underlay = { img:null, src:null, x:0, y:0, scale:1, rotation:0 };
  underlayMoveMode = false;
  screenMasks = [];
  doodleShapes = [];
  doodleDraft = null;
  setDoodleMode('none');
  maskDraft = null;
  suppressFloorGrid = false;
  geometryDirty = true;

  if (silhouetteExportUrl) {
    URL.revokeObjectURL(silhouetteExportUrl);
    silhouetteExportUrl = null;
  }
  updateUnderlayMoveButton();
  applyViewerBackground();
}

function resetFromBrandDuck() {
  playBrandQuack();
  pushHistory();
  mesh = null;
  compareMesh = null;
  primaryDisplayScale = 1;
  compareDisplayScale = 1;
  lines = [];
  compareLines = [];
  loadedLineRows = [];
  primaryVisible = true;
  compareVisible = true;
  activeLineTarget = 'primary';
  activeIndex = 0;
  yaw = Math.PI / 2;
  pitch = 0;
  orthographic = true;
  panX = 0;
  panY = 0;
  panPreviewX = 0;
  panPreviewY = 0;
  zoom = 1.0;
  resetUiControlsToDefaults();
  armMaskCache = null;
  heightBar = null;
  clearPanPreviewTransform();
  clearThreeObject();
  applyViewerBackground();
  syncYawSlider();
  updateLineTargetTabs();
  renderLineList();
  updateMeshMeta();
  updateModelControls();
  isoGuideTitle.textContent = textFor('isoDefaultTitle');
  isoGuideText.textContent = textFor('isoDefaultText');
  resultStatus.textContent = '';
  outputLinks.innerHTML = '';
  outputImage.style.display = 'none';
  outputImage.removeAttribute('src');
  resultTable.innerHTML = '';
  statusEl.textContent = '';
  draw();
}
function renderLineList() {
  updateLineTargetTabs();
  if (activeLineTarget === 'compare' && !compareMesh && mesh) activeLineTarget = 'primary';
  if (activeLineTarget === 'primary' && !mesh && compareMesh) activeLineTarget = 'compare';
  lineList.innerHTML = '';
  const hasAnyMesh = !!mesh || !!compareMesh;
  const maxCount = Math.max(lines.length, compareLines.length);
  if (!hasAnyMesh || maxCount === 0) {
    lineList.innerHTML = '<div class="hint">' + escapeHtml(textFor('lineEmpty')) + '</div>';
    return;
  }
  activeIndex = Math.min(activeIndex, Math.max(maxCount - 1, 0));

  const cellHtml = (targetName, line, index) => {
    const targetMesh = meshForLineTarget(targetName);
    const available = !!targetMesh && !!line;
    const active = available && targetName === activeLineTarget && index === activeIndex;
    if (line && line.preview_visible === undefined) line.preview_visible = true;
    const value = available ? sectionValueText(targetName, line) : '--';
    const warnings = available ? sectionWarnings(targetName, line, index) : [];
    const warningTitle = warnings.join(' / ');
    const visible = available && line.preview_visible !== false;
    const checked = visible ? 'checked' : '';
    const hiddenClass = available && !visible ? 'hidden-plane' : '';
    const warningClass = warnings.length ? 'warning' : '';
    const warningMark = warnings.length ? '<span class="section-warning-mark" aria-hidden="true">!</span>' : '';
    const targetLabel = targetName === 'compare' ? textFor('compareModel') : textFor('primaryModel');
    const toggleLabel = targetLabel + ' ' + textFor('show');
    return `
      <div class="section-model-cell ${available ? '' : 'unavailable'} ${active ? 'active' : ''} ${hiddenClass} ${warningClass}" data-target="${targetName}" data-index="${index}">
        <label class="section-model-toggle" title="${escapeHtml(toggleLabel)}">
          <input class="visibility-check" type="checkbox" aria-label="${escapeHtml(toggleLabel)}" data-visible-target="${targetName}" data-visible-index="${index}" ${checked} ${available ? '' : 'disabled'}>
          <span class="sr-only">${escapeHtml(textFor('show'))}</span>
        </label>
        <span class="measure-value ${warningClass}" title="${escapeHtml(warningTitle || textFor('perimeterLabel'))}">${warningMark}${escapeHtml(value)}</span>
      </div>`;
  };

  const columnHeaderHtml = (targetName) => {
    const summary = lineVisibilitySummary(targetName);
    const targetLabel = targetName === 'compare' ? textFor('compareModel') : textFor('primaryModel');
    const stateClass = summary.total === 0 ? '' : (summary.visible === summary.total ? 'all-visible' : (summary.visible > 0 ? 'partial-visible' : 'none-visible'));
    const mark = summary.total === 0 ? '' : (summary.visible === summary.total ? '✓' : (summary.visible > 0 ? '–' : ''));
    const title = targetLabel + ': ' + summary.visible + ' / ' + summary.total;
    return `<button type="button" class="section-column-toggle ${stateClass}" data-toggle-target="${targetName}" ${summary.total === 0 ? 'disabled' : ''} title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">
      <span class="column-toggle-mark" aria-hidden="true">${mark}</span>
      <span class="column-toggle-text">${escapeHtml(targetLabel)}</span>
    </button>`;
  };

  const header = document.createElement('div');
  header.className = 'section-grid-header';
  header.innerHTML = `
    <span>${escapeHtml(textFor('lineName'))}</span>
    <span>${columnHeaderHtml('primary')}</span>
    <span>${columnHeaderHtml('compare')}</span>
  `;
  lineList.appendChild(header);

  for (let i = 0; i < maxCount; i++) {
    const primaryLine = lines[i];
    const compareLine = compareLines[i];
    const baseLine = primaryLine || compareLine;
    const row = document.createElement('div');
    row.className = 'section-row' + (i === activeIndex ? ' active-row' : '');
    row.innerHTML = `
      <div class="section-name-cell">
        <input class="line-color-input" type="color" value="${escapeHtml(baseLine.color || '#888888')}" data-index="${i}" aria-label="${escapeHtml(textFor('lineName'))} ${escapeHtml(baseLine.label || baseLine.name || textFor('genericLine'))} color">
        <input class="line-name-input" type="text" value="${escapeHtml(baseLine.label || baseLine.name || textFor('genericLine'))}" data-index="${i}" aria-label="${escapeHtml(textFor('lineName'))}">
      </div>
      ${cellHtml('primary', primaryLine, i)}
      ${cellHtml('compare', compareLine, i)}
    `;
    lineList.appendChild(row);
  }

  lineList.querySelectorAll('.section-column-toggle').forEach(button => {
    button.addEventListener('click', event => {
      event.stopPropagation();
      if (button.disabled) return;
      toggleLineGuidesForTarget(button.dataset.toggleTarget);
    });
  });

  lineList.querySelectorAll('.section-model-cell').forEach(cell => {
    cell.addEventListener('click', (event) => {
      if (cell.classList.contains('unavailable')) return;
      if (event.target && event.target.classList.contains('visibility-check')) return;
      activeLineTarget = cell.dataset.target;
      activeIndex = parseInt(cell.dataset.index, 10) || 0;
      drawIsoGuide();
      renderLineList();
      draw();
    });
  });

  lineList.querySelectorAll('.line-color-input').forEach(input => {
    input.addEventListener('click', event => event.stopPropagation());
    input.addEventListener('pointerdown', event => event.stopPropagation());
    input.addEventListener('focus', () => beginHistory());
    input.addEventListener('input', event => {
      const index = parseInt(event.target.dataset.index, 10) || 0;
      const nextColor = event.target.value;
      if (lines[index]) lines[index].color = nextColor;
      if (compareLines[index]) compareLines[index].color = nextColor;
      drawIsoGuide();
      requestOverlayDraw();
    });
    input.addEventListener('change', () => {
      commitHistory();
      renderLineList();
      requestOverlayDraw();
    });
  });
  lineList.querySelectorAll('.line-name-input').forEach(input => {
    input.addEventListener('click', event => event.stopPropagation());
    input.addEventListener('pointerdown', event => event.stopPropagation());
    input.addEventListener('keydown', event => event.stopPropagation());
    input.addEventListener('focus', () => beginHistory());
    input.addEventListener('blur', () => commitHistory());
    input.addEventListener('input', event => {
      const index = parseInt(event.target.dataset.index, 10) || 0;
      const nextLabel = event.target.value.trim();
      if (lines[index]) lines[index].label = nextLabel || lines[index].name;
      if (compareLines[index]) compareLines[index].label = nextLabel || compareLines[index].name;
      drawIsoGuide();
      draw();
    });
  });

  lineList.querySelectorAll('.visibility-check').forEach(input => {
    input.addEventListener('click', event => event.stopPropagation());
    input.addEventListener('change', event => {
      pushHistory();
      const targetName = event.target.dataset.visibleTarget;
      const index = parseInt(event.target.dataset.visibleIndex, 10) || 0;
      const set = linesForLineTarget(targetName);
      if (set[index]) set[index].preview_visible = event.target.checked;
      renderLineList();
      draw();
    });
  });
}

function allEditableLineSets() {
  return [lines, compareLines].filter(set => Array.isArray(set));
}


function lineVisibilitySummary(targetName) {
  const set = linesForLineTarget(targetName);
  const total = meshForLineTarget(targetName) ? set.length : 0;
  const visible = total ? set.filter(line => line.preview_visible !== false).length : 0;
  return {total, visible};
}

function setLineGuidesVisibleForTarget(targetName, visible) {
  const set = linesForLineTarget(targetName);
  if (!meshForLineTarget(targetName) || !set.length) return;
  pushHistory();
  for (const line of set) line.preview_visible = visible;
  renderLineList();
  updateUndoButtons();
  updateDoodleButtons();
  draw();
}

function toggleLineGuidesForTarget(targetName) {
  const summary = lineVisibilitySummary(targetName);
  if (!summary.total) return;
  setLineGuidesVisibleForTarget(targetName, summary.visible !== summary.total);
}

function projectPoint(v) {
  if (!camera) return [0, 0, 0];
  const vec = new THREE.Vector3(v[0], v[1], v[2]);
  vec.project(camera);
  return [(vec.x * 0.5 + 0.5) * canvas.width, (-vec.y * 0.5 + 0.5) * canvas.height, vec.z];
}

function projectPointWithOffset(v, offset = zeroOffset()) {
  const o = normalizeOffset(offset);
  return projectPoint([v[0] + o.x, v[1] + o.y, v[2] + o.z]);
}

function project(v) {
  const p = projectPoint(v);
  return [p[0], p[1]];
}

function rotateVector(vec) {
  const [x, y, z] = vec;
  const cosy=Math.cos(yaw), siny=Math.sin(yaw), cosp=Math.cos(pitch), sinp=Math.sin(pitch);
  const x1=x*cosy-z*siny, z1=x*siny+z*cosy;
  const y1=y*cosp-z1*sinp, z2=y*sinp+z1*cosp;
  return [x1, y1, z2];
}

function drawArrow(x0, y0, x1, y1, color) {
  const angle = Math.atan2(y1 - y0, x1 - x0);
  const head = 8;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x1 - head*Math.cos(angle - Math.PI/6), y1 - head*Math.sin(angle - Math.PI/6));
  ctx.lineTo(x1 - head*Math.cos(angle + Math.PI/6), y1 - head*Math.sin(angle + Math.PI/6));
  ctx.closePath(); ctx.fill();
}

function drawRoundedRect(x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  ctx.lineTo(x + rr, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
  ctx.lineTo(x, y + rr);
  ctx.quadraticCurveTo(x, y, x + rr, y);
  ctx.closePath();
}


function updateIsoGuideDefault() {
  isoGuideTitle.textContent = textFor('isoDefaultTitle');
  isoGuideText.textContent = textFor('isoDefaultText');
  if (isoGuideNote) isoGuideNote.textContent = textFor('isoNote');
}

function drawIsoGuide() {
  const editableLines = currentLines();
  if (!mesh) {
    updateIsoGuideDefault();
    return;
  }
  isoGuideTitle.textContent = textFor('isoDefaultTitle');
  if (!editableLines.length) {
    isoGuideText.textContent = currentLanguage === 'en'
      ? 'The model is loaded. Generate or load section lines next.'
      : 'モデルを読み込みました。次に断面ラインを確認してください。';
    if (isoGuideNote) isoGuideNote.textContent = currentLanguage === 'en'
      ? 'Use Top view or Fit view if the body is outside the canvas.'
      : '見切れている時は「表示を合わせる」か「上面図生成」を使います。';
    return;
  }
  const line = editableLines[activeIndex] || editableLines[0];
  const label = line.label || line.name || textFor('genericLine');
  const targetLabel = activeLineTarget === 'compare' ? textFor('compareModel') : textFor('primaryModel');
  isoGuideText.textContent = currentLanguage === 'en'
    ? `${targetLabel}: adjust "${label}" with the side handle or the right-side value.`
    : `${targetLabel}の「${label}」を調整中。左右の点か右側の数値で位置を合わせます。`;
  if (isoGuideNote) {
    isoGuideNote.textContent = compareMesh
      ? (currentLanguage === 'en' ? 'Toggle each row if the guide display gets crowded.' : '線が多い時は右側のチェックで表示を整理できます。')
      : (currentLanguage === 'en' ? 'Add a compare OBJ when you are ready to check differences.' : '比較したい時は、比較OBJを追加できます。');
  }
}function drawAxisGizmo() {
  const origin = {x: canvas.width - 142, y: canvas.height - 72};
  const len = 44;
  const panel = {x: origin.x - 52, y: origin.y - 64, w: 116, h: 104};
  const clampAxisLabel = (value, min, max) => Math.max(min, Math.min(max, value));
  const axes = [
    {name:'X', vec:[1,0,0], color:'#d94848'},
    {name:'Y', vec:[0,1,0], color:'#2f9e44'},
    {name:'Z', vec:[0,0,1], color:'#1971c2'},
  ];
  ctx.save();
  ctx.fillStyle = 'rgba(255,255,255,0.90)';
  ctx.strokeStyle = '#d5dbe3';
  ctx.lineWidth = 1;
  drawRoundedRect(panel.x, panel.y, panel.w, panel.h, 8); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#758196';
  ctx.font = 'bold 10px Meiryo';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText('MESH', panel.x + 12, panel.y + 12);
  ctx.beginPath(); ctx.arc(origin.x, origin.y, 3, 0, Math.PI*2); ctx.fill();
  let depthLabelCount = 0;
  for (const axis of axes) {
    const r = rotateVector(axis.vec);
    const sx = r[0], sy = -r[1];
    const mag = Math.hypot(sx, sy);
    ctx.fillStyle = axis.color;
    ctx.strokeStyle = axis.color;
    if (mag < 0.16) {
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(origin.x, origin.y, 11, 0, Math.PI*2); ctx.stroke();
      if (r[2] >= 0) {
        ctx.beginPath(); ctx.arc(origin.x, origin.y, 4, 0, Math.PI*2); ctx.fill();
      } else {
        ctx.beginPath();
        ctx.moveTo(origin.x - 5, origin.y - 5); ctx.lineTo(origin.x + 5, origin.y + 5);
        ctx.moveTo(origin.x + 5, origin.y - 5); ctx.lineTo(origin.x - 5, origin.y + 5);
        ctx.stroke();
      }
      ctx.font = 'bold 12px Meiryo';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(axis.name, origin.x + 15 + depthLabelCount * 14, origin.y + 15);
      depthLabelCount += 1;
      continue;
    }
    const x1 = origin.x + sx / mag * len;
    const y1 = origin.y + sy / mag * len;
    drawArrow(origin.x, origin.y, x1, y1, axis.color);
    const lx = clampAxisLabel(x1 + sx / mag * 9, panel.x + 16, panel.x + panel.w - 16);
    const ly = clampAxisLabel(y1 + sy / mag * 9, panel.y + 18, panel.y + panel.h - 16);
    ctx.font = 'bold 12px Meiryo';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(axis.name, lx, ly);
  }
  ctx.restore();
}


function drawProjectedSegment(a, b) {
  ctx.beginPath();
  ctx.moveTo(a[0], a[1]);
  ctx.lineTo(b[0], b[1]);
  ctx.stroke();
}

function segmentLength(seg) {
  const dx = seg[1][0] - seg[0][0];
  const dy = seg[1][1] - seg[0][1];
  return Math.hypot(dx, dy);
}

function drawExtendedProjectedSegment(seg, minLength, extraLength, maxLength = Infinity) {
  const [a, b] = seg;
  const dx0 = b[0] - a[0];
  const dy0 = b[1] - a[1];
  const len = Math.hypot(dx0, dy0);
  if (len < 1) return false;
  let target = Math.max(minLength, len + extraLength);
  if (Number.isFinite(maxLength)) target = Math.min(target, Math.max(len, maxLength));
  const dx = dx0 / len;
  const dy = dy0 / len;
  const mx = (a[0] + b[0]) / 2;
  const my = (a[1] + b[1]) / 2;
  ctx.beginPath();
  ctx.moveTo(mx - dx * target / 2, my - dy * target / 2);
  ctx.lineTo(mx + dx * target / 2, my + dy * target / 2);
  ctx.stroke();
  return true;
}

function drawFallbackScreenGuide(center, length) {
  const half = length / 2;
  ctx.beginPath();
  ctx.moveTo(center[0] - half, center[1]);
  ctx.lineTo(center[0] + half, center[1]);
  ctx.stroke();
}

function drawLinePlane(line, targetMesh = mesh, offset = zeroOffset(), targetName = 'primary') {
  if (!targetMesh) return;
  const b = boundsWithOffset(targetMesh, offset, targetName);
  const y = lineWorldY(targetMesh, targetName, line, offset);
  const cx = (b.min[0] + b.max[0]) / 2;
  const cz = (b.min[2] + b.max[2]) / 2;
  const isActive = targetName === activeLineTarget && line === currentLines()[activeIndex];
  const isCompare = targetName === 'compare';
  ctx.save();
  ctx.strokeStyle = line.color || '#d00';
  ctx.lineWidth = isActive ? 4.2 : 2.4;
  ctx.globalAlpha = isCompare ? (isActive ? 0.95 : 0.56) : (isActive ? 1 : 0.78);
  if (isCompare) ctx.setLineDash([7, 5]);

  if (lineGuideOnlyInput.checked) {
    const xPad = (b.max[0] - b.min[0]) * 0.02;
    const zPad = (b.max[2] - b.min[2]) * 0.04;
    const segX = [project([b.min[0] + xPad, y, cz]), project([b.max[0] - xPad, y, cz])];
    const segZ = [project([cx, y, b.min[2] + zPad]), project([cx, y, b.max[2] - zPad])];
    const lenX = segmentLength(segX);
    const lenZ = segmentLength(segZ);
    const mainSeg = lenZ > lenX ? segZ : segX;
    const subSeg = lenZ > lenX ? segX : segZ;
    const mainLen = Math.max(lenX, lenZ);

    const twoModelCompare = mesh && compareMesh && primaryVisible && compareVisible;
    let maxGuideLength = Infinity;
    if (twoModelCompare) {
      const peerMesh = targetName === 'compare' ? mesh : compareMesh;
      const peerOffset = targetName === 'compare' ? displayOffsets().primary : displayOffsets().compare;
      const peerTargetName = targetName === 'compare' ? 'primary' : 'compare';
      const peerBounds = boundsWithOffset(peerMesh, peerOffset, peerTargetName);
      const center = project([cx, y, cz]);
      const peerCenter = project([
        (peerBounds.min[0] + peerBounds.max[0]) / 2,
        y,
        (peerBounds.min[2] + peerBounds.max[2]) / 2,
      ]);
      const dx = mainSeg[1][0] - mainSeg[0][0];
      const dy = mainSeg[1][1] - mainSeg[0][1];
      const segLen = Math.hypot(dx, dy);
      if (segLen > 1) {
        const ux = dx / segLen;
        const uy = dy / segLen;
        const centerDistance = Math.abs((peerCenter[0] - center[0]) * ux + (peerCenter[1] - center[1]) * uy);
        maxGuideLength = Math.max(mainLen, centerDistance - (isActive ? 84 : 70));
      }
    }

    if (mainLen < 18) {
      drawFallbackScreenGuide(project([cx, y, cz]), twoModelCompare ? 120 : (isActive ? 340 : 280));
    } else {
      drawExtendedProjectedSegment(mainSeg, isActive ? 340 : 280, isActive ? 150 : 120, maxGuideLength);
    }

    if (isActive) {
      const subLen = Math.min(lenX, lenZ);
      if (subLen > 22) {
        const oldDash = isCompare ? [7, 5] : [];
        ctx.setLineDash([5, 4]);
        ctx.lineWidth = 2.1;
        ctx.globalAlpha = 0.82;
        drawProjectedSegment(subSeg[0], subSeg[1]);
        ctx.setLineDash(oldDash);
      }
    }
    ctx.restore();
    return;
  }

  const pts = [
    [b.min[0], y, b.min[2]], [b.max[0], y, b.min[2]], [b.max[0], y, b.max[2]], [b.min[0], y, b.max[2]]
  ].map(project);
  ctx.beginPath();
  pts.forEach((p,i) => i ? ctx.lineTo(p[0],p[1]) : ctx.moveTo(p[0],p[1]));
  ctx.closePath();
  ctx.stroke();
  ctx.restore();
}

function screenYToHeightCm(screenY) {
  const targetMesh = currentMesh();
  if (!targetMesh) return null;
  const offset = normalizeOffset(currentOffset());
  const b = boundsWithOffset(targetMesh, offset, activeLineTarget);
  const cx = (b.min[0] + b.max[0]) / 2;
  const cz = (b.min[2] + b.max[2]) / 2;
  const floorY = project([cx, b.min[1], cz])[1];
  const headY = project([cx, b.max[1], cz])[1];
  const denom = floorY - headY;
  if (Math.abs(denom) < 1) return null;
  const ratio = (floorY - screenY) / denom;
  return Math.max(0, Math.min(targetMesh.height_cm, ratio * targetMesh.height_cm));
}

function setActiveSectionHeightFromScreenY(screenY, finalize = false) {
  const editableLines = currentLines();
  if (!editableLines[activeIndex]) return false;
  const h = screenYToHeightCm(screenY);
  if (h === null) return false;
  editableLines[activeIndex].height_from_floor_cm = h.toFixed(2);
  editableLines[activeIndex].height_ratio = '';
  if (finalize) {
    if (hideArmsInput.checked) rebuildArmMask();
    renderLineList();
    draw();
  } else if (dragMode === 'section') {
    requestOverlayDraw();
  } else {
    requestDraw();
  }
  return true;
}

function hitHeightBarPoint(screenX, screenY) {
  if (!heightBar || !heightBar.points || !heightBar.points.length) return null;
  let best = null;
  let bestDistance = Infinity;
  for (const point of heightBar.points) {
    const distance = Math.hypot(screenX - point.x, screenY - point.y);
    if (distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  }
  return bestDistance <= 18 ? best : null;
}

function hitHeightBarTrack(screenX) {
  if (!heightBar) return null;
  const candidates = [];
  if (heightBar.primary) candidates.push(heightBar.primary);
  if (heightBar.compare) candidates.push(heightBar.compare);
  let best = null;
  let bestDistance = Infinity;
  for (const bar of candidates) {
    const distance = Math.abs(screenX - bar.x);
    if (distance < bestDistance) {
      best = bar;
      bestDistance = distance;
    }
  }
  return bestDistance <= 18 ? best : null;
}

function setActiveSectionHeightFromBarY(screenY, finalize = false, targetName = activeLineTarget) {
  const targetMesh = meshForLineTarget(targetName);
  const editableLines = linesForLineTarget(targetName);
  const bar = heightBar && heightBar[targetName];
  if (!targetMesh || !bar || !editableLines[activeIndex]) return false;
  const y = Math.max(bar.top, Math.min(bar.bottom, screenY));
  const h = (bar.bottom - y) / (bar.bottom - bar.top) * targetMesh.height_cm;
  editableLines[activeIndex].height_from_floor_cm = h.toFixed(2);
  editableLines[activeIndex].height_ratio = '';
  if (finalize) {
    if (hideArmsInput.checked) rebuildArmMask();
    renderLineList();
    draw();
  } else if (dragMode === 'heightbar') {
    requestOverlayDraw();
  } else {
    requestDraw();
  }
  return true;
}

function updateViewerCursor(pointerX = null, pointerY = null) {
  if (doodleMode !== 'none') {
    canvas.style.cursor = DOODLE_CURSORS[doodleMode] || 'crosshair';
    return;
  }
  if (underlayMoveMode) {
    const handle = Number.isFinite(pointerX) && Number.isFinite(pointerY) ? hitUnderlayGizmo(pointerX, pointerY) : null;
    canvas.style.cursor = handle ? handle.cursor : 'move';
    return;
  }
  canvas.style.cursor = maskDrawModeInput && maskDrawModeInput.checked ? 'crosshair' : 'grab';
}

function rectFromPoints(x0, y0, x1, y1) {
  const x = Math.min(x0, x1);
  const y = Math.min(y0, y1);
  return {x, y, w: Math.abs(x1 - x0), h: Math.abs(y1 - y0)};
}

function normalizeScreenRect(rect) {
  return {
    x: rect.x / Math.max(1, canvas.width),
    y: rect.y / Math.max(1, canvas.height),
    w: rect.w / Math.max(1, canvas.width),
    h: rect.h / Math.max(1, canvas.height),
  };
}

function denormalizeScreenRect(rect) {
  return {
    x: rect.x * canvas.width,
    y: rect.y * canvas.height,
    w: rect.w * canvas.width,
    h: rect.h * canvas.height,
  };
}
function normalizeScreenPoint(point) {
  return {
    x: point.x / Math.max(1, canvas.width),
    y: point.y / Math.max(1, canvas.height),
  };
}

function denormalizeScreenPoint(point) {
  return {x: point.x * canvas.width, y: point.y * canvas.height};
}

function normalizeScreenPoints(points) {
  return (points || []).map(normalizeScreenPoint);
}

function denormalizeScreenPoints(points) {
  return (points || []).map(denormalizeScreenPoint);
}

function distancePointToSegment(point, a, b) {
  const vx = b.x - a.x;
  const vy = b.y - a.y;
  const len2 = vx * vx + vy * vy;
  if (len2 < 0.0001) return Math.hypot(point.x - a.x, point.y - a.y);
  const t = Math.max(0, Math.min(1, ((point.x - a.x) * vx + (point.y - a.y) * vy) / len2));
  const px = a.x + vx * t;
  const py = a.y + vy * t;
  return Math.hypot(point.x - px, point.y - py);
}

function polygonArea(points) {
  if (!points || points.length < 3) return 0;
  let area = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    area += a.x * b.y - b.x * a.y;
  }
  return area / 2;
}

function maxDraftDeviation(points) {
  if (!points || points.length < 3) return 0;
  const start = points[0];
  const end = points[points.length - 1];
  let maxDistance = 0;
  for (const point of points) maxDistance = Math.max(maxDistance, distancePointToSegment(point, start, end));
  return maxDistance;
}

function boundsForPoints(points) {
  const xs = points.map(point => point.x);
  const ys = points.map(point => point.y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const maxX = Math.max(...xs);
  const maxY = Math.max(...ys);
  return {x:minX, y:minY, w:Math.max(0, maxX - minX), h:Math.max(0, maxY - minY)};
}

function pointInPolygon(point, polygon) {
  if (!polygon || polygon.length < 3) return false;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i];
    const b = polygon[j];
    const crosses = ((a.y > point.y) !== (b.y > point.y)) &&
      (point.x < (b.x - a.x) * (point.y - a.y) / ((b.y - a.y) || 0.000001) + a.x);
    if (crosses) inside = !inside;
  }
  return inside;
}

function currentMaskShapeMode() {
  return maskShapeModeInput && maskShapeModeInput.value === 'lasso' ? 'lasso' : 'rect';
}

function maskShapeFromDraft(draft) {
  const rect = rectFromPoints(draft.x0, draft.y0, draft.x1, draft.y1);
  if (currentMaskShapeMode() !== 'lasso') return {type:'rect', rect};
  const points = draft.points || [];
  return {type:'lasso', points, rect:points.length ? boundsForPoints(points) : rect};
}

function targetMeshByName(targetName) {
  return targetName === 'compare' ? compareMesh : mesh;
}

function offsetForTargetName(targetName) {
  const offsets = displayOffsets();
  return targetName === 'compare' ? offsets.compare : offsets.primary;
}

function targetVisibleByName(targetName) {
  return targetName === 'compare' ? compareVisible : primaryVisible;
}

function ensureMaskVertexSet(mask) {
  if (!mask._vertexSet) mask._vertexSet = new Set(mask.vertices || []);
  return mask._vertexSet;
}

function isFaceMaskedByUserMasks(targetName, face) {
  if (!screenMasks.length) return false;
  for (const mask of screenMasks) {
    if (mask.targetName !== targetName) continue;
    const vertexSet = ensureMaskVertexSet(mask);
    for (const idx of face) {
      if (vertexSet.has(idx)) return true;
    }
  }
  return false;
}

function createMaskForTargetFromShape(targetName, shape) {
  const targetMesh = targetMeshByName(targetName);
  if (!targetMesh || !targetVisibleByName(targetName) || !shape) return null;
  const offset = offsetForTargetName(targetName);
  const rect = shape.rect || {x:0, y:0, w:0, h:0};
  const right = rect.x + rect.w;
  const bottom = rect.y + rect.h;
  const vertices = [];
  for (let i = 0; i < targetMesh.vertices.length; i += 1) {
    const p = projectPointForTarget(targetMesh.vertices[i], targetMesh, targetName, offset);
    if (!Number.isFinite(p[0]) || !Number.isFinite(p[1]) || p[2] < -1.2 || p[2] > 1.2) continue;
    if (p[0] < rect.x || p[0] > right || p[1] < rect.y || p[1] > bottom) continue;
    const inside = shape.type === 'lasso' ? pointInPolygon({x:p[0], y:p[1]}, shape.points) : true;
    if (inside) vertices.push(i);
  }
  if (!vertices.length) return null;
  const mask = {
    targetName,
    vertices,
    type: shape.type || 'rect',
    rect: normalizeScreenRect(rect),
    points: shape.type === 'lasso' ? normalizeScreenPoints(shape.points) : null,
  };
  mask._vertexSet = new Set(vertices);
  return mask;
}

function addUserMasksFromScreenShape(shape) {
  if (!mesh || !shape || !shape.rect || shape.rect.w <= 6 || shape.rect.h <= 6) return 0;
  resizeCanvasesToDisplaySize();
  ensureThree();
  updateThreeCamera();
  const created = [];
  for (const targetName of ['primary', 'compare']) {
    const mask = createMaskForTargetFromShape(targetName, shape);
    if (mask) created.push(mask);
  }
  if (!created.length) return 0;
  screenMasks.push(...created);
  geometryDirty = true;
  return created.length;
}

function addUserMasksFromScreenRect(rect) {
  return addUserMasksFromScreenShape({type:'rect', rect});
}
function projectedMaskRect(mask) {
  const targetMesh = targetMeshByName(mask.targetName);
  if (!targetMesh || !targetVisibleByName(mask.targetName) || !mask.vertices || !mask.vertices.length) {
    return mask.rect ? denormalizeScreenRect(mask.rect) : null;
  }
  const offset = offsetForTargetName(mask.targetName);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, count = 0;
  for (const idx of mask.vertices) {
    const v = targetMesh.vertices[idx];
    if (!v) continue;
    const p = projectPointForTarget(v, targetMesh, mask.targetName, offset);
    if (!Number.isFinite(p[0]) || !Number.isFinite(p[1]) || p[2] < -1.2 || p[2] > 1.2) continue;
    minX = Math.min(minX, p[0]);
    minY = Math.min(minY, p[1]);
    maxX = Math.max(maxX, p[0]);
    maxY = Math.max(maxY, p[1]);
    count += 1;
  }
  if (!count) return mask.rect ? denormalizeScreenRect(mask.rect) : null;
  const pad = 8;
  return {x:minX - pad, y:minY - pad, w:Math.max(10, maxX - minX + pad * 2), h:Math.max(10, maxY - minY + pad * 2)};
}

function drawAppliedMasks() {
  // User masks now hide mesh faces directly, so they persist after rotation.
}

function drawMaskShape(shape, fillStyle, strokeStyle, dashed = false) {
  if (!shape) return;
  ctx.save();
  ctx.fillStyle = fillStyle;
  ctx.strokeStyle = strokeStyle;
  ctx.lineWidth = 2;
  ctx.setLineDash(dashed ? [8, 5] : []);
  if (shape.type === 'lasso' && shape.points && shape.points.length >= 2) {
    ctx.beginPath();
    ctx.moveTo(shape.points[0].x, shape.points[0].y);
    for (const point of shape.points.slice(1)) ctx.lineTo(point.x, point.y);
    if (shape.points.length >= 3) ctx.closePath();
    ctx.fill();
    ctx.stroke();
  } else if (shape.rect) {
    ctx.fillRect(shape.rect.x, shape.rect.y, shape.rect.w, shape.rect.h);
    ctx.strokeRect(shape.rect.x, shape.rect.y, shape.rect.w, shape.rect.h);
  }
  ctx.restore();
}

function drawMaskGuides() {
  const showMaskRects = !showMaskRectsInput || showMaskRectsInput.checked;
  if ((!showMaskRects || !screenMasks.length) && !maskDraft) return;
  if (showMaskRects) {
    for (const mask of screenMasks) {
      const r = projectedMaskRect(mask);
      if (!r) continue;
      drawMaskShape({type:'rect', rect:r}, 'rgba(44, 151, 255, 0.12)', 'rgba(44, 151, 255, 0.78)', true);
    }
  }
  if (maskDraft) {
    const shape = maskShapeFromDraft(maskDraft);
    drawMaskShape(shape, 'rgba(44, 151, 255, 0.22)', '#2f9cff', false);
  }
}
let outlineWorkCanvas = null;
let outlineMaskCanvas = null;
let outlineStrokeCanvas = null;

function ensureOutlineCanvas(refCanvas) {
  if (!outlineWorkCanvas) outlineWorkCanvas = document.createElement('canvas');
  if (!outlineMaskCanvas) outlineMaskCanvas = document.createElement('canvas');
  if (!outlineStrokeCanvas) outlineStrokeCanvas = document.createElement('canvas');
  for (const c of [outlineWorkCanvas, outlineMaskCanvas, outlineStrokeCanvas]) {
    if (c.width !== refCanvas.width || c.height !== refCanvas.height) {
      c.width = refCanvas.width;
      c.height = refCanvas.height;
    }
  }
}

function renderSilhouetteSourceForTarget(targetName) {
  if (!mesh || !camera) return null;
  ensureOutlineCanvas(canvas);
  const savedMode = renderModeInput.value;
  const savedPrimaryVisible = primaryVisible;
  const savedCompareVisible = compareVisible;
  const savedSuppressFloorGrid = suppressFloorGrid;
  const savedForceSeparateOffsetsForOutline = forceSeparateOffsetsForOutline;
  const savedForceOpaqueModelRender = forceOpaqueModelRender;
  try {
    renderModeInput.value = 'silhouette';
    suppressFloorGrid = true;
    forceSeparateOffsetsForOutline = !!(savedPrimaryVisible && savedCompareVisible && mesh && compareMesh);
    forceOpaqueModelRender = true;
    primaryVisible = targetName === 'primary' && !!mesh && savedPrimaryVisible;
    compareVisible = targetName === 'compare' && !!compareMesh && savedCompareVisible;
    if (!primaryVisible && !compareVisible) return null;
    geometryDirty = true;
    renderThreeScene();
    const workCtx = outlineWorkCanvas.getContext('2d', {willReadFrequently: true});
    workCtx.setTransform(1, 0, 0, 1, 0, 0);
    workCtx.clearRect(0, 0, outlineWorkCanvas.width, outlineWorkCanvas.height);
    workCtx.imageSmoothingEnabled = true;
    workCtx.imageSmoothingQuality = 'high';
    workCtx.drawImage(glCanvas, 0, 0, outlineWorkCanvas.width, outlineWorkCanvas.height);
    return outlineWorkCanvas;
  } finally {
    renderModeInput.value = savedMode;
    primaryVisible = savedPrimaryVisible;
    compareVisible = savedCompareVisible;
    suppressFloorGrid = savedSuppressFloorGrid;
    forceSeparateOffsetsForOutline = savedForceSeparateOffsetsForOutline;
    forceOpaqueModelRender = savedForceOpaqueModelRender;
    geometryDirty = true;
    renderThreeScene();
  }
}

function closeNarrowSilhouetteGaps(solid, w, h, maxGap = 6) {
  const horizontal = new Uint8Array(solid);
  for (let y = 1; y < h - 1; y++) {
    let x = 1;
    while (x < w - 1) {
      const idx = y * w + x;
      if (solid[idx]) { x++; continue; }
      const start = x;
      while (x < w - 1 && !solid[y * w + x]) x++;
      const end = x - 1;
      if (start > 0 && x < w - 1 && solid[y * w + start - 1] && solid[y * w + x] && end - start + 1 <= maxGap) {
        for (let fx = start; fx <= end; fx++) horizontal[y * w + fx] = 1;
      }
    }
  }

  const closed = new Uint8Array(horizontal);
  for (let x = 1; x < w - 1; x++) {
    let y = 1;
    while (y < h - 1) {
      const idx = y * w + x;
      if (horizontal[idx]) { y++; continue; }
      const start = y;
      while (y < h - 1 && !horizontal[y * w + x]) y++;
      const end = y - 1;
      if (start > 0 && y < h - 1 && horizontal[(start - 1) * w + x] && horizontal[y * w + x] && end - start + 1 <= maxGap) {
        for (let fy = start; fy <= end; fy++) closed[fy * w + x] = 1;
      }
    }
  }
  return closed;
}

function buildFilledSilhouetteMask(sourceCanvas) {
  if (!sourceCanvas) return null;
  ensureOutlineCanvas(sourceCanvas);
  const w = sourceCanvas.width;
  const h = sourceCanvas.height;
  const sourceCtx = sourceCanvas.getContext('2d', {willReadFrequently: true});
  const src = sourceCtx.getImageData(0, 0, w, h);
  const data = src.data;
  const outside = new Uint8Array(w * h);
  const solid = new Uint8Array(w * h);
  const stack = [];
  const pixelCoverage = (idx) => {
    const p = idx * 4;
    if (data[p + 3] < 8) return 0;
    const brightness = (data[p] + data[p + 1] + data[p + 2]) / 3;
    return Math.max(0, Math.min(255, (255 - brightness) * 1.75));
  };
  for (let i = 0; i < solid.length; i++) solid[i] = pixelCoverage(i) >= 10 ? 1 : 0;
  const closedSolid = closeNarrowSilhouetteGaps(solid, w, h, 6);
  const isBackground = (idx) => !closedSolid[idx];
  const pushIfOutside = (idx) => {
    if (idx < 0 || idx >= outside.length || outside[idx] || !isBackground(idx)) return;
    outside[idx] = 1;
    stack.push(idx);
  };
  for (let x = 0; x < w; x++) {
    pushIfOutside(x);
    pushIfOutside((h - 1) * w + x);
  }
  for (let y = 0; y < h; y++) {
    pushIfOutside(y * w);
    pushIfOutside(y * w + (w - 1));
  }
  while (stack.length) {
    const idx = stack.pop();
    const x = idx % w;
    if (idx >= w) pushIfOutside(idx - w);
    if (idx < w * (h - 1)) pushIfOutside(idx + w);
    if (x > 0) pushIfOutside(idx - 1);
    if (x < w - 1) pushIfOutside(idx + 1);
  }

  const maskCtx = outlineMaskCanvas.getContext('2d');
  const mask = maskCtx.createImageData(w, h);
  const md = mask.data;
  for (let i = 0; i < outside.length; i++) {
    if (!outside[i]) {
      const p = i * 4;
      const edgeAlpha = pixelCoverage(i);
      md[p] = 0;
      md[p + 1] = 0;
      md[p + 2] = 0;
      md[p + 3] = Math.max(36, Math.min(255, edgeAlpha || 255));
    }
  }
  maskCtx.putImageData(mask, 0, 0);
  return outlineMaskCanvas;
}
function drawOuterStrokeFromMask(maskCanvas, color, dashed = false, opacity = 1) {
  if (!maskCanvas) return;
  ensureOutlineCanvas(maskCanvas);
  const w = maskCanvas.width;
  const h = maskCanvas.height;
  const strokeCtx = outlineStrokeCanvas.getContext('2d');
  strokeCtx.setTransform(1, 0, 0, 1, 0, 0);
  strokeCtx.clearRect(0, 0, w, h);
  strokeCtx.save();
  strokeCtx.imageSmoothingEnabled = true;
  strokeCtx.imageSmoothingQuality = 'high';
  const shifts = [];
  for (let i = 0; i < 16; i++) {
    const angle = Math.PI * 2 * i / 16;
    shifts.push([Math.cos(angle) * 1.85, Math.sin(angle) * 1.85]);
  }
  for (let i = 0; i < 8; i++) {
    const angle = Math.PI * 2 * (i + 0.5) / 8;
    shifts.push([Math.cos(angle) * 0.95, Math.sin(angle) * 0.95]);
  }
  for (const [dx, dy] of shifts) strokeCtx.drawImage(maskCanvas, dx, dy);
  strokeCtx.globalCompositeOperation = 'source-in';
  strokeCtx.fillStyle = color || '#111111';
  strokeCtx.fillRect(0, 0, w, h);
  strokeCtx.globalCompositeOperation = 'destination-out';
  strokeCtx.drawImage(maskCanvas, 0, 0);
  strokeCtx.restore();

  ctx.save();
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  const alpha = (dashed ? 0.82 : 1) * Math.max(0.1, Math.min(1, Number.isFinite(opacity) ? opacity : 1));
  ctx.globalAlpha = alpha * 0.48;
  ctx.filter = 'blur(0.65px)';
  ctx.drawImage(outlineStrokeCanvas, 0, 0, canvas.width, canvas.height);
  ctx.filter = 'none';
  ctx.globalAlpha = alpha;
  ctx.drawImage(outlineStrokeCanvas, 0, 0, canvas.width, canvas.height);
  ctx.restore();
}

function drawProjectedOutlines() {
  if (renderModeInput.value !== 'outline') return;
  const targets = [];
  if (primaryVisible && mesh) targets.push({name: 'primary', color: modelColor('primary'), dashed: false, opacity: modelOpacity('primary')});
  if (compareMesh && compareVisible) targets.push({name: 'compare', color: modelColor('compare'), dashed: true, opacity: modelOpacity('compare')});
  for (const target of targets) {
    const source = renderSilhouetteSourceForTarget(target.name);
    const mask = buildFilledSilhouetteMask(source);
    drawOuterStrokeFromMask(mask, target.color, target.dashed, target.opacity);
  }
}

function drawModelCenterLines() {
  if (!showCenterLinesInput || !showCenterLinesInput.checked || !mesh) return;
  const offsets = displayOffsets();
  const targets = [];
  if (primaryVisible && mesh) targets.push({targetMesh: mesh, offset: offsets.primary, targetName:'primary'});
  if (compareMesh && compareVisible) targets.push({targetMesh: compareMesh, offset: offsets.compare, targetName:'compare'});
  if (!targets.length) return;
  ctx.save();
  ctx.strokeStyle = 'rgba(92, 102, 112, 0.72)';
  ctx.lineWidth = 2;
  ctx.setLineDash([]);
  for (const target of targets) {
    const b = boundsWithOffset(target.targetMesh, target.offset, target.targetName);
    const cx = (b.min[0] + b.max[0]) / 2;
    const cz = (b.min[2] + b.max[2]) / 2;
    const p0 = project([cx, b.min[1], cz]);
    const p1 = project([cx, b.max[1], cz]);
    if (!Number.isFinite(p0[0]) || !Number.isFinite(p0[1]) || !Number.isFinite(p1[0]) || !Number.isFinite(p1[1])) continue;
    ctx.beginPath();
    ctx.moveTo(p0[0], p0[1]);
    ctx.lineTo(p1[0], p1[1]);
    ctx.stroke();
  }
  ctx.restore();
}
function drawHeightBarForTarget(targetName, x) {
  const targetMesh = meshForLineTarget(targetName);
  const editableLines = linesForLineTarget(targetName);
  if (!targetMesh || !editableLines.length || !modelIsVisible(targetName)) return;
  const top = 60, bottom = canvas.height - 60;
  const bar = {targetName, x, top, bottom};
  heightBar[targetName] = bar;
  ctx.save();
  ctx.strokeStyle = targetName === 'compare' ? 'rgba(120,130,142,0.68)' : '#a9b2bf';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
  ctx.fillStyle = '#5f6b78';
  ctx.font = '700 13px Meiryo, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  ctx.fillText(textFor('moveHandle'), x, Math.max(18, top - 10));
  for (const [i,line] of editableLines.entries()) {
    const h = parseFloat(line.height_from_floor_cm || '0');
    const y = bottom - (h / targetMesh.height_cm) * (bottom - top);
    const isActive = targetName === activeLineTarget && i === activeIndex;
    heightBar.points.push({targetName, index: i, x, y, line});
    ctx.fillStyle = line.color || '#333';
    ctx.strokeStyle = isActive ? '#ffffff' : 'rgba(255,255,255,0.72)';
    ctx.lineWidth = isActive ? 3 : 2;
    ctx.beginPath();
    ctx.arc(x, y, isActive ? 7 : 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

function drawHeightBar() {
  heightBar = {primary: null, compare: null, points: []};
  if (compareMesh && compareVisible) drawHeightBarForTarget('compare', 42);
  if (mesh && primaryVisible) drawHeightBarForTarget('primary', canvas.width - 42);
}

function ensureThree() {
  if (renderer) return;
  renderer = new THREE.WebGLRenderer({canvas: glCanvas, antialias: true, alpha: false, preserveDrawingBuffer: true, powerPreference: 'high-performance'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(glCanvas.width, glCanvas.height, false);
  const bgColor = viewerBackgroundColor();
  renderer.setClearColor(new THREE.Color(bgColor), 1);
  scene = new THREE.Scene();
  scene.background = new THREE.Color(bgColor);
  const ambient = new THREE.AmbientLight(0xffffff, 0.78);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffffff, 0.85);
  key.position.set(0.3, 1.0, 0.8);
  scene.add(key);
}

function meshCenterVector() {
  const b = combinedDisplayBounds() || mesh.bounds;
  return new THREE.Vector3(
    (b.min[0] + b.max[0]) / 2,
    (b.min[1] + b.max[1]) / 2,
    (b.min[2] + b.max[2]) / 2
  );
}

function currentViewBasis() {
  const cp = Math.cos(pitch);
  const dir = new THREE.Vector3(Math.sin(yaw) * cp, Math.sin(pitch), Math.cos(yaw) * cp).normalize();
  const baseUp = Math.abs(dir.y) > 0.96 ? new THREE.Vector3(0, 0, dir.y < 0 ? -1 : 1) : new THREE.Vector3(0, 1, 0);
  const right = new THREE.Vector3().crossVectors(dir, baseUp).normalize();
  const up = new THREE.Vector3().crossVectors(right, dir).normalize();
  return {dir, right, up};
}

function fitViewToModels() {
  if (!mesh || typeof THREE === 'undefined') return;
  const b = combinedDisplayBounds() || mesh.bounds;
  const center = meshCenterVector();
  const {right, up} = currentViewBasis();
  const aspect = glCanvas.width / glCanvas.height;
  let maxRight = 1;
  let maxUp = 1;
  for (const x of [b.min[0], b.max[0]]) {
    for (const y of [b.min[1], b.max[1]]) {
      for (const z of [b.min[2], b.max[2]]) {
        const p = new THREE.Vector3(x, y, z).sub(center);
        maxRight = Math.max(maxRight, Math.abs(p.dot(right)));
        maxUp = Math.max(maxUp, Math.abs(p.dot(up)));
      }
    }
  }
  const margin = 1.26;
  const requiredViewHeight = Math.max(maxUp * 2 * margin, (maxRight * 2 * margin) / aspect, 1);
  const modelHeight = Math.max(displayHeightForTarget('primary'), displayHeightForTarget('compare'), 1);
  const baseViewHeight = orthographic ? modelHeight * 1.15 : modelHeight * 2.1 * 2 * Math.tan(35 * Math.PI / 360);
  zoom = Math.max(0.08, Math.min(5.0, baseViewHeight / requiredViewHeight));
  panX = 0;
  panY = 0;
  panPreviewX = 0;
  panPreviewY = 0;
  clearPanPreviewTransform();
  draw();
}
function updateThreeCamera() {
  const aspect = glCanvas.width / glCanvas.height;
  const viewHeight = Math.max(displayHeightForTarget('primary'), displayHeightForTarget('compare'), 1) * 1.15 / zoom;
  if (orthographic) {
    if (!camera || !camera.isOrthographicCamera) {
      camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 10000);
    }
    camera.left = -viewHeight * aspect / 2;
    camera.right = viewHeight * aspect / 2;
    camera.top = viewHeight / 2;
    camera.bottom = -viewHeight / 2;
    camera.near = 0.01;
    camera.far = 10000;
  } else {
    if (!camera || !camera.isPerspectiveCamera) {
      camera = new THREE.PerspectiveCamera(35, aspect, 0.01, 10000);
    }
    camera.aspect = aspect;
  }

  const center = meshCenterVector();
  const cp = Math.cos(pitch);
  const dir = new THREE.Vector3(Math.sin(yaw) * cp, Math.sin(pitch), Math.cos(yaw) * cp).normalize();
  const baseUp = Math.abs(dir.y) > 0.96 ? new THREE.Vector3(0, 0, dir.y < 0 ? -1 : 1) : new THREE.Vector3(0, 1, 0);
  const right = new THREE.Vector3().crossVectors(dir, baseUp).normalize();
  const up = new THREE.Vector3().crossVectors(right, dir).normalize();
  const target = center.clone();
  const worldPerPixel = viewHeight / glCanvas.height;
  target.addScaledVector(right, -panX * worldPerPixel);
  target.addScaledVector(up, panY * worldPerPixel);
  const distance = Math.max(displayHeightForTarget('primary'), displayHeightForTarget('compare'), 1) * (orthographic ? 2.4 : 2.1 / zoom);
  camera.position.copy(target).addScaledVector(dir, distance);
  camera.up.copy(up);
  camera.lookAt(target);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
}

function disposeObject(object) {
  if (!object) return;
  object.traverse(child => {
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      if (Array.isArray(child.material)) child.material.forEach(m => m.dispose());
      else child.material.dispose();
    }
  });
}

function clearFloorGrid() {
  if (floorGridObject && scene) {
    scene.remove(floorGridObject);
    disposeObject(floorGridObject);
    floorGridObject = null;
  }
}

function isMajorGridLine(value, step) {
  return Math.abs(value / step - Math.round(value / step)) < 0.0001;
}

function rebuildFloorGrid() {
  clearFloorGrid();
  if (!showFloorGridInput || !showFloorGridInput.checked || suppressFloorGrid || !mesh || !scene) return;
  const b = combinedDisplayBounds() || mesh.bounds;
  if (!b) return;
  const width = Math.max(b.max[0] - b.min[0], 80);
  const depth = Math.max(b.max[2] - b.min[2], 80);
  const span = Math.max(width, depth, displayHeightForTarget('primary') || 120, displayHeightForTarget('compare') || 0) * 1.15;
  const centerX = (b.min[0] + b.max[0]) / 2;
  const centerZ = (b.min[2] + b.max[2]) / 2;
  const y = b.min[1] - 0.35;
  const step = 10;
  const majorStep = 50;
  const minX = Math.floor((centerX - span / 2) / step) * step;
  const maxX = Math.ceil((centerX + span / 2) / step) * step;
  const minZ = Math.floor((centerZ - span / 2) / step) * step;
  const maxZ = Math.ceil((centerZ + span / 2) / step) * step;
  const minor = [];
  const major = [];
  function pushLine(list, x1, z1, x2, z2) {
    list.push(x1, y, z1, x2, y, z2);
  }
  for (let x = minX; x <= maxX + 0.001; x += step) {
    pushLine(isMajorGridLine(x, majorStep) ? major : minor, x, minZ, x, maxZ);
  }
  for (let z = minZ; z <= maxZ + 0.001; z += step) {
    pushLine(isMajorGridLine(z, majorStep) ? major : minor, minX, z, maxX, z);
  }
  const group = new THREE.Group();
  const addLines = (positions, color, lineWidth) => {
    if (!positions.length) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const material = new THREE.LineBasicMaterial({color, transparent: false, depthTest: false, depthWrite: false, linewidth: lineWidth});
    const linesObject = new THREE.LineSegments(geometry, material);
    linesObject.frustumCulled = false;
    linesObject.renderOrder = -100;
    group.add(linesObject);
  };
  group.renderOrder = -100;
  addLines(minor, 0xe4e9ef, 1);
  addLines(major, 0xc6d0dc, 1);
  floorGridObject = group;
  scene.add(floorGridObject);
}

function buildThreeModelObject(targetMesh, offset, color, targetName = 'primary') {
  const o = normalizeOffset(offset);
  const mode = renderModeInput.value;
  if (mode === 'outline') {
    const object = new THREE.Group();
    object.position.set(o.x, o.y, o.z);
    return object;
  }
  const renderFaces = mode === 'silhouette' && targetMesh.silhouette_faces ? targetMesh.silhouette_faces : targetMesh.faces;
  const activeArmMask = hideArmsInput.checked && armMaskCache && armMaskCache.get(targetMesh) ? armMaskCache.get(targetMesh).get(renderFaces) : null;
  const positions = [];
  for (const [i, f] of renderFaces.entries()) {
    if (activeArmMask && activeArmMask[i]) continue;
    if (isFaceMaskedByUserMasks(targetName, f)) continue;
    for (const idx of f) {
      const v = targetMesh.vertices[idx];
      const p = scaledPointForTarget(targetMesh, targetName, v, offset);
      positions.push(p[0], p[1], p[2]);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  const opacity = modelOpacity(targetName);
  const overlay = isOverlayLayout();
  const material = new THREE.MeshBasicMaterial({
    color,
    side:THREE.DoubleSide,
    transparent:overlay || opacity < 0.999,
    opacity,
    depthTest:!overlay,
    depthWrite:!overlay && opacity >= 0.999
  });
  const object = new THREE.Mesh(geometry, material);
  object.renderOrder = overlay ? (targetName === 'compare' ? 10 : 20) : 0;
  object.userData = {modelShape: true, targetName};
  object.frustumCulled = false;
  object.position.set(0, 0, 0);
  return object;
}

function rebuildThreeMesh() {
  if (!mesh || !scene) return;
  if (meshObject) {
    scene.remove(meshObject);
    disposeObject(meshObject);
    meshObject = null;
  }
  clearFloorGrid();
  if (hideArmsInput.checked && (!armMaskCache || !armMaskCache.get(mesh) || (compareMesh && !armMaskCache.get(compareMesh)))) rebuildArmMask();
  const offsets = displayOffsets();
  rebuildFloorGrid();
  meshObject = new THREE.Group();
  if (isOverlayLayout()) {
    if (compareMesh && compareVisible) meshObject.add(buildThreeModelObject(compareMesh, offsets.compare, modelColor('compare'), 'compare'));
    if (primaryVisible) meshObject.add(buildThreeModelObject(mesh, offsets.primary, modelColor('primary'), 'primary'));
  } else {
    if (primaryVisible) meshObject.add(buildThreeModelObject(mesh, offsets.primary, modelColor('primary'), 'primary'));
    if (compareMesh && compareVisible) meshObject.add(buildThreeModelObject(compareMesh, offsets.compare, modelColor('compare'), 'compare'));
  }
  scene.add(meshObject);
  geometryDirty = false;
}

function applyModelMaterialColors() {
  if (!meshObject) return;
  meshObject.traverse(child => {
    if (!child.userData || !child.userData.modelShape || !child.material) return;
    const targetName = child.userData.targetName || 'primary';
    const color = modelColor(targetName);
    const opacity = modelOpacity(targetName);
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    for (const material of materials) {
      if (material && material.color) {
        material.color.set(color);
        const overlay = isOverlayLayout();
        child.renderOrder = overlay ? (targetName === 'compare' ? 10 : 20) : 0;
        material.opacity = opacity;
        material.transparent = overlay || opacity < 0.999;
        material.depthTest = !overlay;
        material.depthWrite = !overlay && opacity >= 0.999;
        material.needsUpdate = true;
      }
    }
  });
}

function renderThreeScene() {
  if (typeof THREE === 'undefined') return;
  if (!mesh) {
    ensureThree();
    if (meshObject) { scene.remove(meshObject); disposeObject(meshObject); meshObject = null; }
    clearFloorGrid();
    renderer.clear();
    return;
  }
  ensureThree();
  updateThreeCamera();
  if (geometryDirty || !meshObject) rebuildThreeMesh();
  else applyModelMaterialColors();
  updateThreeCamera();
  renderer.render(scene, camera);
}
function drawModelLabels() {
  if (!mesh) return;
  const offsets = displayOffsets();
  const items = [];
  if (primaryVisible && mesh) items.push({label:textFor('primaryModel'), target:mesh, offset:offsets.primary, color:modelColor('primary'), targetName:'primary'});
  if (compareVisible && compareMesh) items.push({label:textFor('compareModel'), target:compareMesh, offset:offsets.compare, color:modelColor('compare'), targetName:'compare'});
  ctx.save();
  ctx.font = '700 13px Meiryo';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const item of items) {
    const b = boundsWithOffset(item.target, item.offset, item.targetName);
    const p = project([(b.min[0] + b.max[0]) / 2, b.max[1], (b.min[2] + b.max[2]) / 2]);
    const labelY = Math.max(18, p[1] - 24);
    ctx.fillStyle = 'rgba(255,255,255,0.88)';
    const width = ctx.measureText(item.label).width + 14;
    ctx.fillRect(p[0] - width / 2, labelY - 11, width, 22);
    ctx.fillStyle = item.color;
    ctx.fillText(item.label, p[0], labelY);
  }
  ctx.restore();
}
function drawOverlayOnly() {
  ctx.clearRect(0,0,canvas.width,canvas.height);
  drawUnderlay();
  ctx.fillStyle = '#5f6b78'; ctx.font = '13px Meiryo';
  if (!mesh) {
    ctx.fillStyle = '#5f6b78'; ctx.font = '16px Meiryo'; ctx.fillText(textFor('noModel'), 30, 40);
    drawDoodles();
    drawUnderlayScaleGizmo();
    return;
  }
  drawProjectedOutlines();
  drawAppliedMasks();
  drawModelCenterLines();
  const offsets = displayOffsets();
  if (primaryVisible) {
    for (const line of lines) {
      if (line.preview_visible !== false) drawLinePlane(line, mesh, offsets.primary, 'primary');
    }
  }
  if (compareMesh && compareVisible) {
    const compareSet = compareLines.length ? compareLines : cloneLinesForMesh(lines, mesh, compareMesh);
    for (const line of compareSet) {
      if (line.preview_visible !== false) drawLinePlane(line, compareMesh, offsets.compare, 'compare');
    }
  }
  drawHeightBar();
  if (!isInteracting) {
    drawIsoGuide();
    drawAxisGizmo();
    drawModelLabels();
  }
  drawMaskGuides();
  drawDoodles();
  drawUnderlayScaleGizmo();
}

function drawOverlaysFromBase() {
  drawOverlayOnly();
}

function doodleTitle(key) {
  const titles = {
    ja: {pen:'落書きペン', circle:'落書き円', clear:'落書き削除'},
    en: {pen:'Doodle pen', circle:'Doodle circle', clear:'Clear doodles'}
  };
  return (titles[currentLanguage] && titles[currentLanguage][key]) || titles.en[key] || key;
}

function doodleColorTitle() {
  return currentLanguage === 'ja' ? '落書き色' : 'Doodle color';
}

function updateDoodleColorControl() {
  if (!doodleColorInput) return;
  doodleColorInput.value = doodleColor;
  doodleColorInput.title = doodleColorTitle();
  doodleColorInput.setAttribute('aria-label', doodleColorTitle());
}

function updateDoodleButtons() {
  const buttons = [
    {button:doodlePenButton, mode:'pen', title:doodleTitle('pen')},
    {button:doodleCircleButton, mode:'circle', title:doodleTitle('circle')},
    {button:doodleClearButton, mode:null, title:doodleTitle('clear')},
  ];
  for (const item of buttons) {
    if (!item.button) continue;
    const active = item.mode && doodleMode === item.mode;
    item.button.classList.toggle('active', !!active);
    item.button.setAttribute('aria-pressed', active ? 'true' : 'false');
    item.button.title = item.title;
    item.button.setAttribute('aria-label', item.title);
  }
}

function setDoodleMode(mode) {
  doodleMode = doodleMode === mode ? 'none' : mode;
  if (doodleMode !== 'none') {
    underlayMoveMode = false;
    if (maskDrawModeInput) maskDrawModeInput.checked = false;
    updateUnderlayMoveButton();
  }
  doodleDraft = null;
  updateDoodleButtons();
  updateViewerCursor();
}

function normalizedDoodlePoint(x, y) {
  return {x:x / Math.max(1, canvas.width), y:y / Math.max(1, canvas.height)};
}

function doodlePointToScreen(point) {
  return {x:point.x * canvas.width, y:point.y * canvas.height};
}

function drawDoodleShape(shape) {
  if (!shape) return;
  ctx.save();
  ctx.strokeStyle = shape.color || doodleColor;
  ctx.lineWidth = shape.width || DOODLE_WIDTH;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.globalAlpha = 0.92;
  if (shape.type === 'pen' && shape.points && shape.points.length) {
    ctx.beginPath();
    shape.points.forEach((point, index) => {
      const p = doodlePointToScreen(point);
      if (index) ctx.lineTo(p.x, p.y);
      else ctx.moveTo(p.x, p.y);
    });
    ctx.stroke();
  } else if (shape.type === 'circle' && shape.start && shape.end) {
    const a = doodlePointToScreen(shape.start);
    const b = doodlePointToScreen(shape.end);
    const cx = (a.x + b.x) / 2;
    const cy = (a.y + b.y) / 2;
    const rx = Math.abs(b.x - a.x) / 2;
    const ry = Math.abs(b.y - a.y) / 2;
    if (rx > 1 || ry > 1) {
      ctx.beginPath();
      ctx.ellipse(cx, cy, Math.max(1, rx), Math.max(1, ry), 0, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  ctx.restore();
}

function drawDoodles() {
  for (const shape of doodleShapes) drawDoodleShape(shape);
  if (doodleDraft) drawDoodleShape(doodleDraft);
}

function addDoodlePoint(x, y) {
  if (!doodleDraft || doodleDraft.type !== 'pen') return;
  const next = normalizedDoodlePoint(x, y);
  const last = doodleDraft.points[doodleDraft.points.length - 1];
  if (!last || Math.hypot((next.x - last.x) * canvas.width, (next.y - last.y) * canvas.height) >= 2) {
    doodleDraft.points.push(next);
  }
}

function clearDoodles() {
  const hadDoodles = doodleShapes.length || doodleDraft;
  if (hadDoodles) pushHistory();
  doodleShapes = [];
  doodleDraft = null;
  doodleMode = 'none';
  updateDoodleButtons();
  updateViewerCursor();
  draw();
}

function setQuickButtonState(button, active, label) {
  if (!button) return;
  button.classList.toggle('active', !!active);
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  if (label) {
    button.title = label;
    button.setAttribute('aria-label', label);
  }
}

function projectionQuickLabel() {
  if (currentLanguage === 'ja') return orthographic ? '正投影' : 'パース';
  return orthographic ? 'Orthographic' : 'Perspective';
}

function updateQuickViewControls() {
  setQuickButtonState(floorGridQuickButton, !showFloorGridInput || showFloorGridInput.checked, textFor('floorGrid'));
  setQuickButtonState(centerLinesQuickButton, !showCenterLinesInput || showCenterLinesInput.checked, textFor('centerLines'));
  setQuickButtonState(projectionQuickButton, !orthographic, projectionQuickLabel());
  setQuickButtonState(fitViewQuickButton, false, textFor('fitView'));
}

function toggleViewCheckbox(input) {
  if (!input) return;
  input.checked = !input.checked;
  input.dispatchEvent(new Event('change', {bubbles:true}));
}

function toggleProjectionMode() {
  pushHistory();
  orthographic = !orthographic;
  geometryDirty = true;
  if (!orthographic && isOverlayLayout()) statusEl.textContent = overlayPerspectiveWarning();
  updateQuickViewControls();
  draw();
}

function resizeCanvasesToDisplaySize() {
  const rect = viewerWrap.getBoundingClientRect();
  const w = Math.max(360, Math.round(rect.width));
  const h = Math.max(360, Math.round(rect.height));
  if (canvas.width === w && canvas.height === h && glCanvas.width === w && glCanvas.height === h) return false;
  canvas.width = w;
  canvas.height = h;
  glCanvas.width = w;
  glCanvas.height = h;
  if (renderer) renderer.setSize(w, h, false);
  return true;
}
function draw() {
  resizeCanvasesToDisplaySize();
  applyViewerBackground();
  updateQuickViewControls();
  try {
    renderThreeScene();
  } catch (err) {
    statusEl.textContent = textFor('renderError') + (err.message || err);
  }
  drawOverlayOnly();
}

function setViewPreset(key) {
  if (key === 'p' || key === 'P') {
    orthographic = !orthographic;
    draw();
    return;
  }
  orthographic = true;
  if (key === '2' || key === '1') { yaw = 0; pitch = 0; }
  if (key === '8' || key === '3') { yaw = Math.PI; pitch = 0; }
  if (key === '6') { yaw = -Math.PI / 2; pitch = 0; }
  if (key === '4') { yaw = Math.PI / 2; pitch = 0; }
  if (key === '5' || key === '7') { yaw = 0; pitch = -Math.PI / 2; }
  geometryDirty = true;
  syncYawSlider();
  draw();
}

window.addEventListener('resize', () => requestDraw());

window.addEventListener('keydown', e => {
  const key = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && key === 'z') {
    e.preventDefault();
    if (e.shiftKey) redoContour();
    else undoContour();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && key === 'y') {
    e.preventDefault();
    redoContour();
    return;
  }
  if (['1','2','3','4','5','6','7','8','p','P'].includes(e.key)) {
    e.preventDefault();
    setViewPreset(e.key);
  }
});

for (const el of [canvas, glCanvas, viewerWrap]) {
  el.addEventListener('contextmenu', e => e.preventDefault());
  el.addEventListener('auxclick', e => {
    if (e.button === 1 || e.button === 2) {
      e.preventDefault();
      e.stopPropagation();
    }
  });
}
canvas.addEventListener('mousemove', e => {
  if (!dragging) updateViewerCursor(e.offsetX, e.offsetY);
});
canvas.addEventListener('mouseleave', () => {
  if (!dragging) updateViewerCursor();
});
canvas.addEventListener('mousedown', e => {
  e.preventDefault();
  dragging = true;
  isInteracting = true;
  document.body.style.userSelect = 'none';
  const wantsDoodle = doodleMode !== 'none' && e.button === 0 && !e.shiftKey && !e.ctrlKey && !e.altKey;
  const underlayLeftDrag = !wantsDoodle && underlayMoveMode && underlay.img && e.button === 0;
  const underlayGizmoHandle = underlayLeftDrag ? hitUnderlayGizmo(e.offsetX, e.offsetY) : null;
  const wantsUnderlayGizmoRotate = !!underlayGizmoHandle && underlayGizmoHandle.type === 'rotate';
  const wantsUnderlayGizmoScale = !!underlayGizmoHandle && underlayGizmoHandle.type !== 'rotate';
  const wantsUnderlayScale = underlayLeftDrag && !wantsUnderlayGizmoScale && !wantsUnderlayGizmoRotate && (e.shiftKey || e.ctrlKey || e.altKey);
  const wantsUnderlay = underlayLeftDrag && !wantsUnderlayScale && !wantsUnderlayGizmoScale && !wantsUnderlayGizmoRotate;
  const wantsPan = !wantsDoodle && !underlayLeftDrag && (e.button === 1 || e.button === 2 || e.shiftKey);
  if (underlayMoveMode && !underlay.img && e.button === 0 && !wantsPan) statusEl.textContent = textFor('underlayNoImage');
  const wantsMask = !wantsDoodle && maskDrawModeInput && maskDrawModeInput.checked && e.button === 0 && !wantsPan && !wantsUnderlay && !wantsUnderlayScale && !wantsUnderlayGizmoScale && !wantsUnderlayGizmoRotate;
  const heightHit = !wantsPan && !wantsMask && !wantsUnderlay && !wantsUnderlayScale && !wantsUnderlayGizmoScale && !wantsUnderlayGizmoRotate && e.button === 0 ? hitHeightBarPoint(e.offsetX, e.offsetY) : null;
  dragMode = wantsDoodle ? 'doodle' : (wantsPan ? 'pan' : (wantsUnderlayGizmoRotate ? 'underlayRotate' : ((wantsUnderlayScale || wantsUnderlayGizmoScale) ? 'underlayScale' : (wantsUnderlay ? 'underlay' : (wantsMask ? 'mask' : (heightHit ? 'heightbar' : (pickSectionHeightInput.checked ? 'section' : 'rotate')))))));
  lastX=e.offsetX; lastY=e.offsetY;
  lastClientX=e.clientX; lastClientY=e.clientY;
  panPreviewX=0; panPreviewY=0;
  dragDistance = 0;
  if (['underlay', 'underlayScale', 'underlayRotate', 'heightbar', 'section', 'mask', 'doodle'].includes(dragMode)) beginHistory();
  if (dragMode === 'doodle') {
    const start = normalizedDoodlePoint(e.offsetX, e.offsetY);
    doodleDraft = doodleMode === 'circle'
      ? {type:'circle', color:doodleColor, width:DOODLE_WIDTH, start, end:start}
      : {type:'pen', color:doodleColor, width:DOODLE_WIDTH, points:[start]};
    drawOverlaysFromBase();
  }
  if (dragMode === 'underlayScale') {
    if (underlayGizmoHandle) {
      const startDistance = Math.max(1, Math.hypot(e.offsetX - underlayGizmoHandle.anchorX, e.offsetY - underlayGizmoHandle.anchorY));
      underlayScaleStart = {mode:'corner', handle:underlayGizmoHandle.name, scale: underlay.scale || 1, rotation:underlayRotation(), anchorX: underlayGizmoHandle.anchorX, anchorY: underlayGizmoHandle.anchorY, anchorLocalX: underlayGizmoHandle.anchorLocalX, anchorLocalY: underlayGizmoHandle.anchorLocalY, startDistance};
      canvas.style.cursor = underlayGizmoHandle.cursor;
    } else {
      underlayScaleStart = {mode:'free', scale: underlay.scale || 1, x: underlay.x, y: underlay.y, anchorX: e.offsetX, anchorY: e.offsetY};
    }
    statusEl.textContent = '';
  }
  if (dragMode === 'underlayRotate' && underlayGizmoHandle) {
    const center = underlayCenter();
    underlayScaleStart = {mode:'rotate', rotation:underlayRotation(), centerX:center.x, centerY:center.y, startAngle:Math.atan2(e.offsetY - center.y, e.offsetX - center.x)};
    canvas.style.cursor = 'grabbing';
    statusEl.textContent = '';
  }
  if (dragMode === 'heightbar' && heightHit) {
    activeLineTarget = heightHit.targetName;
    activeIndex = heightHit.index;
    renderLineList();
    setActiveSectionHeightFromBarY(e.offsetY, false, heightHit.targetName);
  }
  if (dragMode === 'section') setActiveSectionHeightFromScreenY(e.offsetY, false);
  if (dragMode === 'mask') maskDraft = {x0:e.offsetX, y0:e.offsetY, x1:e.offsetX, y1:e.offsetY, points:[{x:e.offsetX, y:e.offsetY}]};
});
window.addEventListener('mouseup', () => {
  const wasSectionDrag = dragging && dragMode === 'section';
  const wasHeightBarDrag = dragging && dragMode === 'heightbar';
  const wasPanDrag = dragging && dragMode === 'pan';
  const wasUnderlayDrag = dragging && (dragMode === 'underlay' || dragMode === 'underlayScale' || dragMode === 'underlayRotate');
  const wasMaskDrag = dragging && dragMode === 'mask';
  const wasDoodleDrag = dragging && dragMode === 'doodle';
  dragging = false;
  isInteracting = false;
  document.body.style.userSelect = '';
  if (wasPanDrag) {
    panPreviewX = 0;
    panPreviewY = 0;
    clearPanPreviewTransform();
  }
  if (wasDoodleDrag && doodleDraft) {
    const usable = doodleDraft.type === 'circle'
      ? Math.hypot((doodleDraft.end.x - doodleDraft.start.x) * canvas.width, (doodleDraft.end.y - doodleDraft.start.y) * canvas.height) > 3
      : doodleDraft.points.length > 1;
    if (usable) {
      doodleShapes.push(doodleDraft);
      commitHistory();
    } else {
      cancelHistory();
    }
    doodleDraft = null;
    draw();
  } else if (wasMaskDrag && maskDraft) {
    const shape = maskShapeFromDraft(maskDraft);
    const createdMasks = addUserMasksFromScreenShape(shape);
    maskDraft = null;
    if (createdMasks) {
      commitHistory();
      resultStatus.textContent = textFor('maskCreated');
    } else {
      cancelHistory();
    }
    draw();
  } else if (wasUnderlayDrag) {
    commitHistory();
    underlayScaleStart = null;
    draw();
  } else if (wasSectionDrag || wasHeightBarDrag) {
    commitHistory();
    if (hideArmsInput.checked) rebuildArmMask();
    renderLineList();
    draw();
  } else {
    cancelHistory();
    draw();
  }
});
window.addEventListener('mousemove', e => {
  if (!dragging) return;
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const dx = x - lastX;
  const dy = y - lastY;
  dragDistance += Math.hypot(e.clientX - lastClientX, e.clientY - lastClientY);
  if (dragMode === 'doodle') {
    if (doodleDraft) {
      if (doodleDraft.type === 'circle') doodleDraft.end = normalizedDoodlePoint(x, y);
      else addDoodlePoint(x, y);
      drawOverlaysFromBase();
    }
    lastX=x; lastY=y;
    lastClientX=e.clientX; lastClientY=e.clientY;
    return;
  }
  if (dragMode === 'pan') {
    const cdx = e.clientX - lastClientX;
    const cdy = e.clientY - lastClientY;
    panX -= cdx;
    panY += cdy;
    lastX = x;
    lastY = y;
    lastClientX = e.clientX;
    lastClientY = e.clientY;
    requestDraw();
    return;
  } else if (dragMode === 'underlay') {
    underlay.x += dx;
    underlay.y += dy;
    lastX=x; lastY=y;
    lastClientX=e.clientX; lastClientY=e.clientY;
    requestDraw();
    return;
  } else if (dragMode === 'underlayScale') {
    const start = underlayScaleStart || {mode:'free', scale: underlay.scale || 1, x: underlay.x, y: underlay.y, anchorX: lastX, anchorY: lastY};
    const oldScale = underlay.scale || 1;
    if (start.mode === 'corner') {
      const distance = Math.max(1, Math.hypot(x - start.anchorX, y - start.anchorY));
      const newScale = Math.max(0.05, Math.min(12, start.scale * distance / Math.max(1, start.startDistance)));
      const centerX = start.anchorX - (start.anchorLocalX * newScale * Math.cos(start.rotation) - start.anchorLocalY * newScale * Math.sin(start.rotation));
      const centerY = start.anchorY - (start.anchorLocalX * newScale * Math.sin(start.rotation) + start.anchorLocalY * newScale * Math.cos(start.rotation));
      underlay.scale = newScale;
      underlay.rotation = start.rotation;
      setUnderlayCenter(centerX, centerY, newScale);
    } else {
      const dragAmount = (x - start.anchorX) - (y - start.anchorY);
      const newScale = Math.max(0.05, Math.min(12, start.scale * Math.exp(dragAmount * 0.006)));
      if (newScale !== oldScale) {
        scaleUnderlayAroundPoint(x, y, newScale);
      }
    }
    lastX=x; lastY=y;
    lastClientX=e.clientX; lastClientY=e.clientY;
    requestDraw();
    return;
  } else if (dragMode === 'underlayRotate') {
    const start = underlayScaleStart || {mode:'rotate', rotation:underlayRotation(), centerX:x, centerY:y, startAngle:0};
    underlay.rotation = start.rotation + Math.atan2(y - start.centerY, x - start.centerX) - start.startAngle;
    lastX=x; lastY=y;
    lastClientX=e.clientX; lastClientY=e.clientY;
    requestDraw();
    return;
  } else if (dragMode === 'heightbar') {
    setActiveSectionHeightFromBarY(y, false, activeLineTarget);
    lastX=x; lastY=y;
    lastClientX=e.clientX; lastClientY=e.clientY;
    return;
  } else if (dragMode === 'section') {
    setActiveSectionHeightFromScreenY(y, false);
    lastX=x; lastY=y;
    lastClientX=e.clientX; lastClientY=e.clientY;
    return;
  } else if (dragMode === 'mask') {
    if (maskDraft) {
      maskDraft.x1 = x;
      maskDraft.y1 = y;
      const lastPoint = maskDraft.points && maskDraft.points[maskDraft.points.length - 1];
      if (!lastPoint || Math.hypot(x - lastPoint.x, y - lastPoint.y) >= 2) maskDraft.points.push({x, y});
      requestOverlayDraw();
    }
    lastX=x; lastY=y;
    lastClientX=e.clientX; lastClientY=e.clientY;
    return;
  } else {
    const yawSign = invertYawInput.checked ? -1 : 1;
    const pitchSign = invertPitchInput.checked ? -1 : 1;
    yaw += dx * 0.008 * yawSign;
    pitch -= dy * 0.006 * pitchSign;
    geometryDirty = true;
    pitch = Math.max(-1.25, Math.min(1.25, pitch));
    syncYawSlider();
  }
  lastX=x; lastY=y;
  lastClientX=e.clientX; lastClientY=e.clientY;
  requestDraw();
});
function handleWheelZoom(e) {
  e.preventDefault();
  e.stopPropagation();
  if (underlayMoveMode && underlay.img) {
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const oldScale = underlay.scale || 1;
    beginHistory();
    const newScale = Math.max(0.05, Math.min(12, oldScale * Math.pow(1.0018, -e.deltaY)));
    if (newScale !== oldScale) {
      scaleUnderlayAroundPoint(cx, cy, newScale);
      if (underlayWheelHistoryTimer) window.clearTimeout(underlayWheelHistoryTimer);
      underlayWheelHistoryTimer = window.setTimeout(() => {
        commitHistory();
        underlayWheelHistoryTimer = null;
      }, 350);
    } else {
      cancelHistory();
    }
    requestDraw();
    return;
  }
  const factor = Math.pow(1.0018, -e.deltaY);
  zoom *= factor;
  zoom = Math.max(0.08, Math.min(5.0, zoom));
  requestDraw();
}
for (const el of [canvas, glCanvas, viewerWrap]) {
  el.addEventListener('wheel', handleWheelZoom, {passive:false});
}
canvas.addEventListener('dblclick', e => { panX = 0; panY = 0; panPreviewX = 0; panPreviewY = 0; clearPanPreviewTransform(); draw(); });
canvas.addEventListener('click', e => {
  const targetMesh = currentMesh();
  const editableLines = currentLines();
  if (!targetMesh || !heightBar || dragDistance > 4 || !editableLines[activeIndex]) return;
  const pointHit = hitHeightBarPoint(e.offsetX, e.offsetY);
  const trackHit = pointHit ? null : hitHeightBarTrack(e.offsetX);
  if (pointHit) {
    pushHistory();
    activeLineTarget = pointHit.targetName;
    activeIndex = pointHit.index;
    setActiveSectionHeightFromBarY(e.offsetY, true, pointHit.targetName);
    return;
  }
  if (trackHit) {
    pushHistory();
    activeLineTarget = trackHit.targetName;
    activeIndex = Math.min(activeIndex, Math.max(linesForLineTarget(activeLineTarget).length - 1, 0));
    setActiveSectionHeightFromBarY(e.offsetY, true, trackHit.targetName);
    return;
  }
  if (pickSectionHeightInput.checked) {
    const h = screenYToHeightCm(e.offsetY);
    if (h === null) return;
    pushHistory();
    editableLines[activeIndex].height_from_floor_cm = h.toFixed(2);
    editableLines[activeIndex].height_ratio = '';
    if (hideArmsInput.checked) rebuildArmMask();
    renderLineList(); draw();
  }
});


hideArmsInput.addEventListener('change', () => { pushHistory(); armMaskCache = null; geometryDirty = true; draw(); });
lineGuideOnlyInput.addEventListener('change', () => { pushHistory(); draw(); });
pickSectionHeightInput.addEventListener('change', () => {
  pushHistory();
  if (pickSectionHeightInput.checked) {
    maskDrawModeInput.checked = false;
    underlayMoveMode = false;
    updateUnderlayMoveButton();
  }
  maskDraft = null;
  updateViewerCursor();
  draw();
});
maskDrawModeInput.addEventListener('change', () => {
  pushHistory();
  if (maskDrawModeInput.checked) {
    pickSectionHeightInput.checked = false;
    underlayMoveMode = false;
    updateUnderlayMoveButton();
  }
  maskDraft = null;
  updateViewerCursor();
  draw();
});
if (maskShapeModeInput) maskShapeModeInput.addEventListener('change', () => {
  pushHistory();
  maskDraft = null;
  updateViewerCursor();
  draw();
});
if (showMaskRectsInput) showMaskRectsInput.addEventListener('change', () => { pushHistory(); draw(); });
clearMasksButton.addEventListener('click', () => {
  const hadMasks = screenMasks.length > 0;
  const hadDoodles = doodleShapes.length > 0 || !!doodleDraft;
  if (hadMasks || hadDoodles || maskDraft) pushHistory();
  screenMasks = [];
  doodleShapes = [];
  doodleDraft = null;
  setDoodleMode('none');
  maskDraft = null;
  if (hadMasks) geometryDirty = true;
  resultStatus.textContent = textFor('masksCleared');
  draw();
});
if (underlayImageFileInput) underlayImageFileInput.addEventListener('change', e => loadUnderlayFile(e.target.files && e.target.files[0]));
if (moveUnderlayButton) moveUnderlayButton.addEventListener('click', () => {
  if (!underlay.img) {
    statusEl.textContent = textFor('underlayNoImage');
    return;
  }
  underlayMoveMode = !underlayMoveMode;
  if (underlayMoveMode && maskDrawModeInput) maskDrawModeInput.checked = false;
  updateUnderlayMoveButton();
  updateViewerCursor();
  statusEl.textContent = '';
});
if (deleteUnderlayButton) deleteUnderlayButton.addEventListener('click', () => {
  if (underlay.img) pushHistory();
  underlay = { img:null, src:null, x:0, y:0, scale:1, rotation:0 };
  underlayMoveMode = false;
  if (underlayImageFileInput) underlayImageFileInput.value = '';
  updateUnderlayMoveButton();
  updateViewerCursor();
  statusEl.textContent = '';
  draw();
});
renderModeInput.addEventListener('change', () => { pushHistory(); geometryDirty = true; draw(); });
if (floorGridQuickButton) floorGridQuickButton.addEventListener('click', () => toggleViewCheckbox(showFloorGridInput));
if (centerLinesQuickButton) centerLinesQuickButton.addEventListener('click', () => toggleViewCheckbox(showCenterLinesInput));
if (projectionQuickButton) projectionQuickButton.addEventListener('click', toggleProjectionMode);
if (fitViewQuickButton) fitViewQuickButton.addEventListener('click', () => fitViewToModels());
if (showFloorGridInput) showFloorGridInput.addEventListener('change', () => { pushHistory(); geometryDirty = true; updateQuickViewControls(); draw(); });
if (showCenterLinesInput) showCenterLinesInput.addEventListener('change', () => { pushHistory(); updateQuickViewControls(); draw(); });
for (const input of [primarySilhouetteColorInput, compareSilhouetteColorInput]) {
  if (input) input.addEventListener('input', requestDraw);
}
for (const input of [primaryModelOpacityInput, compareModelOpacityInput]) {
  if (input) input.addEventListener('input', () => { updateModelOpacityLabels(); requestDraw(); });
}
if (heightScaleMatchPrimaryButton) heightScaleMatchPrimaryButton.addEventListener('click', () => { if (setHeightScaleMode('primary')) statusEl.textContent = textFor('heightScaleApplied'); });
if (heightScaleMatchCompareButton) heightScaleMatchCompareButton.addEventListener('click', () => { if (setHeightScaleMode('compare')) statusEl.textContent = textFor('heightScaleApplied'); });
if (heightScaleClearButton) heightScaleClearButton.addEventListener('click', () => { if (setHeightScaleMode('clear')) statusEl.textContent = textFor('heightScaleApplied'); });
if (viewerBgColorInput) {
  viewerBgColorInput.addEventListener('input', () => { applyViewerBackground(); requestDraw(); });
}
if (compareLayoutInput) compareLayoutInput.addEventListener('change', () => {
  pushHistory();
  geometryDirty = true;
  if (isOverlayLayout() && !orthographic) statusEl.textContent = overlayPerspectiveWarning();
  fitViewToModels();
  draw();
});
viewYawSlider.addEventListener('input', setYawFromSlider);
invertPitchInput.addEventListener('change', () => { pushHistory(); draw(); });
invertYawInput.addEventListener('change', () => { pushHistory(); draw(); });
showPrimaryModelInput.addEventListener('change', () => {
  pushHistory();
  primaryVisible = showPrimaryModelInput.checked && !!mesh;
  geometryDirty = true;
  updateModelControls();
  draw();
});
showCompareModelInput.addEventListener('change', () => {
  pushHistory();
  compareVisible = showCompareModelInput.checked && !!compareMesh;
  geometryDirty = true;
  updateModelControls();
  draw();
});
if (undoButton) undoButton.addEventListener('click', undoContour);
if (redoButton) redoButton.addEventListener('click', redoContour);
deletePrimaryModelButton.addEventListener('click', deletePrimaryModel);
deleteCompareModelButton.addEventListener('click', deleteCompareModel);
if (brandSoundButton) brandSoundButton.addEventListener('click', resetFromBrandDuck);
if (doodlePenButton) doodlePenButton.addEventListener('click', () => setDoodleMode('pen'));
if (doodleCircleButton) doodleCircleButton.addEventListener('click', () => setDoodleMode('circle'));
if (doodleClearButton) doodleClearButton.addEventListener('click', clearDoodles);
if (doodleColorInput) doodleColorInput.addEventListener('input', event => { doodleColor = event.target.value || '#2f80ed'; updateDoodleColorControl(); });
if (languageToggleButton) languageToggleButton.addEventListener('click', switchLanguage);

for (const button of lineTargetButtons) {
  button.addEventListener('click', () => {
    if (button.disabled) return;
    activeLineTarget = button.dataset.target;
    activeIndex = Math.min(activeIndex, Math.max(currentLines().length - 1, 0));
    updateLineTargetTabs();
    renderLineList();
    draw();
  });
}


document.getElementById('compareReportButton').onclick = () => generateCompareReport().catch(err => resultStatus.textContent = err.message);
if (exportSilhouetteButton) exportSilhouetteButton.onclick = exportSilhouettePng;
document.getElementById('meshFile').addEventListener('change', () => loadModel().catch(err => statusEl.textContent = err.message));
document.getElementById('compareMeshFile').addEventListener('change', () => loadCompareModel().catch(err => statusEl.textContent = err.message));
document.getElementById('linesFile').addEventListener('change', () => { pushHistory(); loadLines().then(() => draw()).catch(err => statusEl.textContent = err.message); });

function paintExportMasks(exportCtx, exportCanvas) {
  // Geometry masks are already applied during silhouette rendering.
}

function drawCleanSilhouetteExport(exportCanvas) {
  const exportCtx = exportCanvas.getContext('2d');
  exportCtx.fillStyle = viewerBackgroundColor();
  exportCtx.fillRect(0, 0, exportCanvas.width, exportCanvas.height);

  const previousMode = renderModeInput.value;
  const previousSuppressFloorGrid = suppressFloorGrid;
  try {
    suppressFloorGrid = true;
    renderModeInput.value = 'silhouette';
    geometryDirty = true;
    applyViewerBackground();
    renderThreeScene();
    exportCtx.drawImage(glCanvas, 0, 0);
  } finally {
    suppressFloorGrid = previousSuppressFloorGrid;
    renderModeInput.value = previousMode;
    geometryDirty = true;
    draw();
  }
  paintExportMasks(exportCtx, exportCanvas);
}

function exportSilhouettePng() {
  if (!mesh) {
    resultStatus.textContent = textFor('exportNoModel');
    return;
  }
  const exportCanvas = document.createElement('canvas');
  exportCanvas.width = glCanvas.width;
  exportCanvas.height = glCanvas.height;
  drawCleanSilhouetteExport(exportCanvas);
  exportCanvas.toBlob(blob => {
    if (!blob) {
      resultStatus.textContent = textFor('pngExportFailed');
      return;
    }
    if (silhouetteExportUrl) URL.revokeObjectURL(silhouetteExportUrl);
    silhouetteExportUrl = URL.createObjectURL(blob);
    const name = fileBaseName('meshFile', 'meshPath', 'main') + '_silhouette_' + new Date().toISOString().slice(0,19).replace(/[:T]/g,'-') + '.png';
    outputImage.src = silhouetteExportUrl;
    outputImage.style.display = 'block';
    outputLinks.innerHTML = '<a href="' + silhouetteExportUrl + '" target="_blank">' + textFor('silhouettePngLink') + '</a>';
    resultStatus.textContent = textFor('silhouetteExported');
  }, 'image/png');
}
function attachDisplayScaleFields(data) {
  const primaryScale = displayScaleForTarget('primary');
  const compareScale = displayScaleForTarget('compare');
  data.set('primary_display_scale', primaryScale.toFixed(8));
  data.set('compare_display_scale', compareScale.toFixed(8));
  const primaryHeight = displayHeightForTarget('primary');
  const compareHeight = displayHeightForTarget('compare');
  data.set('display_height_cm', primaryHeight ? primaryHeight.toFixed(4) : '');
  data.set('compare_display_height_cm', compareHeight ? compareHeight.toFixed(4) : '');
}

async function generateCompareReport() {
  if (!mesh || !compareMesh) throw new Error(textFor('needBothModels'));
  const button = document.getElementById('compareReportButton');
  button.disabled = true;
  resultStatus.textContent = textFor('compareGenerating');
  try {
    const data = new FormData(form);
    attachDisplayScaleFields(data);
    data.append('compare_mode', '1');
    data.append('primary_lines_text', csvFromVisibleLinesOrThrow(lines));
    data.append('compare_lines_text', csvFromVisibleLinesOrThrow(compareLines.length ? compareLines : cloneLinesForMesh(lines, mesh, compareMesh)));
    const compareFile = document.getElementById('compareMeshFile').files[0];
    if (compareFile) data.append('compare_mesh_file', compareFile);
    data.append('compare_mesh_path', document.getElementById('compareMeshPath').value.trim());
    data.append('primary_name', fileBaseName('meshFile', 'meshPath', 'main'));
    data.append('compare_name', fileBaseName('compareMeshFile', 'compareMeshPath', 'compare'));
    const res = await fetch('/api/generate', {method:'POST', body:data});
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || textFor('compareFailed'));
    resultStatus.textContent = textFor('compareComplete');
    outputImage.src = payload.png_url + '?t=' + Date.now();
    outputImage.style.display = 'block';
    outputLinks.innerHTML = `<a href="${payload.png_url}" target="_blank">PNG</a><a href="${payload.json_url}" target="_blank">JSON</a>`;
    resultTable.innerHTML = '<thead><tr><th>' + textFor('tableLine') + '</th><th>' + textFor('tablePrimary') + '</th><th>' + textFor('tableCompare') + '</th><th>' + textFor('tableDiff') + '</th></tr></thead><tbody>' +
      payload.sections.map(s => `<tr><td>${s.label || s.name}</td><td>${s.primary_cm.toFixed(2)}</td><td>${s.compare_cm.toFixed(2)}</td><td>${s.diff_cm.toFixed(2)}</td></tr>`).join('') +
      '</tbody>';
  } finally {
    button.disabled = false;
  }
}
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = document.getElementById('runButton');
  button.disabled = true; resultStatus.textContent = textFor('generating');
  try {
    const data = new FormData(form);
    attachDisplayScaleFields(data);
    data.append('lines_text', csvFromVisibleLinesOrThrow());
    const res = await fetch('/api/generate', {method:'POST', body:data});
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || textFor('generateFailed'));
    resultStatus.textContent = textFor('done');
    outputImage.src = payload.png_url + '?t=' + Date.now();
    outputImage.style.display = 'block';
    outputLinks.innerHTML = `<a href="${payload.png_url}" target="_blank">PNG</a><a href="${payload.json_url}" target="_blank">JSON</a>`;
    resultTable.innerHTML = '<thead><tr><th>' + textFor('tableLine') + '</th><th>' + textFor('tablePerimeter') + '</th><th>' + textFor('tableHeight') + '</th></tr></thead><tbody>' +
      payload.sections.map(s => `<tr><td>${s.label || s.name}</td><td>${s.perimeter_cm.toFixed(2)}</td><td>${s.height_from_floor_cm.toFixed(2)}</td></tr>`).join('') +
      '</tbody>';
  } catch (err) {
    resultStatus.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

initResizablePanels();
statusEl.textContent = '';
syncYawSlider();
updateModelControls();
updateDoodleButtons();
updateDoodleColorControl();
applyLanguage();
</script>
</body>
</html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            data = html_page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/shared":
            self.send_response(302)
            self.send_header("Location", "/shared/")
            self.end_headers()
            return
        if parsed.path == "/shared/" or parsed.path.startswith("/shared/"):
            return self.serve_shared_file(parsed.path)
        if parsed.path in {"/asset/IconAhiru.png", "/favicon.ico"}:
            return self.serve_file(DEFAULT_LOGO)
        if parsed.path == "/asset/duck-quacking-37392.mp3":
            return self.serve_file(QUACK_SOUND)
        if parsed.path == "/asset/three.min.js":
            return self.serve_file(VENDOR / "three.min.js")
        if parsed.path.startswith("/outputs/"):
            return self.serve_file(WEB_OUTPUTS / unquote(parsed.path.removeprefix("/outputs/")))
        if parsed.path == "/api/health":
            json_response(self, {
                "ok": True,
                "backend": "python",
                "features": {
                    "meshPreview": True,
                    "lineTemplates": True,
                    "topViewGeneration": True,
                    "compareGeneration": True,
                },
            })
            return
        if parsed.path == "/api/mesh":
            try:
                params = parse_qs(parsed.query)
                path = resolve_input_path(params.get("path", [""])[0])
                if not path.exists():
                    raise FileNotFoundError(path)
                json_response(self, parse_obj_preview(path))
            except Exception as exc:
                json_response(self, {"error": str(exc)}, 500)
            return
        if parsed.path == "/api/lines":
            try:
                params = parse_qs(parsed.query)
                path = resolve_input_path(params.get("path", [""])[0])
                if not path.exists():
                    raise FileNotFoundError(path)
                json_response(self, {"lines": load_line_rows(path)})
            except Exception as exc:
                json_response(self, {"error": str(exc)}, 500)
            return
        json_response(self, {"error": "not found"}, 404)

    def serve_shared_file(self, request_path: str) -> None:
        relative = unquote(request_path.removeprefix("/shared/"))
        if not relative:
            relative = "index.html"
        normalized = Path(relative)
        allowed_roots = {"index.html", "web", "assets", "vendor"}
        if normalized.parts and normalized.parts[0] not in allowed_roots:
            json_response(self, {"error": "not found"}, 404)
            return
        path = (ROOT / normalized).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError:
            json_response(self, {"error": "not found"}, 404)
            return
        if path.is_dir():
            path = path / "index.html"
        return self.serve_file(path)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/generate":
            json_response(self, {"error": "not found"}, 404)
            return
        try:
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            })
            if form.getfirst("compare_mode", "") == "1":
                mesh_path = make_mesh_path_from_form(form, file_key="mesh_file", path_key="mesh_path")
                compare_mesh_path = make_mesh_path_from_form(form, file_key="compare_mesh_file", path_key="compare_mesh_path")
                primary_lines_path = make_lines_path_from_form(
                    form,
                    text_key="primary_lines_text",
                    file_key="lines_file",
                    path_key="lines_path",
                    fallback_prefix="primary_compare_lines",
                )
                compare_lines_path = make_lines_path_from_form(
                    form,
                    text_key="compare_lines_text",
                    file_key="compare_lines_file",
                    path_key="compare_lines_path",
                    fallback_prefix="secondary_compare_lines",
                )
                primary_display = form_float(form, "display_height_cm")
                compare_display = form_float(form, "compare_display_height_cm")
                primary_scale = display_scale_from_form(form, "primary_display_scale")
                compare_scale = display_scale_from_form(form, "compare_display_scale")
                primary_name = safe_name(form.getfirst("primary_name", mesh_path.stem), mesh_path.stem)
                compare_name = safe_name(form.getfirst("compare_name", compare_mesh_path.stem), compare_mesh_path.stem)
                primary_config = apply_interactive_generation_limits(config_from_lines(mesh_path, primary_lines_path, primary_name, primary_display))
                compare_config = apply_interactive_generation_limits(config_from_lines(compare_mesh_path, compare_lines_path, compare_name, compare_display))

                a_vertices, a_faces, _ = compare_avatar_sections.meshcut.load_mesh(mesh_path)
                b_vertices, b_faces, _ = compare_avatar_sections.meshcut.load_mesh(compare_mesh_path)
                primary_sections, primary_mesh = compare_avatar_sections.section_tool.build_sections(a_vertices, a_faces, primary_config)
                compare_sections, compare_mesh = compare_avatar_sections.section_tool.build_sections(b_vertices, b_faces, compare_config)
                primary_sections = scale_section_measurements(primary_sections, primary_scale)
                compare_sections = scale_section_measurements(compare_sections, compare_scale)
                if primary_display is not None:
                    primary_mesh["display_height_cm"] = primary_display
                elif abs(primary_scale - 1.0) >= 0.0001:
                    primary_mesh["display_height_cm"] = float(primary_mesh["height_cm"]) * primary_scale
                if compare_display is not None:
                    compare_mesh["display_height_cm"] = compare_display
                elif abs(compare_scale - 1.0) >= 0.0001:
                    compare_mesh["display_height_cm"] = float(compare_mesh["height_cm"]) * compare_scale

                stem = safe_name(form.getfirst("output_stem", f"{primary_name}_vs_{compare_name}"), "compare_sections")
                stamp = time.strftime("%Y%m%d_%H%M%S")
                out_png = WEB_OUTPUTS / f"{stem}_compare_{stamp}.png"
                compare_avatar_sections.render_comparison(
                    out_png,
                    primary_sections,
                    primary_mesh,
                    compare_sections,
                    compare_mesh,
                    DEFAULT_LOGO if DEFAULT_LOGO.exists() else None,
                    primary_name,
                    compare_name,
                )
                out_json = out_png.with_suffix(".json")
                report = json.loads(out_json.read_text(encoding="utf-8"))
                rows = []
                diff_key = f"diff_{compare_name}_minus_{primary_name}_cm"
                for section in report.get("sections", []):
                    primary_data = section.get(primary_name, {})
                    compare_data = section.get(compare_name, {})
                    rows.append({
                        "name": section.get("name", ""),
                        "label": section.get("label", section.get("name", "")),
                        "primary_cm": float(primary_data.get("perimeter_cm", 0)),
                        "compare_cm": float(compare_data.get("perimeter_cm", 0)),
                        "diff_cm": float(section.get(diff_key, 0)),
                    })
                json_response(self, {
                    "png_url": f"/outputs/{out_png.name}",
                    "json_url": f"/outputs/{out_json.name}",
                    "primary_name": primary_name,
                    "compare_name": compare_name,
                    "sections": rows,
                })
                return

            mesh_upload = save_upload(form["mesh_file"] if "mesh_file" in form else None, UPLOADS)
            mesh_path = mesh_upload or resolve_input_path(form.getfirst("mesh_path", ""))
            if not mesh_path.exists():
                raise FileNotFoundError(f"mesh not found: {mesh_path}")
            mesh_path = ensure_obj_path(mesh_path)

            lines_upload = save_upload(form["lines_file"] if "lines_file" in form else None, UPLOADS)
            lines_text = form.getfirst("lines_text", "").strip()
            if lines_text:
                UPLOADS.mkdir(parents=True, exist_ok=True)
                lines_path = UPLOADS / f"interactive_lines_{int(time.time())}.csv"
                lines_path.write_text(lines_text, encoding="utf-8")
            else:
                lines_path = lines_upload or resolve_input_path(form.getfirst("lines_path", ""))
            if not lines_path.exists():
                raise FileNotFoundError(f"lines not found: {lines_path}")

            stem = safe_name(form.getfirst("output_stem", mesh_path.stem), "section_output")
            stamp = time.strftime("%Y%m%d_%H%M%S")
            out_png = WEB_OUTPUTS / f"{stem}_{stamp}.png"
            out_json = WEB_OUTPUTS / f"{stem}_{stamp}.json"
            display_height = form_float(form, "display_height_cm")
            primary_scale = display_scale_from_form(form, "primary_display_scale")
            args = SimpleNamespace(
                mesh=str(mesh_path),
                lines=str(lines_path),
                title=form.getfirst("title", "").strip() or mesh_path.stem,
                unit_scale=1.0,
                display_height_cm=display_height,
            )
            config = apply_interactive_generation_limits(obj_section_tool.make_config(args))
            vertices, faces, loader = obj_section_tool.meshcut.load_mesh(mesh_path)
            raw_sections, mesh_report = obj_section_tool.overlay.build_sections(vertices, faces, config)
            sections_for_output = scale_section_measurements(raw_sections, primary_scale)
            if display_height is not None:
                mesh_report["display_height_cm"] = display_height
            elif abs(primary_scale - 1.0) >= 0.0001:
                mesh_report["display_height_cm"] = float(mesh_report["height_cm"]) * primary_scale
            obj_section_tool.overlay.render_png(
                out_png,
                config["title"],
                sections_for_output,
                DEFAULT_LOGO if DEFAULT_LOGO.exists() else None,
                white_background=True,
                text_labels=config.get("labels"),
                show_rig_bone_points=False,
            )
            effective_display_height = mesh_report.get("display_height_cm", display_height)
            report = {
                "source": str(mesh_path),
                "loader": loader,
                "mesh": mesh_report,
                "display_height_cm": effective_display_height,
                "height_reference": "OBJ Y axis; floor is mesh min Y after unit scaling",
                "title": config["title"],
                "png": str(out_png),
                "sections": obj_section_tool.overlay.strip_points(sections_for_output, include_rig_bone=False),
            }
            out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            sections = [
                {
                    "name": section["name"],
                    "label": section.get("label", section["name"]),
                    "perimeter_cm": round(float(section["summary"]["perimeter"]), 2),
                    "height_from_floor_cm": round(float(section["height_from_floor_cm"]), 2),
                }
                for section in report["sections"]
            ]
            json_response(self, {
                "png_url": f"/outputs/{out_png.name}",
                "json_url": f"/outputs/{out_json.name}",
                "height_cm": round(float(report["mesh"]["height_cm"]), 2),
                "display_height_cm": report.get("display_height_cm"),
                "sections": sections,
            })
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            json_response(self, {"error": "not found"}, 404)
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix.lower() == ".json":
            content_type = "application/json; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        print(f"[section-interactive] {self.address_string()} - {format % args}", flush=True)


def main() -> None:
    WEB_OUTPUTS.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Interactive OBJ section tool: http://127.0.0.1:8765", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
