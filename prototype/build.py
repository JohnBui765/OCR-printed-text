"""
Prototype driver: page image -> formatted .docx that keeps line breaks, label column,
margin numbers, bold / italic / underline / small caps, separators and spacing.
"""
from __future__ import annotations

import json
import re
import statistics as st
import sys
from pathlib import Path

import numpy as np
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import engine as E  # noqa: E402

FONT_REG = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
_fr = ImageFont.truetype(FONT_REG, 1000)
_fb = ImageFont.truetype(FONT_BOLD, 1000)


def text_em(text: str, bold=False) -> float:
    """Width of text in em units of Arial (Liberation Sans is metric-identical)."""
    return (_fb if bold else _fr).getlength(text) / 1000.0


def analyse(path: str, tessdata: str) -> dict:
    gray0 = E.load_gray(path)
    angle = E.skew_angle(gray0)
    up, f = E.upscale(gray0)
    xh = E.estimate_xheight(up)
    lines = E.ocr_lines(up, tessdata, psm=6)
    for l in lines:
        for w in l.words:
            w.text = E.fix_token(w.text)
    # a "line" of only symbols spanning the page is a rule, not text
    width = max(l.x1 for l in lines) - min(l.x0 for l in lines)
    rules = [l for l in lines if not any(c.isalnum() for w in l.words for c in w.text)]
    lines = [l for l in lines if l not in rules]
    lines = E.merge_label_lines(lines, xh)
    cols = E.split_columns(lines, xh)
    E.typographic(lines)
    bw = E.binarize(up)
    E.detect_underlines(bw, lines, xh)
    E.detect_bold_italic_caps(up, bw, lines, xh)
    seps = sorted(E.find_rules(bw, lines, xh, angle) + [float((r.y0 + r.y1) / 2) for r in rules])
    # one rule may be found twice (scan + symbol line): merge near-duplicates
    merged = []
    for y in seps:
        if not merged or y - merged[-1] > 2 * xh:
            merged.append(y)
    seps = merged
    return {"lines": lines, "cols": cols, "xh": xh, "f": f, "seps": seps, "shape": up.shape, "angle": angle}


def em_px(lines) -> float:
    """Pixel size of 1 em of the body font, from long plain lines (robust font-size estimate)."""
    est = []
    for l in lines:
        ws = [w for w in l.words if not w.bold]
        if len(ws) < 6:
            continue
        txt = " ".join(w.text for w in ws)
        px = ws[-1].x1 - ws[0].x0
        est.append(px / text_em(txt))
    return st.median(est)


def set_cell_spacing(p, before_pt: float, line_pt: float):
    pf = p.paragraph_format
    pf.space_before = Pt(max(0.0, before_pt))
    pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(line_pt)


def dashed_bottom_border(p):
    pPr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    for k, v in (("w:val", "dashed"), ("w:sz", "4"), ("w:space", "1"), ("w:color", "555555")):
        b.set(qn(k), v)
    bdr.append(b)
    pPr.append(bdr)


def add_run(p, text, size_pt, bold=False, italic=False, underline=False, spacing_twips=None):
    r = p.add_run(text)
    r.font.name = "Arial"
    r.font.size = Pt(size_pt)
    r.bold = bold or None
    r.italic = italic or None
    r.underline = underline or None
    if spacing_twips:
        rPr = r._r.get_or_add_rPr()
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:val"), str(int(spacing_twips)))
        rPr.append(sp)
    return r


def emit_words(p, words, size_pt, pt_per_px):
    """Write words as runs, merging neighbours with the same format; spaces inherit underline."""
    i = 0
    while i < len(words):
        w = words[i]
        j = i
        while (j + 1 < len(words) and words[j + 1].bold == w.bold and
               words[j + 1].italic == w.italic and words[j + 1].underline == w.underline):
            j += 1
        chunk = " ".join(x.text for x in words[i:j + 1])
        tail = ""
        last = words[j]
        if last.underline and re.search(r"[.,?!;:]$", last.text):
            cw = (last.x1 - last.x0) / max(1, len(last.text))
            if last.ul_x1 < last.x1 - 0.45 * cw:
                tail, chunk = chunk[-1], chunk[:-1]
        size = size_pt
        spacing = None
        if w.caps_ratio > 1.35 and w.bold:          # display heading, e.g. "PART 1"
            size = round(size_pt * w.caps_ratio * 2) / 2
            target_px = words[j].x1 - w.x0
            natural_pt = text_em(chunk, bold=True) * size
            extra_pt = (target_px * pt_per_px - natural_pt) / max(1, len(chunk))
            if extra_pt > 0.2:
                spacing = extra_pt * 20
        add_run(p, chunk, size, w.bold, w.italic, w.underline, spacing)
        if tail:
            add_run(p, tail, size, w.bold, w.italic, False)
        if j + 1 < len(words):
            add_run(p, " ", size_pt, underline=w.underline and words[j + 1].underline)
        i = j + 1


