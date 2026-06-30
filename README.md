# Quack Contour

Quack Contour is a local browser tool for checking OBJ avatar body sections, silhouettes, and top-view circumference drawings.

It is a sister tool to [Quack Trace](https://github.com/DUCKTHEN/quack-trace), but Quack Contour focuses on imported avatar meshes instead of 2D pattern tracing.

## What It Does

- Import a primary OBJ avatar model.
- Import a comparison OBJ model.
- Compare section guide lines on the body.
- Show silhouette and outline views.
- Generate top-view section PNG/JSON output.
- Move, scale, and rotate an underlay image.
- Draw temporary notes on the viewer.
- Mask mesh areas with rectangular or lasso selection.

## Important Privacy Note

Imported OBJ/FBX model data belongs to the person who provides it.

This project, its author, and its repository do not claim any rights to model data loaded into the tool. The app is designed as a local tool: the model files you choose are read on your own machine for preview and measurement, and are not uploaded to the project author or saved in this repository.

Do not commit private avatars, client models, paid assets, or exported production data. `.gitignore` excludes common model and output files by default.

## Status

Beta. The tool is usable, but the UI, measurement workflow, and export behavior are still changing.

OBJ is the primary supported model format. ASCII FBX parsing exists in some helper code, but the interactive importer currently treats OBJ as the reliable path.

## Requirements

- Python 3.10 or newer
- `numpy`
- `Pillow`

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Run Locally

From this repository folder:

```powershell
python app.py
```

Open:

```text
http://127.0.0.1:8765/
```

## Output Files

Generated PNG/JSON files are written to `outputs/`.

Uploaded or temporary files are written to `uploads/`.

Both folders are local working folders and are ignored by git except for their `.gitkeep` placeholders.

## Assets

The source code is licensed under the MIT License.

Third-party libraries and bundled media are documented in `THIRD_PARTY_NOTICES.md`.

The MIT License applies to the source code. Third-party assets and libraries retain their own licenses. Imported model data and generated outputs remain the responsibility of the user and are not claimed by this project.

The duck sound effect is a third-party Pixabay sound. It is documented in `assets/sounds/README.md`. Do not redistribute or sell the sound as a standalone asset.

## Known Limits

- Very large OBJ files can make preview and top-view generation slow.
- Top-view generation is currently synchronous and can take noticeable time on dense meshes.
- Measurements are practical reference values, not a substitute for modeler or patternmaker review.
- Imported models are not included in this repository.
