# Read OCR (PWA rebuild)

Offline image/PDF → Word converter that keeps the page's format: line breaks, speaker labels,
margin question numbers, bold, underline, rules.

**Status:** specification and working prototype. The app itself is to be built from `CLAUDE.md`.

| Folder | What it holds |
|---|---|
| `CLAUDE.md` | The full brief: diagnosis, measured results, pipeline, acceptance tests |
| `legacy/` | The old Python/Tkinter app (`img2word.py`, `run.bat`, its README) |
| `prototype/` | The format-aware engine that passed the tests (Python, reference only) |
| `bench/` | Scoring script and the full model × zoom × layout benchmark |
| `testdata/` | Three real pages and their word-for-word ground truth |
| `samples/` | Legacy output vs prototype output (`.docx` + rendered previews) |
