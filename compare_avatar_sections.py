"""Render a readable top-view comparison between two avatar section reports.

The two avatars are sliced with the same section logic used by
avatar_section_overlay.py. Output is a small-multiple comparison: avatar A is
solid, avatar B is dashed, with each section panel scaled to its two contours.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import avatar_section_overlay as section_tool  # noqa: E402
import measure_avatar_bust as meshcut  # noqa: E402

TITLE_SUFFIX = "\u4e0a\u9762\u65ad\u9762\u6bd4\u8f03"
HEIGHT_TEXT = "\u8eab\u9577"
SOLID_TEXT = "\u5b9f\u7dda"
DASHED_TEXT = "\u70b9\u7dda"
DIFF_TEXT = "\u5dee"
SECTION_HEIGHT_TEXT = "\u9ad8\u3055"
ALL_SECTIONS_TEXT = "\u5168\u65ad\u9762"
SAME_AXIS_TEXT = "\u5404\u65ad\u9762\u3092\u540c\u3058X/Z\u8ef8\u3067\u91cd\u306d\u8868\u793a"
FOOTNOTE_TEXT = (
    "\u90e8\u4f4d\u5225\u30d1\u30cd\u30eb\u306f\u3001\u305d\u306e\u90e8\u4f4d\u306e2\u672c\u306e\u8f2a\u90ed\u304c\u898b\u3084\u3059\u3044\u3088\u3046\u500b\u5225\u30b9\u30b1\u30fc\u30eb\u3067\u8868\u793a\u3002"
    "\u53f3\u4e0b\u306e\u5168\u65ad\u9762\u56f3\u306f\u5404OBJ\u5185\u3067\u540c\u3058X/Z\u8ef8\u306b\u91cd\u306d\u3066\u3044\u307e\u3059\u3002"
)


def draw_dashed_path(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: str,
    width: int,
    dash: float = 9.0,
    gap: float = 6.5,
) -> None:
    # Keep dash phase continuous across many tiny OBJ contour segments.
    period = dash + gap
    distance = 0.0
    for a, b in zip(points, points[1:]):
        x1, y1 = a
        x2, y2 = b
        seg_len = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if seg_len <= 0:
            continue
        ux, uy = (x2 - x1) / seg_len, (y2 - y1) / seg_len
        t = 0.0
        while t < seg_len:
            phase = (distance + t) % period
            if phase < dash:
                run = min(dash - phase, seg_len - t)
                draw.line([(x1 + ux * t, y1 + uy * t), (x1 + ux * (t + run), y1 + uy * (t + run))], fill=color, width=width)
            else:
                run = min(period - phase, seg_len - t)
            t += max(run, 0.01)
        distance += seg_len


def polyline(draw: ImageDraw.ImageDraw, points, transform, color: str, width: int, dashed: bool) -> None:
    mapped = [transform(point) for point in points] + [transform(points[0])]
    if dashed:
        draw_dashed_path(draw, mapped, color, width)
    else:
        draw.line(mapped, fill=color, width=width, joint="curve")


def draw_axes(
    draw: ImageDraw.ImageDraw,
    transform,
    plot_bounds: tuple[float, float, float, float],
    *,
    width: int = 2,
) -> None:
    left, top, right, bottom = plot_bounds
    axis_color = "#9A9A9A"
    axis_x = transform((0.0, 0.0))[0]
    axis_z = transform((0.0, 0.0))[1]
    if left <= axis_x <= right:
        draw.line([(axis_x, top), (axis_x, bottom)], fill=axis_color, width=width)
    if top <= axis_z <= bottom:
        draw.line([(left, axis_z), (right, axis_z)], fill=axis_color, width=width)


def fit_transform(
    point_sets,
    panel: tuple[float, float, float, float],
    padding: tuple[float, float, float, float],
    *,
    axis_reference: bool = False,
):
    x0, y0, x1, y1 = panel
    pad_left, pad_top, pad_right, pad_bottom = padding
    all_points = [point for points in point_sets for point in points]
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_z = min(point[1] for point in all_points)
    max_z = max(point[1] for point in all_points)

    if axis_reference:
        min_x = min(min_x, 0.0)
        max_x = max(max_x, 0.0)
        min_z = min(min_z, 0.0)
        max_z = max(max_z, 0.0)
        extent_x = max(abs(min_x), abs(max_x), 1e-6)
        extent_z = max(abs(min_z), abs(max_z), 1e-6)
        min_x, max_x = -extent_x, extent_x
        min_z, max_z = -extent_z, extent_z

    span_x = max(max_x - min_x, 1e-6)
    span_z = max(max_z - min_z, 1e-6)
    plot_left = x0 + pad_left
    plot_top = y0 + pad_top
    plot_right = x1 - pad_right
    plot_bottom = y1 - pad_bottom
    scale = min((plot_right - plot_left) / span_x, (plot_bottom - plot_top) / span_z)
    center_x = (plot_left + plot_right) / 2
    center_y = (plot_top + plot_bottom) / 2
    data_cx = (min_x + max_x) / 2
    data_cz = (min_z + max_z) / 2

    def transform(point):
        return (center_x + (point[0] - data_cx) * scale, center_y - (point[1] - data_cz) * scale)

    return transform, (plot_left, plot_top, plot_right, plot_bottom)


def load_avatar(mesh_path: Path, config_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    config = section_tool.load_config(config_path)
    vertices, faces, loader = meshcut.load_mesh(mesh_path)
    sections, mesh = section_tool.build_sections(vertices, faces, config)
    if "display_height_cm" in config:
        mesh["display_height_cm"] = float(config["display_height_cm"])
    return sections, mesh, loader


def display_height(mesh: dict[str, Any]) -> float:
    return float(mesh.get("display_height_cm", mesh["height_cm"]))


def section_map(sections: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {section["name"]: section for section in sections}


def panel_rect(margin: int, grid_top: int, panel_w: int, panel_h: int, gap_x: int, gap_y: int, col: int, row: int):
    x0 = margin + col * (panel_w + gap_x)
    y0 = grid_top + row * (panel_h + gap_y)
    return x0, y0, x0 + panel_w, y0 + panel_h


def render_comparison(
    out_png: Path,
    a_sections: list[dict[str, Any]],
    a_mesh: dict[str, Any],
    b_sections: list[dict[str, Any]],
    b_mesh: dict[str, Any],
    logo_path: Path | None,
    a_name: str,
    b_name: str,
) -> None:
    width, height = 1620, 1260
    margin = 64
    title_y = 42
    subtitle_y = 82
    legend_y = 120
    grid_top = 162
    panel_w = 470
    panel_h = 285
    gap_x = 32
    gap_y = 36
    cols = 3

    image = Image.new("RGBA", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    font_title = section_tool.find_font("meiryob.ttc", 36)
    font_subtitle = section_tool.find_font("meiryo.ttc", 22)
    font_panel = section_tool.find_font("meiryob.ttc", 24)
    font_text = section_tool.find_font("meiryo.ttc", 19)
    font_small = section_tool.find_font("meiryo.ttc", 17)
    font_tiny = section_tool.find_font("meiryo.ttc", 15)

    title_text = f"{a_name} / {b_name}\uff1a{TITLE_SUFFIX}"
    draw.text((width / 2, title_y), title_text, fill="#333333", font=font_title, anchor="mm")
    subtitle = (
        f"{a_name} {HEIGHT_TEXT} {display_height(a_mesh):.2f} cm\uff08{SOLID_TEXT}\uff09 / "
        f"{b_name} {HEIGHT_TEXT} {display_height(b_mesh):.2f} cm\uff08{DASHED_TEXT}\uff09"
    )
    draw.text((width / 2, subtitle_y), subtitle, fill="#555555", font=font_subtitle, anchor="mm")

    legend_x = margin
    draw.line([(legend_x, legend_y), (legend_x + 92, legend_y)], fill="#333333", width=5)
    draw.text((legend_x + 108, legend_y - 14), f"{a_name}\uff08{SOLID_TEXT}\uff09", fill="#333333", font=font_text)
    section_tool.draw_dashed(draw, [(legend_x + 280, legend_y), (legend_x + 372, legend_y)], "#333333", width=5)
    draw.text((legend_x + 388, legend_y - 14), f"{b_name}\uff08{DASHED_TEXT}\uff09", fill="#333333", font=font_text)

    if logo_path is not None and logo_path.exists():
        section_tool.paste_logo(image, logo_path, size=70, margin=34)

    b_by_name = section_map(b_sections)
    comparison: list[dict[str, Any]] = []

    for index, a_section in enumerate(a_sections):
        name = a_section["name"]
        b_section = b_by_name.get(name)
        if b_section is None:
            continue

        row = index // cols
        col = index % cols
        x0, y0, x1, y1 = panel_rect(margin, grid_top, panel_w, panel_h, gap_x, gap_y, col, row)
        draw.rounded_rectangle([x0, y0, x1, y1], radius=8, fill="#FFFFFF", outline="#DDD8CE", width=2)

        points_a = a_section["points"]
        points_b = b_section["points"]
        transform, plot_bounds = fit_transform(
            [points_a, points_b],
            (x0, y0, x1, y1),
            (42, 68, 42, 50),
            axis_reference=True,
        )

        color = a_section.get("color", "#333333")
        draw_axes(draw, transform, plot_bounds)
        polyline(draw, points_a, transform, color, width=4, dashed=False)
        polyline(draw, points_b, transform, color, width=4, dashed=True)

        label = a_section.get("label", name)
        a_perim = float(a_section["summary"]["perimeter"])
        b_perim = float(b_section["summary"]["perimeter"])
        diff = b_perim - a_perim
        draw.text((x0 + 20, y0 + 18), label, fill=color, font=font_panel)
        draw.text(
            (x0 + 20, y0 + 48),
            f"{a_name} {a_perim:.2f} / {b_name} {b_perim:.2f} / {DIFF_TEXT} {diff:+.2f} cm",
            fill="#555555",
            font=font_small,
        )
        draw.text(
            (x0 + 20, y1 - 32),
            f"{SECTION_HEIGHT_TEXT}\uff1a{a_name} {float(a_section['height_from_floor_cm']):.2f} cm / "
            f"{b_name} {float(b_section['height_from_floor_cm']):.2f} cm",
            fill="#666666",
            font=font_small,
        )

        comparison.append(
            {
                "name": name,
                "label": label,
                "color": color,
                a_name: {
                    "perimeter_cm": round(a_perim, 2),
                    "height_from_floor_cm": round(float(a_section["height_from_floor_cm"]), 2),
                },
                b_name: {
                    "perimeter_cm": round(b_perim, 2),
                    "height_from_floor_cm": round(float(b_section["height_from_floor_cm"]), 2),
                },
                f"diff_{b_name}_minus_{a_name}_cm": round(diff, 2),
            }
        )

    overlay_specs = [
        (a_sections, a_mesh, a_name, 1),
        (b_sections, b_mesh, b_name, 2),
    ]
    overlay_summaries: list[dict[str, Any]] = []
    for sections, mesh, name, col in overlay_specs:
        x0, y0, x1, y1 = panel_rect(margin, grid_top, panel_w, panel_h, gap_x, gap_y, col, 2)
        draw.rounded_rectangle([x0, y0, x1, y1], radius=8, fill="#FFFFFF", outline="#DDD8CE", width=2)
        draw.text((x0 + 20, y0 + 18), f"{name} {ALL_SECTIONS_TEXT}\uff08{HEIGHT_TEXT} {display_height(mesh):.2f} cm\uff09", fill="#333333", font=font_panel)
        point_sets = [section["points"] for section in sections]
        transform, plot_bounds = fit_transform(
            point_sets,
            (x0, y0, x1, y1),
            (42, 62, 42, 38),
            axis_reference=True,
        )
        draw_axes(draw, transform, plot_bounds)
        for section in sections:
            polyline(draw, section["points"], transform, section.get("color", "#333333"), width=3, dashed=(name == b_name))
        draw.text((x0 + 20, y1 - 28), SAME_AXIS_TEXT, fill="#666666", font=font_tiny)
        overlay_summaries.append({"name": name, "height_cm": round(display_height(mesh), 2), "mesh_height_cm": round(float(mesh["height_cm"]), 2)})

    draw.text((margin, height - 46), FOOTNOTE_TEXT, fill="#666666", font=font_small)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out_png, quality=95)

    out_json = out_png.with_suffix(".json")
    out_json.write_text(
        json.dumps(
            {
                "png": str(out_png),
                "title": title_text,
                "mode": f"{a_name} solid, {b_name} dashed; each panel scaled to its two loops",
                "body_heights_cm": {a_name: round(display_height(a_mesh), 2), b_name: round(display_height(b_mesh), 2)},
                "mesh_heights_cm": {a_name: round(float(a_mesh["height_cm"]), 2), b_name: round(float(b_mesh["height_cm"]), 2)},
                "overlay_panels": overlay_summaries,
                "sections": comparison,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-mesh", required=True)
    parser.add_argument("--a-config", required=True)
    parser.add_argument("--a-name", default="9AR")
    parser.add_argument("--b-mesh", required=True)
    parser.add_argument("--b-config", required=True)
    parser.add_argument("--b-name", default="DF")
    parser.add_argument("--out-png", required=True)
    parser.add_argument("--logo")
    args = parser.parse_args()

    a_sections, a_mesh, _ = load_avatar(Path(args.a_mesh), Path(args.a_config))
    b_sections, b_mesh, _ = load_avatar(Path(args.b_mesh), Path(args.b_config))
    render_comparison(
        Path(args.out_png),
        a_sections,
        a_mesh,
        b_sections,
        b_mesh,
        Path(args.logo) if args.logo else None,
        args.a_name,
        args.b_name,
    )


if __name__ == "__main__":
    main()
