# Quack Contour Web Version Plan

Quack Contour currently runs as a local Python server at `http://127.0.0.1:8765/`.

The planned `github.io` version should be a static browser app, similar in spirit to Quack Trace.

## Goal

Make a public web version that modelers can open without installing Python, while keeping private model data local in the user's browser.

## Important Constraint

GitHub Pages can serve static files only. It cannot run the current Python endpoints:

- `/api/mesh`
- `/api/lines`
- `/api/generate`

So the web version needs browser-side processing or a separate hosted backend.

## Recommended Direction

Use a browser-only version first.

- Parse OBJ files in the browser.
- Render models with Three.js.
- Keep imported files in browser memory only.
- Use Web Workers for heavy parsing and section calculations.
- Start with preview, silhouette, guide editing, opacity controls, doodles, and diagnostics.
- Add top-view PNG/JSON generation only after performance is acceptable.

This keeps the privacy story simple: no model upload, no server storage.

## Local Version

Keep the Python version as the heavier desktop/local tool.

The local version can continue to support:

- Larger OBJ files.
- Generated PNG/JSON files in `outputs/`.
- Temporary uploads in `uploads/`.
- More CPU-heavy section generation.

## Web Version Milestones

1. Create a static `docs/` or `web/` entry point for GitHub Pages.
2. Move reusable UI strings, colors, and section definitions out of embedded Python HTML.
3. Add browser OBJ import and Three.js preview.
4. Add primary/compare model opacity, visibility, and layout controls.
5. Add guide line editing and model diagnostics.
6. Add underlay image and doodle tools.
7. Prototype browser-side top-view export in a Web Worker.
8. Test with small, medium, and dense OBJ files.

## Privacy Text for Web Version

Imported model files are read in the user's browser. They are not uploaded to the project author, GitHub, or any server by the static web app.
