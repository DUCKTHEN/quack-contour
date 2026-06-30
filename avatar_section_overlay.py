"""Create top-view section overlay PNGs from avatar OBJ/ASCII FBX meshes.

This generalizes the 9AR experiments: load an avatar mesh, slice horizontal
sections, optionally scan for target circumferences, draw section contours, and
write a JSON measurement report.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import measure_avatar_bust as meshcut


DEFAULT_COLORS = [
    "#6F4E37",
    "#8D4ED8",
    "#C00028",
    "#E67E22",
    "#0077B6",
    "#2E8B57",
    "#1E1E1E",
    "#7A7A7A",
]


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def mesh_bounds(vertices) -> dict[str, list[float]]:
    mins = vertices.min(axis=0)
    maxs = vertices.max(axis=0)
    return {
        "min": [float(value) for value in mins],
        "max": [float(value) for value in maxs],
    }


def height_from_section(section: dict[str, Any], floor: float, body_height: float) -> float:
    if "height_from_floor_cm" in section:
        return floor + float(section["height_from_floor_cm"])
    if "height_ratio" in section:
        return floor + body_height * float(section["height_ratio"])
    raise ValueError(f"Section {section.get('name', '<unnamed>')} needs height_from_floor_cm or height_ratio")


def summarize_loop(loop: dict[str, object]) -> dict[str, Any]:
    return {
        "perimeter": float(loop["perimeter"]),
        "closed": bool(loop["closed"]),
        "point_count": int(loop["point_count"]),
        "centroid": [float(value) for value in loop["centroid"]],  # type: ignore[index]
        "bbox": [float(value) for value in loop["bbox"]],  # type: ignore[index]
    }


def choose_section_loop(
    vertices,
    faces,
    height: float,
    expected: float | None,
    select: str,
    eps: float,
    join_tolerance: float,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    segments = meshcut.slice_mesh(vertices, faces, "y", height, eps)
    paths = meshcut.build_paths(segments, join_tolerance)
    selected = meshcut.select_loop(paths, select, expected)
    if selected is None:
        raise ValueError(f"No section loop found at height {height:.3f}")
    return selected, paths


def scan_for_section(
    vertices,
    faces,
    base_height: float,
    section: dict[str, Any],
    eps: float,
    join_tolerance: float,
) -> tuple[float, dict[str, object], list[dict[str, object]], list[dict[str, Any]]]:
    expected = section.get("expected_cm")
    select = section.get("select", "largest")
    if expected is None or not section.get("scan", True):
        selected, paths = choose_section_loop(vertices, faces, base_height, expected, select, eps, join_tolerance)
        return base_height, selected, paths, []

    expected = float(expected)
    scan_range = section.get("scan_range_cm")
    if scan_range:
        start, end = float(scan_range[0]), float(scan_range[1])
    else:
        around = float(section.get("scan_around_cm", 4.0))
        start, end = base_height - around, base_height + around
    step = float(section.get("scan_step_cm", 0.1))
    mode = section.get("scan_select", "closest")

    records: list[dict[str, Any]] = []
    best: tuple[float, dict[str, object], list[dict[str, object]]] | None = None
    count = max(0, int(round((end - start) / step)))
    for index in range(count + 1):
        height = round(start + index * step, 5)
        try:
            selected, paths = choose_section_loop(vertices, faces, height, expected, mode, eps, join_tolerance)
        except ValueError:
            continue
        diff = abs(float(selected["perimeter"]) - expected)
        record = {
            "height_from_floor_cm": height,
            "perimeter": float(selected["perimeter"]),
            "diff": diff,
            "loop_count": len(paths),
        }
        records.append(record)
        if best is None or diff < abs(float(best[1]["perimeter"]) - expected):
            best = (height, selected, paths)

    if best is None:
        selected, paths = choose_section_loop(vertices, faces, base_height, expected, "closest", eps, join_tolerance)
        return base_height, selected, paths, records
    return best[0], best[1], best[2], sorted(records, key=lambda item: item["diff"])[:10]


def spine_estimate(summary: dict[str, Any], ratio_from_back: float) -> dict[str, Any]:
    bbox = summary["bbox"]
    back_z = float(bbox[1])
    front_z = float(bbox[3])
    z = back_z + ratio_from_back * (front_z - back_z)
    return {
        "x": 0.0,
        "z": z,
        "back_z": back_z,
        "front_z": front_z,
        "method": f"X=0 and {ratio_from_back:.2f} of section depth forward from posterior surface",
    }


def find_font(name: str, size: int):
    candidates = [
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\meiryob.ttc" if "bd" in name.lower() else name,
        name,
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_white_background(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if a and r >= 246 and g >= 244 and b >= 238:
                pixels[x, y] = (255, 255, 255, a)
    canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    canvas.alpha_composite(rgba)
    return canvas


def paste_logo(canvas: Image.Image, logo_path: Path, size: int, margin: int) -> None:
    logo = Image.open(logo_path).convert("RGBA")
    pixels = logo.load()
    for y in range(logo.height):
        for x in range(logo.width):
            r, g, b, a = pixels[x, y]
            if r < 8 and g < 8 and b < 8:
                pixels[x, y] = (0, 0, 0, 0)
    bbox = logo.getbbox()
    if bbox is not None:
        logo = logo.crop(bbox)
    logo.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, (canvas.width - logo.width - margin, margin))


def draw_dashed(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], color: str, width: int = 2) -> None:
    for a, b in zip(points, points[1:]):
        x1, y1 = a
        x2, y2 = b
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 0:
            continue
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        t = 0.0
        while t < length:
            t2 = min(t + 10, length)
            draw.line([(x1 + ux * t, y1 + uy * t), (x1 + ux * t2, y1 + uy * t2)], fill=color, width=width)
            t += 18


def render_png(
    output_path: Path,
    title: str,
    sections: list[dict[str, Any]],
    logo_path: Path | None,
    white_background: bool,
    text_labels: dict[str, str] | None = None,
    show_rig_bone_points: bool = True,
) -> None:
    width = max(1700, int(1580 + 42 * len(sections)))
    height = max(1020, int(820 + 52 * len(sections)))
    background = "#ffffff" if white_background else "#fbfaf7"
    labels = {
        "front": "Front",
        "back": "Back",
        "measured_loops": "Measured loops",
        "spine_points": "Estimated rig-bone points",
        "point_color_matches_section": "Point color matches section",
        "height_from_floor": "H = height from floor",
        "estimated_not_real_bone": "Estimated, not real rig bone",
        "footnote": "Original OBJ X/Z axes. Estimated rig-bone points are shape-based estimates unless skeleton data is provided.",
        "spine_z": "bone Z",
    }
    if text_labels:
        labels.update(text_labels)
    image = Image.new("RGBA", (width, height), background)
    draw = ImageDraw.Draw(image)

    font_title = find_font("arial.ttf", 34)
    font_label = find_font("arial.ttf", 22)
    font_small = find_font("arial.ttf", 19)
    font_bold = find_font("arialbd.ttf", 23)
    font_panel = find_font("arialbd.ttf", 26)

    plot_x0, plot_y0 = 90, 145
    legend_x = width - 620
    plot_w = legend_x - 160
    plot_h = height - 330
    panel_top = plot_y0 - 22
    panel_bottom = plot_y0 + plot_h + 22
    legend_y = panel_top
    legend_w = 540
    legend_h = panel_bottom - panel_top

    all_points = [point for section in sections for point in section["points"]]
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_z = min(point[1] for point in all_points)
    max_z = max(point[1] for point in all_points)
    min_x = min(min_x, 0.0)
    max_x = max(max_x, 0.0)
    min_z = min(min_z, 0.0)
    max_z = max(max_z, 0.0)
    margin_x = (max_x - min_x) * 0.10
    margin_z = (max_z - min_z) * 0.16
    min_x -= margin_x
    max_x += margin_x
    min_z -= margin_z
    max_z += margin_z
    scale = min(plot_w / max(max_x - min_x, 1e-6), plot_h / max(max_z - min_z, 1e-6))
    ox = plot_x0 + plot_w / 2 - ((min_x + max_x) / 2) * scale
    oy = plot_y0 + plot_h / 2 + ((min_z + max_z) / 2) * scale

    def tr(point: tuple[float, float]) -> tuple[float, float]:
        x, z = point
        return ox + x * scale, oy - z * scale

    def poly(points: list[tuple[float, float]], color: str, line_width: int = 4) -> None:
        mapped = [tr(point) for point in points]
        draw.line(mapped, fill=color, width=line_width, joint="curve")
        if len(mapped) > 2:
            draw.line([mapped[-1], mapped[0]], fill=color, width=line_width)

    draw.text((width / 2, 66), title, fill="#333333", font=font_title, anchor="mm")
    draw.rounded_rectangle(
        [plot_x0 - 22, plot_y0 - 22, plot_x0 + plot_w + 22, plot_y0 + plot_h + 22],
        radius=14,
        fill="#ffffff",
        outline="#dad7d0",
        width=2,
    )
    draw.line([tr((0, min_z)), tr((0, max_z))], fill="#7b7b7b", width=2)
    draw.line([tr((min_x, 0)), tr((max_x, 0))], fill="#7b7b7b", width=2)
    front_axis = tr((0, max_z))
    back_axis = tr((0, min_z))
    draw.text((front_axis[0], front_axis[1] - 34), labels["front"], fill="#666666", font=font_label, anchor="mm")
    draw.text((back_axis[0], back_axis[1] + 30), labels["back"], fill="#666666", font=font_label, anchor="mm")
    draw.text((tr((0, max_z))[0] + 14, tr((0, max_z))[1] + 8), "X=0", fill="#666666", font=font_small)
    draw.text((tr((min_x, 0))[0] + 8, tr((min_x, 0))[1] - 28), "Z=0", fill="#666666", font=font_small)

    for section in sorted(sections, key=lambda item: float(item["summary"]["perimeter"]), reverse=True):
        poly(section["points"], section["color"], 5 if section["name"].lower() == "neck" else 4)

    if show_rig_bone_points:
        spine_points = [tr((0.0, section["spine_estimate"]["z"])) for section in sections]
        draw_dashed(draw, spine_points, "#8A8A8A", width=2)
        for section in sections:
            px, py = tr((0.0, section["spine_estimate"]["z"]))
            radius = 8
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=section["color"], outline="#ffffff", width=2)
            draw.ellipse([px - radius - 2, py - radius - 2, px + radius + 2, py + radius + 2], outline="#4d4d4d", width=1)

    draw.rounded_rectangle([legend_x, legend_y, legend_x + legend_w, legend_y + legend_h], radius=12, fill="#ffffff", outline="#ddd8ce", width=2)
    draw.text((legend_x + 28, legend_y + 34), labels["measured_loops"], fill="#333333", font=font_panel)
    y = legend_y + 82
    for section in sections:
        perim = section["summary"]["perimeter"]
        height_cm = section["height_from_floor_cm"]
        draw.line([(legend_x + 30, y + 10), (legend_x + 84, y + 10)], fill=section["color"], width=6)
        draw.text((legend_x + 100, y - 4), f'{section["label"]}: {perim:.2f} cm', fill=section["color"], font=font_bold)
        if show_rig_bone_points:
            spine_z = section["spine_estimate"]["z"]
            detail = f"H {height_cm:.2f} / {labels['spine_z']} {spine_z:+.2f}"
        else:
            detail = f"H {height_cm:.2f}"
        draw.text((legend_x + 100, y + 28), detail, fill="#565656", font=font_small)
        y += 66

    y += 12
    if show_rig_bone_points:
        draw.text((legend_x + 28, y), labels["spine_points"], fill="#333333", font=font_panel)
        y += 45
        x = legend_x + 42
        for section in sections:
            draw.ellipse([x - 8, y + 4, x + 8, y + 20], fill=section["color"], outline="#4d4d4d", width=1)
            x += 38
        draw.text((legend_x + 30, y + 42), labels["point_color_matches_section"], fill="#565656", font=font_small)
        y += 78
    draw.text((legend_x + 30, y), labels["height_from_floor"], fill="#565656", font=font_small)
    if show_rig_bone_points:
        y += 30
        draw.text((legend_x + 30, y), labels["estimated_not_real_bone"], fill="#565656", font=font_small)

    draw.text((plot_x0 - 2, height - 72), labels["footnote"], fill="#666666", font=font_small)

    if white_background:
        image = make_white_background(image)
    if logo_path is not None:
        paste_logo(image, logo_path, size=82, margin=42)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, quality=95)


def build_sections(vertices, faces, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    unit_scale = float(config.get("unit_scale", 1.0))
    vertices = vertices * unit_scale
    bounds = mesh_bounds(vertices)
    floor = bounds["min"][1]
    top = bounds["max"][1]
    body_height = top - floor
    eps = float(config.get("eps", 1e-5))
    join_tolerance = float(config.get("join_tolerance", 1e-4))
    spine_ratio = float(config.get("spine_from_back_ratio", 0.28))
    sections: list[dict[str, Any]] = []

    for index, section_config in enumerate(config["sections"]):
        section = dict(section_config)
        section.setdefault("name", f"section_{index + 1}")
        section.setdefault("label", section["name"].replace("_", " ").title())
        section.setdefault("color", DEFAULT_COLORS[index % len(DEFAULT_COLORS)])
        expected = section.get("expected_cm")
        if expected is not None:
            section["expected_cm"] = float(expected)
        select = section.get("select")
        if select is None:
            section["select"] = "closest" if expected is not None else "largest"
        base_height = height_from_section(section, floor, body_height)
        height, selected, paths, scan_best = scan_for_section(vertices, faces, base_height, section, eps, join_tolerance)
        summary = summarize_loop(selected)
        section.update(
            {
                "height_from_floor_cm": height - floor,
                "mesh_y": height,
                "summary": summary,
                "spine_estimate": spine_estimate(summary, spine_ratio),
                "loop_count": len(paths),
                "points": selected["points"],
                "scan_best": scan_best,
            }
        )
        sections.append(section)

    report_mesh = {
        "bounds": bounds,
        "floor_y": floor,
        "top_y": top,
        "height_cm": body_height,
        "unit_scale": unit_scale,
        "vertices": int(vertices.shape[0]),
        "faces": int(faces.shape[0]),
    }
    return sections, report_mesh


def strip_points(sections: list[dict[str, Any]], include_rig_bone: bool = True) -> list[dict[str, Any]]:
    cleaned = []
    for section in sections:
        copied = {key: value for key, value in section.items() if key != "points"}
        copied["summary"] = dict(copied["summary"])
        copied["summary"]["perimeter_cm_2dp"] = round(float(copied["summary"]["perimeter"]), 2)
        rig_bone = dict(copied.pop("spine_estimate"))
        if include_rig_bone:
            rig_bone["z_2dp"] = round(float(rig_bone["z"]), 2)
            copied["rig_bone_estimate"] = rig_bone
        cleaned.append(copied)
    return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description="Render avatar horizontal section overlay from OBJ/ASCII FBX.")
    parser.add_argument("mesh", help="OBJ or ASCII FBX avatar mesh")
    parser.add_argument("--config", help="Section configuration JSON")
    parser.add_argument("--out-png", required=True, help="Output PNG path")
    parser.add_argument("--out-json", help="Output measurement JSON path")
    parser.add_argument("--title", help="Override title")
    parser.add_argument("--logo", help="Optional top-right logo image")
    parser.add_argument("--no-white-background", action="store_true", help="Keep warm background instead of white")
    args = parser.parse_args()

    config = load_config(Path(args.config) if args.config else None)
    if "sections" not in config:
        raise SystemExit("Config must define a sections array.")

    mesh_path = Path(args.mesh)
    vertices, faces, loader = meshcut.load_mesh(mesh_path)
    sections, mesh_report = build_sections(vertices, faces, config)
    title = args.title or config.get("title") or f"{mesh_path.stem}: measured top-view sections + estimated rig-bone line"
    out_png = Path(args.out_png)
    logo_path = Path(args.logo) if args.logo else (Path(config["logo"]) if config.get("logo") else None)
    show_rig_bone_points = bool(config.get("show_rig_bone_points", True))
    render_png(out_png, title, sections, logo_path, not args.no_white_background, config.get("labels"), show_rig_bone_points)

    report = {
        "source": str(mesh_path),
        "loader": loader,
        "mesh": mesh_report,
        "height_reference": "OBJ Y axis; floor is mesh min Y after unit scaling",
        "bone_data_found_in_obj": False,
        "show_rig_bone_points": show_rig_bone_points,
        "title": title,
        "png": str(out_png),
        "sections": strip_points(sections, show_rig_bone_points),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out_json:
        Path(args.out_json).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
