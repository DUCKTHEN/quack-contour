# Web Version Plan

Quack Contour now includes a first static GitHub Pages entry point at the repository root.

Current files:

- `index.html`
- `web/styles.css`
- `web/app.js`

## Current Static Web Scope

The first browser version is intentionally lightweight:

- Load OBJ files directly in the browser.
- Preview a primary model and optional comparison model.
- Switch between side-by-side and overlay layouts.
- Change model colors and opacity.
- Show section guide lines as visual reference.
- Show basic model diagnostics.
- Keep model data local to the browser.

This version is meant to make the project easy to try from GitHub Pages, like Quack Trace.

## Not Yet Ported From The Local Version

The local Python app still contains the richer workflow:

- Detailed section guide editing.
- Top-view PNG and JSON generation.
- Masking and lasso workflows.
- Underlay image transform controls.
- Doodle tools and undo/redo integration.
- Full comparison table and export workflow.

## Next Steps

1. Move browser OBJ parsing into a Web Worker so large files do not block the UI.
2. Add section guide editing to the static version.
3. Add top-view generation for checked sections only.
4. Add Quack Trace style doodle tools and pen color controls.
5. Add underlay image move, scale, and rotate controls.
6. Add GitHub Pages publishing notes and a release checklist.

## 日本語メモ

リポジトリ直下に、GitHub Pages向けの最初のWeb版を追加しました。

現時点では「Webからすぐ試せる軽量版」です。OBJの読み込み、主モデルと比較モデルの表示、並べる・重ねる、色と濃さの変更、簡易モデル診断ができます。

まだローカルPython版の全機能は移植していません。断面編集、上面図生成、マスク、下絵、落書き、エクスポートまわりは今後段階的にWeb版へ移していきます。