def build(path: str, tessdata: str, out_docx: str, body_pt: float = 11.0) -> dict:
    A = analyse(path, tessdata)
    lines, cols, xh = A["lines"], A["cols"], A["xh"]
    em = em_px(lines)
    pt_per_px = body_pt / em
    left = cols["left"]
    content_w_pt = (cols["page_right"] - left) * pt_per_px
    # page: A4; margins centre the content, never below 1.27 cm
    page_w = 595.3
    side = max(36.0, (page_w - content_w_pt) / 2)
    if content_w_pt > page_w - 72:                  # too wide -> shrink everything
        body_pt = body_pt * (page_w - 72) / content_w_pt
        pt_per_px = body_pt / em
        content_w_pt = (cols["page_right"] - left) * pt_per_px
        side = 36.0
    # fit-to-width: Arial (esp. Arial Bold) can run wider than the source face; a line that
    # overflows gets wrapped twice by Word. Shrink the body size until every line fits.
    worst = 1.0
    for l in lines:
        ws = l.label + l.words
        if not ws:
            continue
        need_em = sum(text_em(w.text, w.bold) for w in ws) + text_em(" ") * (len(ws) - 1)
        have_px = (ws[-1].x1 - ws[0].x0)
        if l.label and l.words:          # label and text sit in separate columns
            need_em = sum(text_em(w.text, w.bold) for w in l.words) + text_em(" ") * (len(l.words) - 1)
            have_px = l.words[-1].x1 - l.words[0].x0
        worst = max(worst, (need_em * body_pt) / (have_px * pt_per_px))
    if worst > 1.0:
        body_pt = body_pt / (worst * 1.015)
    pitches = [b.base_at(b.x0) - a.base_at(a.x0) for a, b in zip(lines, lines[1:])]
    pitch_px = st.median([p for p in pitches if p > 0])
    line_pt = pitch_px * pt_per_px

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Pt(595.3), Pt(841.9)
    sec.left_margin = sec.right_margin = Pt(side)
    sec.top_margin = sec.bottom_margin = Pt(54)
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(body_pt)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    doc._body.clear_content()

    # justified? non-final lines of turns reach the right edge
    ends = [l.words[-1].x1 for l in lines if l.words]
    body_right = cols["body_right"]
    near = sum(1 for e in ends if body_right - e < 1.2 * xh)
    justified = near > 0.55 * len(ends)

    # group lines into paragraphs
    paras: list[list] = []
    prev_y = None
    sep_iter = list(A["seps"])
    cxp = A["shape"][1] / 2
    for l in lines:
        ymid = l.base_at(cxp) - 0.5 * xh
        while sep_iter and sep_iter[0] < ymid:
            paras.append(["SEP", sep_iter.pop(0)])
        starts_bold = bool(l.words) and l.words[0].bold and l.words[0].x0 < left + 2 * xh
        new = (not paras or paras[-1][0] == "SEP" or bool(l.label) or starts_bold or
               (prev_y is not None and l.base_at(l.x0) - prev_y > 1.45 * pitch_px))
        if new:
            paras.append(["P", []])
        paras[-1][1].append(l)
        prev_y = l.base_at(l.x0)

    last_base = None
    hang_pt = (cols["text_x"] - left) * pt_per_px if cols["hanging"] else 0
    right_tab_pt = content_w_pt
    for kind, payload in paras:
        if kind == "SEP":
            p = doc.add_paragraph()
            set_cell_spacing(p, 2, 6)
            p.paragraph_format.right_indent = Pt(max(0, (cols["page_right"] - cols["body_right"]) * pt_per_px))
            dashed_bottom_border(p)
            last_base = payload
            continue
        plines = payload
        p = doc.add_paragraph()
        first_base = plines[0].base_at(plines[0].x0)
        gap_pt = ((first_base - last_base) * pt_per_px - line_pt) if last_base is not None else 0
        set_cell_spacing(p, gap_pt, line_pt)
        pf = p.paragraph_format
        if hang_pt:
            pf.left_indent = Pt(hang_pt)
            pf.first_line_indent = Pt(-hang_pt)
            pf.tab_stops.add_tab_stop(Pt(hang_pt))
        pf.tab_stops.add_tab_stop(Pt(right_tab_pt), WD_TAB_ALIGNMENT.RIGHT)
        if justified and len(plines) > 1:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        for k, l in enumerate(plines):
            if l.label:
                lab = " ".join(w.text for w in l.label)
                sc = st.median([w.caps_ratio for w in l.label])
                lab_pt = body_pt * sc if lab.isupper() and sc < 0.92 else body_pt
                add_run(p, lab, round(lab_pt * 2) / 2, any(w.bold for w in l.label),
                        any(w.italic for w in l.label), any(w.underline for w in l.label))
                add_run(p, "\t", body_pt)
            elif hang_pt and k == 0 and l.words and l.words[0].x0 > left + 3 * xh:
                add_run(p, "\t", body_pt)
            emit_words(p, l.words, body_pt, pt_per_px)
            if l.margin:
                add_run(p, "\t", body_pt)
                mw = l.margin
                add_run(p, " ".join(w.text for w in mw), body_pt, any(w.bold for w in mw),
                        sum(w.italic for w in mw) * 2 >= len(mw), False)
            if k + 1 < len(plines):
                p.add_run().add_break(WD_BREAK.LINE)
        last_base = plines[-1].base_at(plines[-1].x0)
    doc.save(out_docx)
    return {"body_pt": round(body_pt, 2), "line_pt": round(line_pt, 2), "justified": justified,
            "hanging_pt": round(hang_pt, 1), "content_cm": round(content_w_pt / 72 * 2.54, 2),
            "lines": len(lines), "paragraphs": sum(1 for k, _ in paras if k == "P"),
            "separators": len(A["seps"]), "upscale": round(A["f"], 2), "deskew_deg": round(A["angle"], 2)}


def dump(path: str, tessdata: str) -> list[str]:
    A = analyse(path, tessdata)
    out = []
    for l in A["lines"]:
        def fmt(ws):
            s = []
            for w in ws:
                t = w.text
                tags = ("B" if w.bold else "") + ("I" if w.italic else "") + ("U" if w.underline else "")
                s.append(f"{t}[{tags}]" if tags else t)
            return " ".join(s)
        out.append(f"{fmt(l.label):<14} | {fmt(l.words)} | {fmt(l.margin)}")
    return out


if __name__ == "__main__":
    print(json.dumps(build(sys.argv[1], sys.argv[2], sys.argv[3]), indent=1))
