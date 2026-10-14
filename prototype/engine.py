"""
NOTE FOR CLAUDE CODE: functions whose docstring starts with REJECTED are kept only as a record.
They were measured to make results worse (see CLAUDE.md, "Rejected approaches"). Do not wire them back in.

Prototype: layout- and format-aware OCR for printed pages (transcripts, worksheets).

Throwaway evaluation code. It answers one question: can an offline pipeline
recover the *format* of a page (label column, margin numbers, bold, italic,
underline, small caps, separators, line breaks) and not just the words?
The real app would run the same logic in the browser on Tesseract.js output.
"""
from __future__ import annotations

import re
import statistics as st
from dataclasses import dataclass, field

import cv2
import numpy as np
import pytesseract
from lxml import html as lhtml
from PIL import Image, ImageOps

# ---------------------------------------------------------------- data model


@dataclass
class Word:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    conf: float
    bold: bool = False
    italic: bool = False
    underline: bool = False
    caps_ratio: float = 1.0      # cap height of this word / body cap height
    stroke: float = 0.0
    ul_x1: int = 0               # where the underline stops (to leave trailing punctuation plain)


@dataclass
class Line:
    words: list[Word]
    x0: int
    y0: int
    x1: int
    y1: int
    baseline: float               # y of the baseline at the line's left edge
    slope: float
    label: list[Word] = field(default_factory=list)
    margin: list[Word] = field(default_factory=list)

    def base_at(self, x: float) -> float:
        return self.baseline + self.slope * (x - self.x0)


# ---------------------------------------------------------------- image prep


def load_gray(path: str) -> np.ndarray:
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im.convert("RGBA"))
        im = bg
    return np.array(im.convert("L"))


