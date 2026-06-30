"""Measure a bust-section circumference from OBJ or ASCII FBX mesh data.

The tool slices the mesh with a horizontal plane, builds intersection loops,
and reports loop perimeters. OBJ is the primary supported format. ASCII FBX is
supported for simple mesh exports that contain Vertices and PolygonVertexIndex
arrays; binary FBX should be exported to OBJ first.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np


AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def parse_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    with path.open("r", encoding="utf-8", errors="ignore") as file:
        for line in file:
            if line.startswith("v "):
                _, x, y, z, *_ = line.split()
                vertices.append((float(x), float(y), float(z)))
            elif line.startswith("f "):
                indices: list[int] = []
                for item in line.split()[1:]:
                    raw_index = int(item.split("/")[0])
                    if raw_index < 0:
                        raw_index = len(vertices) + raw_index + 1
                    indices.append(raw_index - 1)
                faces.extend(triangulate(indices))

    return np.array(vertices, dtype=float), np.array(faces, dtype=int)


def parse_ascii_fbx(path: Path) -> tuple[np.ndarray, np.ndarray]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if "Kaydara FBX Binary" in text[:256]:
        raise ValueError("Binary FBX is not supported yet. Export the avatar as OBJ, or ASCII FBX.")

    vertices_match = re.search(r"Vertices:\s*\*\d+\s*\{\s*a:\s*([^}]*)\}", text, re.S)
    polygons_match = re.search(r"PolygonVertexIndex:\s*\*\d+\s*\{\s*a:\s*([^}]*)\}", text, re.S)
    if not vertices_match or not polygons_match:
        raise ValueError("Could not find ASCII FBX Vertices and PolygonVertexIndex arrays.")

    vertex_values = [float(value) for value in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", vertices_match.group(1))]
    if len(vertex_values) % 3:
        raise ValueError("FBX Vertices array length is not divisible by 3.")
    vertices = np.array(vertex_values, dtype=float).reshape((-1, 3))

    polygon_values = [int(value) for value in re.findall(r"-?\d+", polygons_match.group(1))]
    polygons: list[list[int]] = []
    current: list[int] = []
    for value in polygon_values:
        if value < 0:
            current.append(-value - 1)
            polygons.append(current)
            current = []
        else:
            current.append(value)
    if current:
        polygons.append(current)

    faces: list[tuple[int, int, int]] = []
    for polygon in polygons:
        faces.extend(triangulate(polygon))

    return vertices, np.array(faces, dtype=int)


def triangulate(indices: list[int]) -> list[tuple[int, int, int]]:
    if len(indices) < 3:
        return []
    if len(indices) == 3:
        return [tuple(indices)]  # type: ignore[return-value]
    first = indices[0]
    return [(first, indices[index], indices[index + 1]) for index in range(1, len(indices) - 1)]


def load_mesh(path: Path) -> tuple[np.ndarray, np.ndarray, str]:
    suffix = path.suffix.lower()
    if suffix == ".obj":
        vertices, faces = parse_obj(path)
        return vertices, faces, "obj"
    if suffix == ".fbx":
        vertices, faces = parse_ascii_fbx(path)
        return vertices, faces, "ascii_fbx"
    raise ValueError(f"Unsupported mesh format: {path.suffix}. Use .obj or ASCII .fbx.")


def intersect_triangle_plane(
    triangle: np.ndarray,
    axis_index: int,
    height: float,
    eps: float,
) -> list[np.ndarray]:
    points: list[np.ndarray] = []
    edges = ((0, 1), (1, 2), (2, 0))
    distances = triangle[:, axis_index] - height

    for start, end in edges:
        p1 = triangle[start]
        p2 = triangle[end]
        d1 = distances[start]
        d2 = distances[end]

        if abs(d1) <= eps and abs(d2) <= eps:
            points.extend([p1, p2])
        elif abs(d1) <= eps:
            points.append(p1)
        elif abs(d2) <= eps:
            points.append(p2)
        elif d1 * d2 < 0:
            t = d1 / (d1 - d2)
            points.append(p1 + t * (p2 - p1))

    unique: list[np.ndarray] = []
    for point in points:
        if not any(np.linalg.norm(point - existing) <= eps for existing in unique):
            unique.append(point)

    if len(unique) < 2:
        return []
    if len(unique) == 2:
        return unique

    # Degenerate coplanar triangle: keep the longest edge as a conservative segment.
    longest: tuple[np.ndarray, np.ndarray] | None = None
    longest_length = -1.0
    for i in range(len(unique)):
        for j in range(i + 1, len(unique)):
            length = float(np.linalg.norm(unique[i] - unique[j]))
            if length > longest_length:
                longest_length = length
                longest = (unique[i], unique[j])
    return list(longest) if longest else []


def slice_mesh(
    vertices: np.ndarray,
    faces: np.ndarray,
    axis: str,
    height: float,
    eps: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    axis_index = AXIS_INDEX[axis]
    keep_axes = [index for index in range(3) if index != axis_index]
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for face in faces:
        triangle = vertices[face]
        if triangle[:, axis_index].min() - eps > height or triangle[:, axis_index].max() + eps < height:
            continue
        points = intersect_triangle_plane(triangle, axis_index, height, eps)
        if len(points) != 2:
            continue
        a = (float(points[0][keep_axes[0]]), float(points[0][keep_axes[1]]))
        b = (float(points[1][keep_axes[0]]), float(points[1][keep_axes[1]]))
        if math.hypot(a[0] - b[0], a[1] - b[1]) > eps:
            segments.append((a, b))

    return segments


def quantize(point: tuple[float, float], tolerance: float) -> tuple[int, int]:
    return (round(point[0] / tolerance), round(point[1] / tolerance))


def build_paths(
    segments: Iterable[tuple[tuple[float, float], tuple[float, float]]],
    tolerance: float,
) -> list[dict[str, object]]:
    points: dict[tuple[int, int], tuple[float, float]] = {}
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)

    for a, b in segments:
        qa = quantize(a, tolerance)
        qb = quantize(b, tolerance)
        if qa == qb:
            continue
        points.setdefault(qa, a)
        points.setdefault(qb, b)
        adjacency[qa].append(qb)
        adjacency[qb].append(qa)

    unused_edges = {frozenset((a, b)) for a, neighbors in adjacency.items() for b in neighbors}
    paths: list[dict[str, object]] = []

    while unused_edges:
        edge = unused_edges.pop()
        start, current = tuple(edge)
        previous: tuple[int, int] | None = start
        path = [start, current]

        while True:
            candidates = [
                node for node in adjacency[current]
                if frozenset((current, node)) in unused_edges and node != previous
            ]
            if not candidates:
                candidates = [node for node in adjacency[current] if frozenset((current, node)) in unused_edges]
            if not candidates:
                break

            next_node = candidates[0]
            unused_edges.remove(frozenset((current, next_node)))
            previous, current = current, next_node
            path.append(current)
            if current == start:
                break

        coords = [points[node] for node in path]
        closed = len(coords) > 2 and math.hypot(coords[0][0] - coords[-1][0], coords[0][1] - coords[-1][1]) <= tolerance * 2
        paths.append(path_stats(coords, closed))

    return sorted(paths, key=lambda item: item["perimeter"], reverse=True)


def path_stats(coords: list[tuple[float, float]], closed: bool) -> dict[str, object]:
    perimeter = 0.0
    for i in range(len(coords) - 1):
        perimeter += math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
    if closed and len(coords) > 2:
        perimeter += math.hypot(coords[0][0] - coords[-1][0], coords[0][1] - coords[-1][1])

    xs = [point[0] for point in coords]
    ys = [point[1] for point in coords]
    return {
        "perimeter": perimeter,
        "closed": closed,
        "point_count": len(coords),
        "centroid": [sum(xs) / len(xs), sum(ys) / len(ys)] if coords else [0.0, 0.0],
        "bbox": [min(xs), min(ys), max(xs), max(ys)] if coords else [0.0, 0.0, 0.0, 0.0],
        "points": coords,
    }


def select_loop(paths: list[dict[str, object]], mode: str, expected: float | None = None) -> dict[str, object] | None:
    if not paths:
        return None
    closed_paths = [path for path in paths if path["closed"]]
    candidates = closed_paths or paths
    if mode == "largest":
        return max(candidates, key=lambda item: float(item["perimeter"]))
    if mode == "center":
        return min(
            candidates,
            key=lambda item: math.hypot(float(item["centroid"][0]), float(item["centroid"][1])),  # type: ignore[index]
        )
    if mode == "closest":
        if expected is None:
            raise ValueError("--expected is required when --select closest")
        return min(candidates, key=lambda item: abs(float(item["perimeter"]) - expected))
    raise ValueError(f"Unknown loop selection mode: {mode}")


def write_svg(
    output_path: Path,
    paths: list[dict[str, object]],
    selected: dict[str, object] | None,
    title: str,
) -> None:
    all_points = [point for path in paths for point in path["points"]]  # type: ignore[index]
    if not all_points:
        return

    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    width, height = 900, 680
    pad = 60
    scale = min((width - 2 * pad) / max(max_x - min_x, 1e-6), (height - 2 * pad) / max(max_y - min_y, 1e-6))

    def map_point(point: tuple[float, float]) -> tuple[float, float]:
        x = pad + (point[0] - min_x) * scale
        y = height - pad - (point[1] - min_y) * scale
        return x, y

    selected_id = id(selected) if selected is not None else None
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffdf8"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.title{font-size:18px;font-weight:700}.note{font-size:13px}</style>',
        f'<text class="title" x="{width / 2}" y="28" text-anchor="middle">{title}</text>',
    ]

    for index, path in enumerate(paths, 1):
        coords = path["points"]  # type: ignore[assignment]
        mapped = " ".join(f"{map_point(point)[0]:.2f},{map_point(point)[1]:.2f}" for point in coords)
        color = "#b00020" if id(path) == selected_id else "#555555"
        stroke_width = "3" if id(path) == selected_id else "1.5"
        parts.append(
            f'<polyline points="{mapped}" fill="none" stroke="{color}" '
            f'stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        centroid = path["centroid"]  # type: ignore[assignment]
        cx, cy = map_point((float(centroid[0]), float(centroid[1])))
        parts.append(f'<text class="note" x="{cx + 6:.1f}" y="{cy:.1f}">#{index} {float(path["perimeter"]):.2f}</text>')

    if selected is not None:
        parts.append(f'<text class="note" x="24" y="{height - 24}">selected perimeter: {float(selected["perimeter"]):.3f}</text>')
    parts.append("</svg>")
    output_path.write_text("\n".join(parts), encoding="utf-8")


def measure_at_height(args: argparse.Namespace) -> dict[str, object]:
    mesh_path = Path(args.mesh)
    vertices, faces, loader = load_mesh(mesh_path)
    vertices = vertices * args.unit_scale

    segments = slice_mesh(vertices, faces, args.axis, args.height, args.eps)
    paths = build_paths(segments, args.join_tolerance)
    selected = select_loop(paths, args.select, args.expected)

    result = {
        "source": str(mesh_path),
        "loader": loader,
        "axis": args.axis,
        "height": args.height,
        "unit_scale": args.unit_scale,
        "vertices": int(vertices.shape[0]),
        "faces": int(faces.shape[0]),
        "segments": len(segments),
        "loop_count": len(paths),
        "expected": args.expected,
        "selected_loop": summarize_loop(selected),
        "loops": [summarize_loop(path) for path in paths[: args.max_loops]],
    }

    if args.svg:
        svg_path = Path(args.svg)
        write_svg(svg_path, paths[: args.max_loops], selected, f"{mesh_path.name} slice {args.axis}={args.height}")
        result["svg"] = str(svg_path)

    return result


def summarize_loop(loop: dict[str, object] | None) -> dict[str, object] | None:
    if loop is None:
        return None
    return {
        "perimeter": round(float(loop["perimeter"]), 6),
        "closed": bool(loop["closed"]),
        "point_count": int(loop["point_count"]),
        "centroid": [round(float(value), 6) for value in loop["centroid"]],  # type: ignore[index]
        "bbox": [round(float(value), 6) for value in loop["bbox"]],  # type: ignore[index]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure bust circumference from a mesh slice.")
    parser.add_argument("mesh", help="OBJ or ASCII FBX mesh path")
    parser.add_argument("--height", type=float, required=True, help="Slice height along the selected axis, after unit scaling")
    parser.add_argument("--axis", choices=["x", "y", "z"], default="y", help="Vertical axis. CLO/our OBJ defaults to Y")
    parser.add_argument("--unit-scale", type=float, default=1.0, help="Multiply imported vertex coordinates by this value")
    parser.add_argument("--select", choices=["largest", "center", "closest"], default="largest", help="Loop selection strategy")
    parser.add_argument("--expected", type=float, help="Expected circumference for --select closest")
    parser.add_argument("--eps", type=float, default=1e-7, help="Plane intersection epsilon")
    parser.add_argument("--join-tolerance", type=float, default=1e-4, help="2D point join tolerance")
    parser.add_argument("--max-loops", type=int, default=12, help="Maximum loops to include in JSON/SVG")
    parser.add_argument("--json-out", help="Optional JSON output path")
    parser.add_argument("--svg", help="Optional SVG cross-section preview path")
    args = parser.parse_args()

    result = measure_at_height(args)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
