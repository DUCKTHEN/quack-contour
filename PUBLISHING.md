# Publishing Checklist

Before publishing Quack Contour:

- Confirm no private OBJ, FBX, ZPRJ, PNG, JSON, or client data is staged.
- Confirm `outputs/` and `uploads/` will stage only `.gitkeep`.
- Run `git add --dry-run .` and confirm private model/output files are not listed.
- Run `python -m py_compile app.py obj_section_tool.py compare_avatar_sections.py avatar_section_overlay.py measure_avatar_bust.py`.
- Start the app with `python app.py`.
- Open `http://127.0.0.1:8765/`.
- Confirm the duck favicon and UI load.
- Import a small test OBJ locally.
- Generate a top-view PNG/JSON from non-private test data.

Suggested git check:

```powershell
git status --short
git diff --check
```

Do not publish user model files. The repository should contain code, templates, and app assets only.
