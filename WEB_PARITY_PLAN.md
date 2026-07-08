# Web Parity Plan

Goal: keep Quack Contour from becoming two separate apps.
The long-term direction is a shared frontend plus optional local Python APIs.

## Current Structure

- `/` in `app.py` serves the current local Python UI from `html_page()`.
- `/shared/` in `app.py` serves the static frontend files:
  - `index.html`
  - `web/app.js`
  - `web/styles.css`
  - `assets/`
  - `vendor/`
- GitHub Pages serves the same static frontend files directly.
- `/api/health` exists only when running the local Python server.

## Why The UI Still Differs

The local UI is still embedded inside `app.py`.
The Web UI is still a separate implementation in `index.html` and `web/app.js`.
This means visual parity can drift even when both routes work.

## Safe Migration Direction

1. Keep `/` untouched until a small shared route is proven stable.
2. Use `/shared/` to test the static frontend under the local Python server.
3. Let the static frontend detect the local API with `/api/health`.
4. Mark Python-only features as local-only instead of failing on GitHub Pages.
5. Move UI pieces from `app.py` into shared files only in small, reviewed steps.
6. Avoid copying large features twice. If a feature must exist in Web and local, it should live in shared frontend code.

## Local Python API Boundary

Likely local-only for now:

- Full-resolution top-view generation through Python helper scripts.
- Compare report generation using local files and generated outputs.
- Any feature that depends on filesystem paths or Python packages.

Good candidates for shared frontend:

- Header and panel layout.
- Import buttons and file-name display.
- Viewer toolbar.
- Color, opacity, and display-height controls.
- Underlay image move/scale/rotate UI.
- Doodle UI.
- Model diagnostics display.
- Basic OBJ preview if browser performance is acceptable.

## First Experiment Status

- `/shared/` route: present in `app.py`.
- `/api/health`: present in `app.py`.
- Web API detection: added in `web/app.js`.
- Initial no-API fallback class: added to `index.html`.
- CSS hooks for local-only controls: added to `web/styles.css`.
- Cache-busted static CSS/JS/worker references: added to `index.html` and `web/app.js`.

## Next Small Steps

1. Verify `/` still shows the local Python UI.
2. Verify `/shared/` shows the static frontend locally.
3. Verify `/shared/` changes `body.no-local-api` to `body.has-local-api`.
4. Verify the same static files do not throw errors without `/api/health`.
5. Pick one small shared UI component to align next, likely the header actions or import panel.

## Do Not Do Yet

- Do not continue one-off migration of section sliders or XYZ gizmo.
- Do not rewrite the whole local UI at once.
- Do not push until explicitly approved.
