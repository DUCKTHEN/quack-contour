# Quack Contour
<img width="1277" height="665" alt="image" src="https://github.com/user-attachments/assets/329fe712-a2ed-4e61-bcfa-64af179f439e" />
<img width="1790" height="1080" alt="interactive_sections_20260701_094119" src="https://github.com/user-attachments/assets/69b8c042-159b-4058-a823-5f6759c05f1a" />
<img width="1620" height="1260" alt="interactive_sections_compare_20260701_093230" src="https://github.com/user-attachments/assets/ea6aa818-7d98-4d29-b166-72a4ccafe6b6" />

Quack Contour is an experimental OBJ avatar contour tool for CG designers, digital fashion workflows, and modelers who want to inspect body shape from more than one angle.

It helps compare human-like avatar bodies through silhouettes, section guide lines, and top-view cross-section drawings. The goal is not to replace strict measurement software, but to support visual thinking: seeing a model as a shadow, comparing proportions, and discovering new viewpoints through section lines and contour drawings.

Quack Contour is a sister tool to [Quack Trace](https://github.com/DUCKTHEN/quack-trace). Quack Trace focuses on 2D pattern tracing from images. Quack Contour focuses on imported 3D avatar meshes.

## Concept

Quack Contour was developed from the perspective of a CG designer with a fashion background.

In clothing and avatar production, the difference between a human body and a digital avatar is often difficult to understand from a normal front view alone. A silhouette can reveal the overall balance. Section guide lines can reveal height positions and body landmarks. A top-view cross section can show thickness, depth, and volume in a way that feels different from ordinary viewport inspection.

This tool is meant to be an idea tool for observing form. It is for asking questions such as:

- How does this avatar read as a whole silhouette?
- Where do the bust, waist, hip, shoulder, and neck positions sit visually?
- How does one model differ from another when viewed as body sections?
- What kind of shape information would help a modeler or digital fashion designer make better decisions?

## Current Status

The local Python version is the main version right now.

The browser-only Web version is under development. The goal is to make Quack Contour usable without Python, while keeping the Web version as close as possible to the local version. Some advanced features are still local-only.

## Main Features

Local Python version:

- Import a primary OBJ model.
- Import an optional comparison OBJ model.
- Compare models side by side or overlaid.
- View models as silhouettes or outline contours.
- Adjust section guide lines for body landmarks such as neck, shoulder, bust, underbust, waist, and hip.
- Generate top-view cross-section drawings as PNG and JSON.
- Export silhouette PNGs.
- Use underlay images for visual comparison.
- Add quick drawing notes on the viewport.
- Create masks with rectangle or lasso selection.
- Run lightweight model diagnostics before review.

Web version in progress:

- Browser-only OBJ loading.
- Primary and comparison model preview.
- Color and opacity controls.
- Basic model diagnostics.
- No model upload to the project author.

## Local Python Version

The local version runs a small web server on your machine and opens Quack Contour at:

```text
http://127.0.0.1:8765/
```

Requirements:

- Python 3.10 or newer
- Packages listed in `requirements.txt`

Run:

```powershell
pip install -r requirements.txt
python app.py
```

On Windows, `start-quack-contour.bat` can also be used.

## Web Version

A static Web version is included for GitHub Pages development:

- `index.html`
- `web/styles.css`
- `web/app.js`

The Web version is being developed so it can run without Python. It is not yet fully equal to the local Python version.

Planned direction:

- Keep the local and Web UI as close as possible.
- Avoid maintaining two completely different apps where possible.
- Keep browser-only features available without a server.
- Keep Python-only generation features local until they can be safely shared or replaced.

## Privacy And Model Data

Imported model data belongs to the model creator, rights holder, or user. Quack Contour does not claim ownership of imported OBJ/FBX files.

Important notes:

- The local Python version processes model files on the user's own machine.
- The GitHub Pages/Web version reads model files in the browser.
- Imported models are not sent to the project author.
- Imported models are not saved into this repository.
- Generated PNG/JSON output may contain measurements or shapes derived from the imported model, so handle outputs according to the model owner's permission.
- Do not commit private avatars, commercial models, client data, paid assets, or unpublished character models into this repository.

## Model Diagnostics

The model diagnostics panel is a lightweight warning and reference panel. It can help users notice common import issues before measuring or comparing.

It may show information such as:

- Vertex count.
- Face and triangle count.
- Estimated model height from bounds.
- Bounding-box size.
- Approximate file size.
- Scale differences between primary and comparison models.
- Warnings for very large or suspiciously scaled files.

Diagnostics are only hints. They do not judge model quality, rig quality, topology quality, commercial usability, or whether a model is suitable for production.

## Recommended OBJ Size

For the Web version, a practical target is:

- Under about 20 MB per OBJ.
- Under about 150,000 triangles.

Larger files may still load, but interaction can become slow depending on the machine and browser.

For the local Python version, heavier files can be used, but review copies are usually more comfortable when they are lighter. For silhouette and section review, a decimated inspection OBJ is often enough.

## Outputs And Measurement Notes

Quack Contour can export top-view section drawings as PNG and JSON. These outputs are intended as review materials and visual notes for model checking.

They are not strict ISO 8559 documents. Some ISO-inspired terms and body landmarks may be used as reference labels, but Quack Contour is not an official standards tool.

## Relationship To Quack Trace

Quack Contour is part of the Quack tool family.

- [Quack Trace](https://github.com/DUCKTHEN/quack-trace): trace real-size 2D coordinates from images.
- Quack Contour: inspect OBJ avatar body silhouettes, section lines, and contour drawings.

Both tools are made to support creative inspection and practical production notes.

## License

Code is released under the MIT License. See [`LICENSE`](LICENSE).

The MIT License is intended to make the tool easy to use in personal, studio, commercial, and game-production contexts.

Assets, sample files, generated images, and imported user model data are not automatically covered by the MIT code license unless explicitly stated.

---

# Quack Contour 日本語説明

Quack Contour は、OBJ形式のアバターモデルを読み込み、シルエット・断面ガイド・上面断面図を通して体型を観察するための実験的なツールです。

服飾のバックグラウンドを持つCGデザイナーの視点から、人間の形とアバターの形を比較したり、モデルを影のようなシルエットとして見たり、断面図から新しい視点を考えたりできる「形を見るためのアイディアソフト」として開発しています。

厳密な人体計測ソフトというより、モデラーやデジタルファッション制作に関わる人が、形の違いに気づくための補助ツールです。

## 現在の状態

現在はローカルPython版を中心に開発しています。

Web版は、Pythonなしでブラウザから使えるように開発中です。最終的にはローカル版とWeb版の見た目や操作感をできるだけ近づける方針ですが、現時点では細かい断面編集・出力・マスクなど一部の機能はローカル版が先行しています。

## 主な用途

- 主モデルと比較モデルを並べて体型差を見る。
- シルエット表示で全体のバランスを見る。
- 外周線表示で輪郭を確認する。
- 首、肩、バスト、アンダーバスト、ウエスト、ヒップなどの断面ガイドを調整する。
- 選択した断面から上面断面図をPNG/JSONとして出力する。
- 下絵画像や落書きメモを使って、修正指示や比較メモを残す。
- 矩形または投げなわでマスクを作成し、確認したい部分を整理する。

## モデルデータについて

読み込んだモデルデータは、モデル制作者・権利者・利用者に属します。Quack Contour の制作者やリポジトリが、読み込まれたモデルデータに対して権利を主張することはありません。

- ローカルPython版では、ユーザーのPC上で処理されます。
- Web版では、ブラウザ内でモデルを読み込みます。
- モデルデータがツール制作者へ送信されることはありません。
- 読み込んだモデルがこのリポジトリに保存されることもありません。
- 生成したPNG/JSONにはモデル由来の形状や寸法情報が含まれる場合があるため、モデル権利者の許諾に従って扱ってください。

## ライセンス

コードは MIT License です。

個人制作、スタジオ制作、商用利用、ゲーム制作の現場でも使いやすい形を意識しています。ただし、サンプル素材・生成画像・ユーザーが読み込むモデルデータは、明示されていない限りMITライセンスの対象ではありません。