def skew_angle(gray: np.ndarray) -> float:
    """Projection-profile skew estimate in degrees (geometry only; the image is never resampled)."""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape
    best, best_a = -1.0, 0.0
    for a in np.arange(-3.0, 3.01, 0.1):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
        r = cv2.warpAffine(bw, M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        prof = r.sum(axis=1).astype(float)
        score = float(((prof[1:] - prof[:-1]) ** 2).sum())
        if score > best:
            best, best_a = score, a
    return float(best_a)


def find_rules(bw: np.ndarray, lines: list, xh: float, angle_deg: float) -> list[float]:
    """Dashed/solid rules: many flat pieces, outside words, collinear along the page skew.
    Returns the rule's y at the page centre."""
    n, lab, stats, cent = cv2.connectedComponentsWithStats(bw, 8)
    boxes = [(w.x0, w.y0, w.x1, w.y1) for l in lines for w in l.label + l.words + l.margin]
    H, W = bw.shape
    cx = W / 2
    t = np.tan(np.radians(-angle_deg))
    pieces = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if h <= max(2, 0.3 * xh) and w >= 0.15 * xh and w >= 2 * h:
            mx, my = x + w / 2, y + h / 2
            if any(bx0 <= mx <= bx1 and by0 <= my <= by1 for bx0, by0, bx1, by1 in boxes):
                continue
            pieces.append((my - t * (mx - cx), x, x + w))
    pieces.sort()
    rules, i = [], 0
    while i < len(pieces):
        j = i
        while j + 1 < len(pieces) and pieces[j + 1][0] - pieces[i][0] < 0.4 * xh:
            j += 1
        grp = pieces[i:j + 1]
        span = max(p[2] for p in grp) - min(p[1] for p in grp)
        if span > 0.5 * W and len(grp) >= 6:
            rules.append(float(np.median([p[0] for p in grp])))
        i = j + 1
    return rules


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """REJECTED (measured worse, see CLAUDE.md). Projection-profile deskew: the rotation that makes text rows sharpest."""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = bw.shape
    best, best_a = -1.0, 0.0
    for a in np.arange(-3.0, 3.01, 0.1):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
        r = cv2.warpAffine(bw, M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        prof = r.sum(axis=1).astype(float)
        score = float(((prof[1:] - prof[:-1]) ** 2).sum())
        if score > best:
            best, best_a = score, a
    if abs(best_a) < 0.05:
        return gray, 0.0
    M = cv2.getRotationMatrix2D((w / 2, h / 2), best_a, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255), float(best_a)


def estimate_xheight(gray: np.ndarray) -> float:
    """Median height of small connected components = x-height of the body text."""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    hs = [h for (_, _, w, h, a) in stats[1:] if 3 <= h <= 60 and w <= 2.2 * h and a >= 6]
    if not hs:
        return 10.0
    hs = np.array(hs)
    lo = hs[hs <= np.percentile(hs, 55)]
    vals, counts = np.unique(lo, return_counts=True)
    return float(vals[np.argmax(counts)])


def upscale(gray: np.ndarray, target_xh: float = 26.0) -> tuple[np.ndarray, float]:
    xh = estimate_xheight(gray)
    f = float(np.clip(target_xh / xh, 1.0, 6.0))
    if abs(f - 1.0) < 0.15:
        return gray.copy(), 1.0
    up = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return up, f


def remove_rules(gray: np.ndarray, xh: float) -> np.ndarray:
    """REJECTED (measured worse, see CLAUDE.md). Erase underlines and rules before OCR (they are measured separately for formatting).
    Ink that continues above AND below the rule (a descender crossing it) is kept."""
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, bw = cv2.threshold(gray, min(250, t + 25), 255, cv2.THRESH_BINARY_INV)
    k = max(8, int(1.4 * xh))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    horiz = cv2.dilate(horiz, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    th = max(2, int(0.18 * xh))
    above = np.zeros_like(bw); below = np.zeros_like(bw)
    above[th + 1:, :] = bw[:-(th + 1), :]
    below[:-(th + 1), :] = bw[th + 1:, :]
    crossing = (above > 0) & (below > 0) & (horiz > 0)
    out = gray.copy()
    out[(horiz > 0) & ~crossing] = 255
    return out


def reread_underlined(gray: np.ndarray, bw: np.ndarray, lines: list, xh: float, tessdata: str) -> int:
    """REJECTED (measured worse, see CLAUDE.md). Underlines touching the baseline corrupt punctuation and descenders ('network.' -> 'network,').
    Erase the underline only in the band below this line's baseline, then re-read just this line."""
    changed = 0
    for l in lines:
        ws = l.label + l.words + l.margin
        if not any(w.underline for w in ws):
            continue
        y0 = max(0, l.y0 - int(0.3 * xh)); y1 = min(gray.shape[0], int(l.base_at(l.x0)) + int(1.0 * xh))
        x0 = max(0, l.x0 - int(xh)); x1 = min(gray.shape[1], l.x1 + int(xh))
        g = gray[y0:y1, x0:x1].copy(); b = bw[y0:y1, x0:x1]
        for w in ws:
            if not w.underline:
                continue
            base = int(round(l.base_at((w.x0 + w.x1) / 2))) - y0
            ya, yb = base + max(1, int(0.08 * xh)), min(g.shape[0] - 1, base + int(0.75 * xh))
            xa, xb = w.x0 - x0, min(g.shape[1], max(w.x1, w.ul_x1) - x0 + 2)
            band = b[ya:yb + 1, xa:xb] > 0
            if band.size == 0:
                continue
            rows = np.where(band.mean(axis=1) > 0.6)[0]           # the underline rows
            if rows.size == 0:
                continue
            r0, r1 = ya + rows.min() - 1, ya + rows.max() + 1
            above = b[max(0, r0 - 2), xa:xb] > 0
            below = b[min(b.shape[0] - 1, r1 + 2), xa:xb] > 0
            keep = above & below                                    # descender crossing the rule
            for yy in range(max(0, r0), min(g.shape[0], r1 + 1)):
                seg = g[yy, xa:xb]
                seg[~keep] = 255
        cfg = f"--tessdata-dir {tessdata} --oem 1 --psm 7"
        d = pytesseract.image_to_data(Image.fromarray(g), config=cfg, output_type=pytesseract.Output.DICT)
        new = [(t.strip(), d["left"][i] + x0, d["left"][i] + d["width"][i] + x0)
               for i, t in enumerate(d["text"]) if t.strip()]
        if len(new) != len(ws):
            continue                                                # can't align safely: keep first reading
        for w, (t, a, c) in zip(ws, new):
            t = fix_token(t)
            if t != w.text and abs(a - w.x0) < xh and abs(c - w.x1) < xh:
                w.text = t
                changed += 1
    return changed


# ---------------------------------------------------------------- OCR (hOCR)

_BBOX = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
_BASE = re.compile(r"baseline (-?[\d.]+) (-?[\d.]+)")
_CONF = re.compile(r"x_wconf (\d+)")


def ocr_lines(img: np.ndarray, tessdata: str, psm: int = 6) -> list[Line]:
    cfg = f"--tessdata-dir {tessdata} --oem 1 --psm {psm} -c preserve_interword_spaces=1"
    hocr = pytesseract.image_to_pdf_or_hocr(Image.fromarray(img), extension="hocr", config=cfg)
    doc = lhtml.fromstring(hocr)
    lines: list[Line] = []
    for ln in doc.xpath("//span[@class='ocr_line' or @class='ocr_caption' or @class='ocr_header' or @class='ocr_textfloat']"):
        title = ln.get("title", "")
        lx0, ly0, lx1, ly1 = map(int, _BBOX.search(title).groups())
        m = _BASE.search(title)
        slope, off = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
        words = []
        for w in ln.xpath(".//span[@class='ocrx_word']"):
            t = "".join(w.itertext()).strip()
            if not t:
                continue
            wt = w.get("title", "")
            x0, y0, x1, y1 = map(int, _BBOX.search(wt).groups())
            c = _CONF.search(wt)
            words.append(Word(t, x0, y0, x1, y1, float(c.group(1)) if c else 0.0))
        if words:
            lines.append(Line(words, lx0, ly0, lx1, ly1, ly1 + off, slope))
    lines.sort(key=lambda l: (l.y0 + l.y1) / 2)
    return lines


# ---------------------------------------------------------------- token fixes

def fix_token(t: str) -> str:
    """Repairs for confusions Tesseract makes on sans-serif text. No 'improving' of the source."""
    if t == "|":
        return "I"
    t = re.sub(r"^\|(?=['’](m|ll|d|ve)\b)", "I", t)          # |'m -> I'm
    t = re.sub(r"(?<=[A-Za-z])\|(?=[A-Za-z])", "I", t)
    return t


# ---------------------------------------------------------------- layout

def merge_label_lines(lines: list[Line], xh: float) -> list[Line]:
    """A small-caps label sitting slightly off its text line can come back as its own line: re-attach it."""
    out: list[Line] = []
    for ln in lines:
        text_right = max(w.x1 for w in ln.words)
        is_lonely_label = (len(ln.words) <= 3 and ln.words[-1].text.endswith(":") and
                           text_right < min(l.x0 for l in lines) + 12 * xh)
        if is_lonely_label:
            best = None
            for other in lines:
                if other is ln:
                    continue
                if other.x0 > text_right and abs(((other.y0 + other.y1) / 2) - ((ln.y0 + ln.y1) / 2)) < 1.2 * xh:
                    best = other
                    break
            if best is not None:
                best.words = ln.words + best.words
                best.x0 = min(best.x0, ln.x0)
                continue
        out.append(ln)
    return out


def split_columns(lines: list[Line], xh: float) -> dict:
    """Find the label column, the text column and the right-margin column from word geometry."""
    gaps = []
    for ln in lines:
        for a, b in zip(ln.words, ln.words[1:]):
            gaps.append(b.x0 - a.x1)
    word_gap = st.median(gaps) if gaps else xh
    big = max(3.0 * word_gap, 2.2 * xh)
    left_edge = min(l.words[0].x0 for l in lines)
    page_right = max(l.words[-1].x1 for l in lines)
    starts = []
    for ln in lines:
        ws = ln.words
        # label: short first segment at the left edge, ended by a wide gap
        for k in range(1, min(4, len(ws))):
            gap = ws[k].x0 - ws[k - 1].x1
            if gap > big and ws[0].x0 < left_edge + 2 * xh:
                ln.label, ln.words = ws[:k], ws[k:]
                break
        ws = ln.words
        # margin: short last segment far to the right, after a wide gap
        for k in range(len(ws) - 1, max(0, len(ws) - 3), -1):
            gap = ws[k].x0 - ws[k - 1].x1
            if gap > big and ws[k].x0 > left_edge + 0.80 * (page_right - left_edge):
                ln.margin, ln.words = ws[k:], ws[:k]
                break
        if ln.words:
            starts.append(ln.words[0].x0)
    label_lines = [l for l in lines if l.label]
    text_x = st.median([l.words[0].x0 for l in label_lines if l.words]) if label_lines else left_edge
    body_right = sorted(w.x1 for l in lines for w in l.words[-1:])[-max(1, len(lines) // 10)] if lines else page_right
    return {"left": left_edge, "text_x": text_x, "body_right": body_right,
            "page_right": page_right, "word_gap": word_gap, "hanging": bool(label_lines) and text_x - left_edge > 3 * xh}


# ---------------------------------------------------------------- formatting

def binarize(gray: np.ndarray) -> np.ndarray:
    t, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, bw = cv2.threshold(gray, min(250, t + 25), 255, cv2.THRESH_BINARY_INV)
    return bw


def detect_underlines(bw: np.ndarray, lines: list[Line], xh: float) -> None:
    for ln in lines:
        for w in ln.label + ln.words + ln.margin:
            width = w.x1 - w.x0
            if width < 0.6 * xh:
                continue
            base = ln.base_at((w.x0 + w.x1) / 2)
            ya = int(base + 0.05 * xh)
            yb = int(min(bw.shape[0] - 1, base + 0.75 * xh))
            best, best_end = 0, 0
            x_hi = min(bw.shape[1], w.x1 + int(xh))
            for y in range(ya, yb + 1):
                row = bw[y, w.x0:x_hi] > 0
                cur = 0
                for i, v in enumerate(row):
                    cur = cur + 1 if v else 0
                    if cur > best:
                        best, best_end = cur, w.x0 + i + 1
            w.underline = min(best, width) >= 0.80 * width or (best >= 0.6 * width and best_end >= w.x1 - 0.2 * xh)
            w.ul_x1 = best_end


def stroke_width(bw_crop: np.ndarray) -> float:
    """REJECTED (superseded by stroke_mass: binarised area/skeleton was too coarse for bold)."""
    if bw_crop.size == 0 or bw_crop.max() == 0:
        return 0.0
    from skimage.morphology import skeletonize
    skel = skeletonize(bw_crop > 0)
    L = int(skel.sum())
    return float((bw_crop > 0).sum()) / max(L, 1)


def stroke_mass(gray_crop: np.ndarray, bw_crop: np.ndarray, xh: float) -> float:
    """Sub-pixel stroke thickness: grey ink mass / skeleton length, ignoring dots and commas."""
    from skimage.morphology import skeletonize
    b = (bw_crop > 0).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(b, 8)
    keep = np.zeros_like(b)
    for i in range(1, n):
        if stats[i][3] >= 0.45 * xh:
            keep[lab == i] = 1
    if keep.sum() == 0:
        return 0.0
    sk = skeletonize(keep > 0).sum()
    region = cv2.dilate(keep, np.ones((3, 3), np.uint8))
    ink = (255 - gray_crop.astype(float)) / 255.0
    return float((ink * region).sum()) / max(1, sk)


def detect_bold_italic_caps(gray: np.ndarray, bw: np.ndarray, lines: list[Line], xh: float) -> None:
    owner = {}
    for l in lines:
        for w in l.label + l.words + l.margin:
            owner[id(w)] = l
    allw = [w for l in lines for w in l.label + l.words + l.margin]
    for w in allw:
        l = owner[id(w)]
        ybot = int(round(l.base_at((w.x0 + w.x1) / 2))) + 1     # above the baseline: no underline
        g, b = gray[w.y0:ybot, w.x0:w.x1], bw[w.y0:ybot, w.x0:w.x1]
        w.stroke = stroke_mass(g, b, xh)
        rows = np.where(b.max(axis=1) > 0)[0] if b.size else []
        w.caps_ratio = (ybot - (w.y0 + rows[0])) if len(rows) else 0
    body = [w.stroke for w in allw if len(w.text) >= 3 and w.text.isalpha() and w.text.islower()]
    med = st.median(body) if body else 1.0
    ratio = {id(w): w.stroke / med for w in allw}
    for w in allw:                                   # first pass: confident words
        n = sum(c.isalnum() for c in w.text)
        w.bold = ratio[id(w)] >= (1.30 if n >= 3 else 1.45)
    for l in lines:                                  # second pass: neighbours of bold words
        seq = l.label + l.words
        for i, w in enumerate(seq):
            nb = (i > 0 and seq[i - 1].bold) or (i + 1 < len(seq) and seq[i + 1].bold)
            if not w.bold and nb and ratio[id(w)] >= 1.22:
                w.bold = True
    for w in allw:
        w.italic = False                             # not reliable on small screenshots (measured)
    caps_body = [w.caps_ratio for w in allw if re.fullmatch(r"[A-Z][a-z]{2,}[.,?!]?", w.text)]
    ref = st.median(caps_body) if caps_body else 1.0
    for w in allw:
        w.caps_ratio = w.caps_ratio / ref if ref else 1.0


def typographic(lines: list[Line], curly: bool = True) -> None:
    """Faithful print typography: ’ apostrophes, … ellipsis, spaced en dashes (UK publishers)."""
    for l in lines:
        for w in l.label + l.words + l.margin:
            t = w.text
            if curly:
                t = re.sub(r"(?<=\w)'(?=\w)", "’", t)
                t = re.sub(r"^'(?=\w)", "‘", t)
            t = t.replace("...", "…")
            if t in {"-", "—", "--"}:
                t = "–"
            w.text = t
        if l.label and sum(c.isupper() for w in l.label for c in w.text) >= 0.6 * sum(c.isalpha() for w in l.label for c in w.text):
            for w in l.label:
                w.text = w.text.upper()


def find_separators(bw: np.ndarray, lines: list[Line], xh: float, left: int, right: int) -> list[float]:
    """REJECTED (measured worse, see CLAUDE.md). Dashed or solid rules spanning most of the width, not inside any text line."""
    occupied = np.zeros(bw.shape[0], bool)
    for l in lines:
        occupied[max(0, l.y0 - 2):l.y1 + 2] = True
    seps = []
    span = right - left
    y = 0
    while y < bw.shape[0]:
        if not occupied[y]:
            row = bw[y, left:right] > 0
            cover = row.mean() if row.size else 0
            if cover > 0.25:
                xs = np.where(row)[0]
                if xs.size and xs[-1] - xs[0] > 0.6 * span:
                    seps.append(y)
                    y += int(xh)
                    continue
        y += 1
    return seps
