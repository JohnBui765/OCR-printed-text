# Read OCR → offline PWA: brief for Claude Code

Rebuild **Read OCR** (the legacy Python/Tkinter app in `legacy/`) as an installable, offline
PWA hosted on **GitHub Pages**, like the owner's other apps (static `index.html` + `sw.js` +
`manifest.webmanifest`, installed from Edge/Chrome on Windows and "Add to Home Screen" on iPhone).

**Input:** screenshots, phone photos, scans (PNG/JPG/WebP/HEIC where the browser decodes it) and PDFs.
**Output:** a `.docx` that reproduces the page: the same words, the same line breaks, the speaker-label
column, right-margin question numbers (Q1, Q21/Q22…), bold, underline, small caps, rules, spacing.
It must be fast, reliable and look professional. The legacy UI (`samples/` has its output) is not
acceptable, and neither is its output.

Owner's machines: Windows 11 desktop (6-core Ryzen 5 5500, 16 GB) with **Word 2016**, plus an iPhone.
Typical material: Cambridge IELTS/coursebook tapescripts (see `testdata/`).

---

## 1. Diagnosis of the legacy app (all measured; numbers from `bench/` and `prototype/`)

1. **It never opens Word.** The only line that launches anything is `os.startfile(target)` at the end
   of `go()`, and `target` is the output *folder*: it opens a new File Explorer window after every run.
   No COM/win32/subprocess call touches Office; python-docx writes the file directly. The owner's Word
   2016 error pop-ups therefore come from Word/Office itself, not from this code (Office 2016 left
   support on 14 Oct 2025). The PWA must not auto-open files or folders either.
2. **It freezes and loads the CPU.** OCR runs on the Tk main thread, so the window goes "Not Responding"
   for the whole batch; a multithreaded Tesseract build uses up to four cores per page; each PDF page makes a
   temp-PNG round trip plus a new `tesseract.exe` process.
3. **It silently drops text.** Default layout mode `--psm 3` splits these two-column transcripts into
   regions and discards some. On `p2_bikes` it lost "Which is the main point.", "big publicity
   campaign.", "cars." and most speaker labels. On `p1_cottage` all labels were stacked at the top and
   all Q-numbers at the bottom, detached from their lines.
4. **Too little upscaling.** Fixed 2× gives an x-height of ~15 px; Tesseract misreads capital I as `|`
   ("| was hoping"), and `tidy()` only repairs `|` *between* letters.
5. **It destroys typography on purpose.** `tidy()` turns ’ into ', – into -, — into " - ".
6. **No formatting at all** (its own README says so).

## 2. Benchmark (3 pages, 116 lines, hand-made ground truth in `testdata/gt/`)

| Pipeline | Words right | Exact lines |
|---|---|---|
| Legacy settings (std model, 2×, psm 3) | 94.8 % (62 wrong/lost) | 52/116 |
| psm 6, 4× | 98.0 % (24) | 91/116 |
| **Prototype** (below) | **p1 100 %, p3 100 %, p2 98.5 % (7)** | **44/44, 27/27, 37/45** |

* psm 11 is worse still (87–91 %). Full matrix: `bench/matrix.log`.
* The Windows installer's `tessdata` model and **tesseract.js `@tesseract.js-data/eng` 4.0.0_best_int**
  share the same LSTM: identical scores in every configuration. Moving to the browser costs no
  accuracy. `tessdata_fast` is no better (best 97.8 % words vs 98.0 %, though it matched 94/116
  lines vs 91). Use **4.0.0_best_int** (2.95 MB gzipped): same network as the desktop install.
* p2's 7 residual errors are all where an underline touches punctuation or a descender
  ("network." → "network,", "yet" → "vet", "so" → "$0"). Tesseract's confidence does **not** flag
  them (some score 93/100).

## 3. The pipeline that works (`prototype/engine.py`, `prototype/build.py`)

Port this logic to JS (a Web Worker). Thresholds are in units of x-height (`xh`) after upscaling.

1. Decode; flatten alpha onto white; greyscale.
2. **x-height** = modal height of small connected components; **upscale (cubic)** so xh ≈ 26 px
   (factor clamped 1–6).
3. Tesseract LSTM (`--oem 1 --psm 6`, `preserve_interword_spaces=1`), read **hOCR**: word boxes,
   confidences, per-line baseline and slope.
4. **Token repair, nothing more:** standalone `|` → `I`; `|'m/'ll/'d/'ve` → `I'm…`; `|` between letters
   → `I`. Never "correct" the source: p3 really says "judgment. as", "unquestionably" in lower case,
   "Hey," — these must survive.
5. **Geometry:** re-attach a small-caps label that came back as its own line; *label* = short first
   segment at the left edge followed by a gap > max(3 × median word gap, 2.2 xh); *margin label* = short
   last segment after such a gap beyond 80 % of the width; layout is *hanging* if the text column
   starts > 3 xh right of the left edge. OCR lines with no letters or digits are rules, not text.
6. **Underline:** per word, scan rows from baseline + 0.05 xh to baseline + 0.75 xh for one continuous
   dark run ≥ 80 % of the word width (binarise at Otsu + 25). Record where the run ends, and keep a
   trailing `.,?!;:` plain when the run stops before it.
7. **Bold:** stroke = grey ink mass ÷ skeleton length, measured above the baseline, ignoring components
   shorter than 0.45 xh (dots, commas). Ratio to the page median of lowercase words of 3+ letters.
   Bold if ≥ 1.30 (3+ alphanumerics) or ≥ 1.45 (1–2 chars); a neighbour of a bold word is bold at
   ≥ 1.22. Measured: bold words ≥ 1.33, regular 3+ letter words ≤ 1.24.
