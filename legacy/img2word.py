#!/usr/bin/env python3
"""
img2word.py - Offline image/PDF -> Word (.docx) converter.

Runs entirely on your own machine. No internet, no upload, no account.

WHAT IT DOES
  * Digital PDFs (the text is already in the file): copies the real text.
    No OCR, so no OCR mistakes.
  * Scanned PDFs and photos/screenshots: renders each page at high DPI and
    runs Tesseract OCR on it.
  * Rebuilds paragraphs. Raw OCR breaks a line every time the page does;
    this stitches those lines back into proper paragraphs and repairs
    words split by an end-of-line hyphen ("vocabu-lary" -> "vocabulary").
  * Writes a .docx you can edit in Word.

USAGE
  Double-click, or:      python img2word.py
  Command line:          python img2word.py scan1.pdf photo.jpg -o out_folder

SETUP: see the accompanying README.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# ----------------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------------

MISSING: list[str] = []

try:
    import pytesseract
    from PIL import Image, ImageOps
except ImportError:
    MISSING.append("pytesseract pillow")

try:
    import pymupdf as fitz          # modern import name
except ImportError:
    try:
        import fitz                 # older PyMuPDF releases
    except ImportError:
        MISSING.append("pymupdf")

try:
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt
except ImportError:
    MISSING.append("python-docx")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_EXT = {".pdf"}

# Where Tesseract usually lives once installed.
TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
]


def locate_tesseract() -> str | None:
    """Find the Tesseract binary and point pytesseract at it."""
    found = shutil.which("tesseract")
    if not found:
        for candidate in TESSERACT_CANDIDATES:
            if candidate and os.path.isfile(candidate):
                found = candidate
                break
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
    return found


# ----------------------------------------------------------------------------
# Text clean-up: turn OCR lines back into paragraphs
# ----------------------------------------------------------------------------

BULLET_RE = re.compile(r"^\s*(?:[-\u2013\u2014\u2022*\u00b7\u25cf\u25a0\u25aa]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)])\s+")
# "MAN:", "WOMAN:", "INTERVIEWER:", "DR BROWN:" - a new turn in a transcript.
SPEAKER_RE = re.compile(r"^[A-Z][A-Z0-9 .'\u2019-]{0,24}:(?:\s|$)")
SENTENCE_END_RE = re.compile(r"[.!?:;\u2026]['\"\u2019\u201d)\]]?\s*$")
# Characters Tesseract commonly mangles in otherwise clean English text.
OCR_FIXES = [
    ("\u2018", "'"), ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
    ("\u2013", "-"), ("\u2014", " - "), ("\ufb01", "fi"), ("\ufb02", "fl"),
    ("|", "I"),
]


HYPHEN_WORD_RE = re.compile(r"\b[A-Za-z]+-[A-Za-z]+\b")


def _join_buffer(lines: list[str], real_hyphens: set[str] | None = None) -> str:
    """
    Glue buffered lines into one paragraph, healing hyphen-split words.

    A line-end hyphen is usually an artefact ("vocabu-|lary"), but sometimes it
    is a real compound that happened to break there ("mixed-|handedness"). If
    the same compound appears hyphenated elsewhere on the page, keep the hyphen.
    """
    real_hyphens = real_hyphens or set()
    out = ""
    for part in lines:
        if not out:
            out = part
        elif out.endswith("-") and part[:1].islower():
            stem = re.search(r"([A-Za-z]+)-$", out)
            tail = re.match(r"([A-Za-z]+)", part)
            prefix = f"{stem.group(1)}-".lower() if stem else ""
            genuine = bool(prefix) and any(h.startswith(prefix) for h in real_hyphens)
            out = out + part if genuine else out[:-1] + part
        else:
            out = out + " " + part
    return re.sub(r"\s{2,}", " ", out).strip()


def rebuild_paragraphs(text: str, smart_join: bool = True) -> list[str]:
    """
    Convert raw OCR/PDF text into a list of paragraphs.

    Heuristic: inside a justified block every line runs to roughly the same
    width, so a *short* line that also ends in sentence punctuation is almost
    always the last line of a paragraph. Bullets and headings break out on
    their own.
    """
    raw = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    if not smart_join:
        return [ln.strip() for ln in raw if ln.strip()]

    real_hyphens = {w.lower() for w in HYPHEN_WORD_RE.findall(text)}
    widths = sorted(len(ln) for ln in raw if ln.strip())
    if not widths:
        return []
    if len(widths) < 5:                 # too little text for a width heuristic
        return [ln.strip() for ln in raw if ln.strip()]
    median = widths[len(widths) // 2]
    short_threshold = median * 0.72

    paragraphs: list[str] = []
    buffer: list[str] = []

    for i, line in enumerate(raw):
        stripped = line.strip()

        if not stripped:                                    # blank line
            if buffer:
                follower = ""
                for later in raw[i + 1:]:
                    if later.strip():
                        follower = later.strip()
                        break
                # Underlines and stray marks make Tesseract emit blank lines
                # inside a paragraph. Only treat one as a real break when the
                # text either finishes a sentence or restarts with a capital.
                mid_sentence = (
                    follower
                    and follower[:1].islower()
                    and not SENTENCE_END_RE.search(buffer[-1])
                    and not BULLET_RE.match(follower)
                    and not SPEAKER_RE.match(follower)
                )
                if not mid_sentence:
                    paragraphs.append(_join_buffer(buffer, real_hyphens))
                    buffer = []
            continue

        is_bullet = bool(BULLET_RE.match(stripped))
        is_speaker = bool(SPEAKER_RE.match(stripped))
        is_heading = (
            len(stripped) < short_threshold
            and not SENTENCE_END_RE.search(stripped)
            and (stripped.isupper() or stripped.endswith(":"))
        )

        if (is_bullet or is_speaker or is_heading) and buffer:   # start a new block
            paragraphs.append(_join_buffer(buffer, real_hyphens))
            buffer = []

        buffer.append(stripped)

        nxt = ""
        for later in raw[i + 1:]:
            if later.strip():
                nxt = later.strip()
                break

        ends_here = (
            is_heading
            or not nxt
            or bool(BULLET_RE.match(nxt))
            or bool(SPEAKER_RE.match(nxt))
            or (len(stripped) < short_threshold and bool(SENTENCE_END_RE.search(stripped)))
        )
        if ends_here:
            paragraphs.append(_join_buffer(buffer, real_hyphens))
            buffer = []

    if buffer:
        paragraphs.append(_join_buffer(buffer, real_hyphens))

    return [p for p in paragraphs if p]


def tidy(text: str) -> str:
    for bad, good in OCR_FIXES:
        if bad == "|":
            text = re.sub(r"(?<=[A-Za-z])\|(?=[A-Za-z])", good, text)
        else:
            text = text.replace(bad, good)
    return text


# ----------------------------------------------------------------------------
# OCR
# ----------------------------------------------------------------------------

def preprocess(img: "Image.Image") -> "Image.Image":
    """Grayscale, auto-contrast, upscale small images. Cheap but effective."""
    img = ImageOps.exif_transpose(img).convert("L")
    img = ImageOps.autocontrast(img)
    if img.width < 1200:
        factor = max(2, 1600 // max(img.width, 1))
        img = img.resize((img.width * factor, img.height * factor), Image.LANCZOS)
    return img


def ocr_image(img: "Image.Image", lang: str = "eng", psm: int = 3) -> str:
    config = f"--oem 3 --psm {psm}"
    return pytesseract.image_to_string(preprocess(img), lang=lang, config=config)


# ----------------------------------------------------------------------------
# Per-file extraction
# ----------------------------------------------------------------------------

MIN_NATIVE_CHARS = 120   # below this, treat the PDF page as a scan


def extract_pdf(path: Path, lang: str, dpi: int, psm: int, force_ocr: bool,
                log=print) -> list[list[str]]:
    """Return a list of pages; each page is a list of paragraphs."""
    pages: list[list[str]] = []
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, start=1):
            native = "" if force_ocr else page.get_text("text")
            if len(native.strip()) >= MIN_NATIVE_CHARS:
                log(f"   page {number}/{len(doc)}: real text layer, copied directly")
                text = native
            else:
                log(f"   page {number}/{len(doc)}: no text layer, running OCR at {dpi} DPI")
                pix = page.get_pixmap(dpi=dpi)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_name = tmp.name
                pix.save(tmp_name)
                try:
                    with Image.open(tmp_name) as img:
                        text = ocr_image(img, lang=lang, psm=psm)
                finally:
                    os.unlink(tmp_name)
            pages.append(rebuild_paragraphs(tidy(text)))
    return pages


def extract_image(path: Path, lang: str, psm: int, log=print) -> list[list[str]]:
    log("   running OCR")
    with Image.open(path) as img:
        text = ocr_image(img, lang=lang, psm=psm)
    return [rebuild_paragraphs(tidy(text))]


# ----------------------------------------------------------------------------
# Word output
# ----------------------------------------------------------------------------

def write_docx(pages: list[list[str]], out_path: Path, font: str = "Calibri",
               size_pt: int = 11, page_breaks: bool = True) -> Path:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = font
    style.font.size = Pt(size_pt)

    for index, paragraphs in enumerate(pages):
        if index and page_breaks:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        if not paragraphs:
            doc.add_paragraph("[no text recognised on this page]")
            continue
        for para in paragraphs:
            doc.add_paragraph(para)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def convert(path: Path, out_dir: Path, lang: str = "eng", dpi: int = 300,
            psm: int = 3, force_ocr: bool = False, page_breaks: bool = True,
            log=print) -> Path:
    suffix = path.suffix.lower()
    log(f"-> {path.name}")
    if suffix in PDF_EXT:
        pages = extract_pdf(path, lang, dpi, psm, force_ocr, log=log)
    elif suffix in IMAGE_EXT:
        pages = extract_image(path, lang, psm, log=log)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    out_path = out_dir / (path.stem + ".docx")
    counter = 2
    while out_path.exists():
        out_path = out_dir / f"{path.stem} ({counter}).docx"
        counter += 1

    write_docx(pages, out_path, page_breaks=page_breaks)
    words = sum(len(p.split()) for page in pages for p in page)
    log(f"   saved {out_path.name}  ({len(pages)} page(s), ~{words} words)")
    return out_path


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

PSM_CHOICES = {
    "Auto (normal pages)": 3,
    "Single column of text": 4,
    "One block of text (a cropped snippet)": 6,
    "Sparse text (labels, diagrams)": 11,
}


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Image / PDF -> Word  (offline)")
    root.geometry("820x600")
    root.minsize(760, 520)   # below this the controls would start to clip

    files: list[Path] = []
    out_dir = tk.StringVar(value=str(Path.home() / "Desktop"))
    lang = tk.StringVar(value="eng")
    dpi = tk.IntVar(value=300)
    psm_label = tk.StringVar(value="Auto (normal pages)")
    force_ocr = tk.BooleanVar(value=False)
    page_breaks = tk.BooleanVar(value=True)

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    listbox = tk.Listbox(frame, height=8, selectmode="extended")
    listbox.pack(fill="both", expand=True, pady=(0, 8))

    console = tk.Text(frame, height=12, wrap="word", state="disabled",
                      background="#111", foreground="#ddd", insertbackground="#ddd")
    console.pack(fill="both", expand=True, pady=(8, 0))

    def log(message: str = "") -> None:
        console.configure(state="normal")
        console.insert("end", message + "\n")
        console.see("end")
        console.configure(state="disabled")
        root.update_idletasks()

    def add_files() -> None:
        chosen = filedialog.askopenfilenames(
            title="Choose images or PDFs",
            filetypes=[("Images and PDFs", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
                       ("All files", "*.*")])
        for item in chosen:
            path = Path(item)
            if path not in files:
                files.append(path)
                listbox.insert("end", path.name)

    def clear_files() -> None:
        files.clear()
        listbox.delete(0, "end")

    def choose_out() -> None:
        chosen = filedialog.askdirectory(title="Where should the Word files go?")
        if chosen:
            out_dir.set(chosen)

    def go() -> None:
        if not files:
            messagebox.showinfo("Nothing to do", "Add at least one image or PDF first.")
            return
        binary = locate_tesseract()
        if not binary:
            log("!! Tesseract was not found. Digital PDFs will still work; scans and photos will not.")
        target = Path(out_dir.get())
        log("")
        made = []
        for path in list(files):
            try:
                made.append(convert(path, target, lang.get(), dpi.get(),
                                    PSM_CHOICES[psm_label.get()], force_ocr.get(),
                                    page_breaks.get(), log=log))
            except Exception as exc:  # noqa: BLE001
                log(f"   FAILED: {exc}")
        log(f"\nDone. {len(made)} file(s) written to {target}")
        if made:
            try:
                os.startfile(target)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    row1 = ttk.Frame(frame); row1.pack(fill="x")
    ttk.Button(row1, text="Add files...", command=add_files).pack(side="left")
    ttk.Button(row1, text="Clear list", command=clear_files).pack(side="left", padx=6)
    ttk.Button(row1, text="Output folder...", command=choose_out).pack(side="left")
    ttk.Label(row1, textvariable=out_dir, foreground="#555").pack(side="left", padx=8)

    row2 = ttk.Frame(frame); row2.pack(fill="x", pady=8)
    ttk.Label(row2, text="Layout:").pack(side="left")
    ttk.Combobox(row2, textvariable=psm_label, values=list(PSM_CHOICES), width=32,
                 state="readonly").pack(side="left", padx=(4, 12))
    ttk.Label(row2, text="Language:").pack(side="left")
    ttk.Combobox(row2, textvariable=lang, values=["eng", "vie", "eng+vie"], width=9,
                 state="readonly").pack(side="left", padx=(4, 12))
    ttk.Label(row2, text="DPI:").pack(side="left")
    ttk.Spinbox(row2, from_=150, to=600, increment=50, textvariable=dpi,
                width=6).pack(side="left", padx=4)

    row3 = ttk.Frame(frame); row3.pack(fill="x")
    # CONVERT is packed FIRST so it always reserves its space on the right;
    # anything added later can only use what is left over, never push it off.
    ttk.Button(row3, text="CONVERT", command=go).pack(side="right", padx=(12, 0))
    ttk.Checkbutton(row3, text="Force OCR even when the PDF already has text",
                    variable=force_ocr).pack(side="left")
    ttk.Checkbutton(row3, text="Page break between pages",
                    variable=page_breaks).pack(side="left", padx=12)

    log("Ready. Add files, pick an output folder, press CONVERT.")
    log("Tesseract: " + (locate_tesseract() or "NOT FOUND (digital PDFs still work)"))
    root.mainloop()


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def main() -> int:
    if MISSING:
        print("Missing libraries. Run this once:\n")
        print("   pip install " + " ".join(MISSING))
        return 1

    args = [a for a in sys.argv[1:]]
    if not args:
        run_gui()
        return 0

    out_dir = Path.cwd()
    if "-o" in args:
        index = args.index("-o")
        out_dir = Path(args[index + 1])
        del args[index:index + 2]

    locate_tesseract()
    for item in args:
        try:
            convert(Path(item), out_dir)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
