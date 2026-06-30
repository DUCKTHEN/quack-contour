"""OBJ section top-view tool.

Load an OBJ/ASCII-FBX avatar mesh plus a horizontal line list, then render a
top-view overlay and measurement JSON.

Line list CSV columns:
name,label,color,height_from_floor_cm,height_ratio,expected_cm,scan_around_cm,scan_step_cm,select,scan
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import avatar_section_overlay as overlay  # noqa: E402
import measure_avatar_bust as meshcut  # noqa: E402


NUMERIC_FIELDS = {
    "height_from_floor_cm",
    "height_ratio",
    "expected_cm",
    "scan_around_cm",
    "scan_step_cm",
}
BOOL_FIELDS = {"scan"}
TEXT_FIELDS = {"name", "label", "color", "select"}


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def clean_row(row: dict[str, str]) -> dict[str, Any]:
    section: dict[str, Any] = {}
    for key, raw in row.items():
        if key is None:
            continue
        key = key.strip()
        value = (raw or "").strip()
        if not key or not value:
            continue
        if key in NUMERIC_FIELDS:
            section[key] = float(value)
        elif key in BOOL_FIELDS:
            section[key] = parse_bool(value)
        elif key in TEXT_FIELDS:
            section[key] = value
    if "name" not in section:
        raise ValueError(f"Line row needs a name: {row}")
    return section


def load_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["sections"] if isinstance(data, dict) and "sections" in data else data
        return [dict(row) for row in rows]
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [clean_row(row) for row in reader]


def make_config(args: argparse.Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {
        "title": args.title or f"{Path(args.mesh).stem}: top-view sections",
        "unit_scale": args.unit_scale,
        "show_rig_bone_points": False,
        "sections": load_lines(Path(args.lines)),
        "labels": {
            "front": "前",
            "back": "後",
            "measured_loops": "測定断面",
            "height_from_floor": "H = 床からの高さ",
            "footnote": "OBJ X/Z軸を保持。水平断面ラインから上面図を作成。",
        },
    }
    if args.display_height_cm is not None:
        config["display_height_cm"] = args.display_height_cm
    return config


def write_outputs(
    mesh_path: Path,
    config: dict[str, Any],
    out_png: Path,
    out_json: Path,
    logo_path: Path | None,
) -> dict[str, Any]:
    vertices, faces, loader = meshcut.load_mesh(mesh_path)
    sections, mesh_report = overlay.build_sections(vertices, faces, config)
    overlay.render_png(
        out_png,
        config["title"],
        sections,
        logo_path,
        white_background=True,
        text_labels=config.get("labels"),
        show_rig_bone_points=False,
    )
    report = {
        "source": str(mesh_path),
        "loader": loader,
        "mesh": mesh_report,
        "display_height_cm": config.get("display_height_cm"),
        "height_reference": "OBJ Y axis; floor is mesh min Y after unit scaling",
        "title": config["title"],
        "png": str(out_png),
        "sections": overlay.strip_points(sections, include_rig_bone=False),
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh", required=True, help="OBJ or ASCII FBX path")
    parser.add_argument("--lines", required=True, help="CSV/JSON line list")
    parser.add_argument("--out-png", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--title")
    parser.add_argument("--logo")
    parser.add_argument("--unit-scale", type=float, default=1.0)
    parser.add_argument("--display-height-cm", type=float)
    args = parser.parse_args()

    report = write_outputs(
        Path(args.mesh),
        make_config(args),
        Path(args.out_png),
        Path(args.out_json),
        Path(args.logo) if args.logo else None,
    )
    print(json.dumps(
        {
            "png": report["png"],
            "height_cm": round(float(report["mesh"]["height_cm"]), 2),
            "display_height_cm": report.get("display_height_cm"),
            "sections": [
                {
                    "name": section["name"],
                    "label": section.get("label"),
                    "perimeter_cm": round(float(section["summary"]["perimeter"]), 2),
                    "height_from_floor_cm": round(float(section["height_from_floor_cm"]), 2),
                }
                for section in report["sections"]
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
