# img2word — offline image/PDF → Word

Everything runs on your own computer. Nothing is uploaded anywhere.

## Install once (Windows, ~10 minutes)

**1. Python** — https://www.python.org/downloads/
Tick **"Add python.exe to PATH"** on the first screen. Leave "tcl/tk" ticked (that's the window).

**2. Tesseract OCR** — https://github.com/UB-Mannheim/tesseract/wiki
Download `tesseract-ocr-w64-setup-*.exe`. Install to the default folder
(`C:\Program Files\Tesseract-OCR`) — the script looks there automatically.
On the "Choose Components" screen, open **Additional language data** and tick
**Vietnamese** if you ever want to OCR Vietnamese as well as English.

**3. The Python libraries** — open Command Prompt and paste:

```
pip install pytesseract pillow pymupdf python-docx
```

## Run it

Double-click `run.bat`, or double-click `img2word.py`, or:

```
python img2word.py                          # opens the window
python img2word.py scan.pdf photo.jpg -o C:\Users\You\Desktop
```

Add files → choose an output folder → **CONVERT**. One `.docx` per input file.

## Settings that actually matter

| Setting | Use it when |
|---|---|
| **Layout: Auto** | Normal full pages. Leave it here. |
| **Layout: Single column** | The page has two columns and they come out interleaved. |
| **Layout: One block** | You cropped a snippet out of a bigger page. |
| **Layout: Sparse text** | Labels on a diagram, scattered boxes. |
| **DPI 300 → 400/500** | Small print or a poor scan. Slower, more accurate. |
| **Force OCR** | A PDF has a text layer but it's garbage (bad earlier OCR). |
| **Language: eng+vie** | Mixed English/Vietnamese page (needs the Vietnamese pack). |

## Two things to know

**Digital PDFs never touch OCR.** If the PDF already contains real text, the script
copies it verbatim — perfect accuracy. For those files you don't even need this tool:
Word itself opens a PDF directly (File → Open → select the .pdf).

**Input quality decides everything.** A flat 300 DPI scan gives near-perfect English.
A blurry, angled, badly lit phone photo produces word salad and no software fixes that.
If you must use a phone, use a scanner app (Microsoft Lens, Adobe Scan, CamScanner) so
the page gets flattened and de-skewed before it reaches Tesseract.

Layout is not preserved. You get clean paragraphs, not columns, tables, or formatting.
