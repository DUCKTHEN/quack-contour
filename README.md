# Quack Contour

Quack Contour is an OBJ avatar contour and body-section checking tool. It helps modelers inspect silhouettes, section guide lines, and top-view circumference drawings while comparing a primary model with an optional comparison model.

Quack Contour is a sister tool to [Quack Trace](https://github.com/DUCKTHEN/quack-trace). Quack Trace focuses on 2D pattern tracing from images. Quack Contour focuses on imported 3D avatar meshes.

## 日本語概要

Quack Contour は、OBJアバターモデルの体型断面、シルエット、外周線、上面断面図を確認するためのツールです。

[Quack Trace](https://github.com/DUCKTHEN/quack-trace) の姉妹ツールとして作っています。Quack Trace が画像から実寸座標を取る製図トレース向けなのに対して、Quack Contour は3Dモデルを読み込み、主モデルと比較モデルの体型バランスを確認する用途に寄せています。

主な用途:

- 主モデルと比較モデルを並べて、身長、肩幅、バスト、ウエスト、ヒップなどの差を見る。
- 首、肩、バスト、アンダーバスト、ウエスト、ヒップ上側、ヒップなどの断面ガイドを調整する。
- シルエット表示、外周線表示、断面ガイド表示で、モデラー向けの確認資料を作る。
- 選択した断面だけを上面断面図としてPNG/JSON出力する。
- 下絵画像、落書きメモ、マスク機能を使って、修正指示や比較メモを残す。

## Web Version

A GitHub Pages friendly static web version is included at the repository root:

- `index.html`
- `web/styles.css`
- `web/app.js`

The web version runs entirely in the browser. It can load OBJ files from your local computer, show a primary model and comparison model, change display opacity, switch between side-by-side and overlay layouts, and show lightweight model diagnostics.

Current web version scope:

- Browser-only OBJ loading.
- Primary and comparison model preview.
- Side-by-side and overlay comparison.
- Model color and opacity controls.
- Basic diagnostics: vertices, faces, triangle count, bounding box, height estimate, and data-size warning.
- No server upload and no remote model storage.

The full desktop/local version still has more features, including detailed section editing, top-view PNG/JSON output, masking, underlay image controls, and richer drawing tools.

## Web版について

GitHub Pages で公開しやすい静的Web版を、リポジトリ直下に入れています。

- `index.html`
- `web/styles.css`
- `web/app.js`

Web版はブラウザだけで動きます。OBJファイルはユーザーのPC上で読み込まれ、サーバーへ送信されません。まずは「Webからすぐ試せる入口」として、主モデル、比較モデル、濃さ調整、並べる・重ねる、簡易診断を使えるようにしています。

現在のWeb版でできること:

- OBJをブラウザ内で読み込む。
- 主モデルと比較モデルを表示する。
- 並べる表示、重ねる表示を切り替える。
- モデル色と濃さを変える。
- 頂点数、面数、三角面数、バウンディングボックス、推定身長、データ量の注意を確認する。

ローカルPython版の方が、現時点では機能が多いです。断面位置編集、上面図PNG/JSON出力、マスク、下絵、落書きなどはローカル版で扱います。Web版には段階的に移植していきます。

## Local Python Version

The local version runs a small web server on your machine and opens the full Quack Contour UI at `http://127.0.0.1:8765/`.

Requirements:

- Python 3.10 or newer
- See `requirements.txt`

Run:

```powershell
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:8765/
```

On Windows, `start-quack-contour.bat` can also be used.

## ローカルPython版の使い方

ローカル版は、自分のPC内で小さなWebサーバーを立ち上げて使います。起動後、ブラウザで `http://127.0.0.1:8765/` を開きます。

必要なもの:

- Python 3.10 以上
- `requirements.txt` に記載されたPythonパッケージ

起動方法:

```powershell
pip install -r requirements.txt
python app.py
```

Windowsでは `start-quack-contour.bat` から起動することもできます。

## Privacy And Model Data

Quack Contour does not claim ownership of imported OBJ/FBX model data. Model files belong to their creators and users.

The local Python version stores temporary files only inside the local working folders used by the app. The GitHub Pages web version reads model files in the browser and does not upload them to this repository or to the project author.

Do not commit private avatar models, commercial models, client data, or unpublished character assets into this repository.

## モデルデータとプライバシー

読み込んだOBJ/FBXモデルデータは、モデル制作者または利用者のものです。このプロジェクト作者やリポジトリは、読み込まれたモデルデータに対して一切の権利を主張しません。

GitHub Pages版では、モデルファイルはブラウザ内で読み込まれます。作者へ送信されず、このリポジトリにも保存されません。

ローカルPython版では、作業用の一時ファイルがローカルPC内の作業フォルダに作られる場合があります。公開リポジトリへ、非公開モデル、商用モデル、依頼品、未公開キャラクター素材をコミットしないでください。

## Outputs

Quack Contour can export top-view section drawings as PNG and JSON. These outputs are intended as review materials, not as strict ISO 8559 documents.

## 出力について

Quack Contour は、上面断面図をPNG/JSONとして出力できます。これはモデラー向けの確認資料として使う想定です。ISO 8559-1 を参考にした補助メモを含みますが、正式な規格文書ではありません。

## Model Diagnostics

The model diagnostics panel is intended to help users notice common import issues before measuring:

- Very large OBJ files that may slow the browser.
- Extremely high vertex or face counts.
- Missing or suspicious bounding-box dimensions.
- Large differences between primary and comparison model scale.
- Basic height estimates based on model bounds.

Diagnostics are warnings and hints. They do not judge model quality, rig quality, topology quality, or commercial usability.

## モデル診断で分かること

モデル診断パネルでは、計測前に気づきたい問題を軽く確認できます。

- OBJが重すぎて、ブラウザやPCの負荷が高くなりそうか。
- 頂点数、面数、三角面数が極端に多くないか。
- バウンディングボックスや推定身長が不自然ではないか。
- 主モデルと比較モデルのスケール差が大きすぎないか。
- 読み込み後に断面確認へ進めそうか。

これは注意喚起のための補助表示です。モデルの品質、リグ、トポロジー、商用利用の可否を判定するものではありません。

## Recommended OBJ Size

For the browser version, a practical target is under about 20 MB per OBJ and under about 150,000 triangles. Larger files may still load, but interaction can become slow depending on the machine.

For the local Python version, heavier files can be used, but keeping review copies lighter is recommended. A decimated inspection copy is often enough for silhouette and section review.

## OBJの重さの目安

Web版では、まずは1ファイル20MB以下、三角面15万程度までを目安にしています。それ以上でも読み込める場合はありますが、PCやブラウザによって操作が重くなります。

ローカルPython版ではもう少し重いモデルも扱えます。ただし、体型比較や断面確認だけなら、確認用に軽量化したOBJを使う方が安定します。

## License

Code is released under the MIT License. See `LICENSE`.

Assets, sample files, generated images, and imported user model data are not automatically covered by the MIT code license unless explicitly stated.

## ライセンス

コードは MIT License です。ゲーム会社、個人制作者、モデラー、ツール開発者が使いやすい形を意識しています。

ただし、アセット、サンプルファイル、生成画像、ユーザーが読み込んだモデルデータは、明示されていない限りMITライセンスの対象には含まれません。

## Status

This is a beta tool. The UI and measurement behavior are still evolving through real modeler feedback.

## 開発状況

現在はベータ版です。モデラー向けの使いやすさ、表示の軽さ、断面の見やすさ、GitHub Pages版の機能移植を継続して改善しています。