8. **Small caps / headings:** label cap height ÷ cap height of capitalised body words < 0.92 → emit
   upper case at `body_pt × ratio`. Bold with ratio > 1.35 → heading: larger size, and letter spacing
   that matches the measured width.
9. **Faithful typography:** `'` inside words → ’, `...` → …, a standalone `-`/`—`/`--` → – (UK spaced
   en dash). Make these settings, default on.
10. **Skew:** estimate by projection profile and use it for geometry only (rules, line order).
    **Never resample the image to deskew it** (see section 4).
11. **DOCX, "Replica" mode:** A4, Arial; derive pt-per-px from the em size implied by Arial widths of
    long lines; body 11 pt, then **shrink until every original line fits** (Arial Bold labels run wider
    than many source faces: without this, Word wraps lines twice). One paragraph per turn; hanging
    indent; label + tab; original line breaks as `<w:br/>`; margin label after a tab to a **right tab
    stop** at the content's right edge; exact line spacing = measured pitch; space-before = measured
    extra gap; justify when most lines reach the right edge; a rule = empty paragraph with a dashed
    bottom border. `samples/*.replica-render.png` shows the result (rendered with LibreOffice).

## 4. Rejected approaches (measured worse; do not retry without new evidence)

* psm 3 / psm 11 on transcripts: drop text.
* Rotating the image to deskew before OCR: p2 fell to 86.5 %, p3 lost three commas.
* Erasing every long horizontal run before OCR: in blurred upscales neighbouring letters fuse into
  long runs, so T-bars and hyphens vanish (p1 fell to 95.7 %: "The" → "1he").
* Re-reading underlined lines with psm 7 after erasing the underline: garbles small-caps labels
  ("JAKE:" → "sake:"); net loss.
* Bold/italic by matching against rendered Arial (NCC): source faces are heavier than Arial, so
  everything scores "bold".
* **Italic from small screenshots** (x-height ≈ 7 px): the italic Q-numbers measured only 2–6° of
  slant, inside the range upright body words also produced (−6° to 18°). Not separable. Do not auto-italicise images; give a one-tap
  toggle. For digital PDFs, read bold/italic **exactly** from font names via pdf.js.

## 5. Known defects in the prototype output

* p1: "Chervil Cottage" lost its underline; "It" ("That's right. It grows…") got a false one; the
  underline runs on under "28th," and "week." although the original stops before the punctuation.
* p2: the dashed rule is not yet detected. The page is tilted 0.6° and Tesseract folds the dashes into
  a word "~~" inside a text line, which hides them from rule detection. Drop symbol-only *words*
  (not just lines) first, then detect the rule.
* p2: the 7 underline-punctuation errors above.
* Margin numbers are emitted upright (they are italic in the original; see section 4).
* "Flow" mode (editable paragraphs without original line breaks) is not built yet.

## 6. Target architecture

* Static site for GitHub Pages: `index.html`, `app.js`, `engine.worker.js`, `sw.js` (cache-first,
  versioned cache name), `manifest.webmanifest`, icons. **Vendor every library locally**: no CDN at
  runtime; the app must work fully offline after the first visit.
* OCR: **tesseract.js v7** (LSTM-only SIMD/relaxed-SIMD core, worker pool of about cores − 2, pages
  in parallel), model `4.0.0_best_int/eng.traineddata.gz` from `@tesseract.js-data/eng`, cached by the
  service worker.
* PDF: **pdfjs-dist**. A page with a real text layer → take text, positions and font names (exact
  bold/italic), no OCR. A scanned page → render to canvas at about 300 DPI → OCR.
* DOCX: the `docx` npm package (v9) or hand-built OOXML with JSZip. Needs hanging indents, right tab
  stops, `<w:br/>`, exact line spacing, run bold/underline/size/character spacing, paragraph borders.
  Output must open cleanly in **Word 2016**.
* Also "Copy formatted" (HTML on the clipboard) for pasting straight into Word with formatting.
* UI: professional and calm. Drop/paste/pick files (camera on iPhone); a queue with per-page progress;
  a review screen with the source image beside the result, inline text editing and tap-to-toggle
  B / I / U per word; export `.docx`. Modes: **Replica** (line for line, default) and **Flow**.
  Settings: typography, font, page size. Never block the UI thread; never auto-open files or folders.
* Accept an approved design before polishing; the owner checks results on both Windows and iPhone.

## 7. Acceptance tests

* `testdata/*.png` against `testdata/gt/*.txt`, scored like `bench/score.py` (word-multiset accuracy and
  exact-line matches; label + text + margin label joined with single spaces).
* Must reach: **p1 100 % and 44/44 lines; p3 100 % and 27/27; p2 ≥ 98.5 % and ≥ 37/45** (target 100 %).
* Bold must be exactly "PART 1" (p1) and every "Speaker N (Name):" label (p3), nothing else.
* Underlines on p1, exactly: "It’s free the week / beginning the 28th", "£550 for the week",
  "Chervil Cottage", "The building was built as a garage.", "garden",
  "you get to it from the living room through French doors", "parking isn’t a problem – there’s /
  plenty of room at the front for that".
* The generated `.docx` must open in Word 2016 without a repair prompt, with no line wrapped twice.
* Speed target: under 3 s per screenshot page on the owner's desktop.

## 8. Running the prototype (reference only)

```
pip install pytesseract pillow opencv-python scikit-image python-docx lxml numpy
# eng.traineddata: unzip @tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz into a folder
python prototype/run_all.py . path/to/that/folder      # writes out/*.docx and prints scores
```
