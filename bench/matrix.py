"""Run every model x zoom x layout-mode combination on the three pages and score it."""
import sys, time, os
sys.path.insert(0, os.path.dirname(__file__))
from score import word_scores, line_scores
import pytesseract
from PIL import Image, ImageOps

# usage: python bench/matrix.py <repo-root> <dir holding tessdata_std/ tessdata_bestint/ tessdata_sys/>
S = sys.argv[1]
M = sys.argv[2]
PAGES = ["p1_cottage", "p2_bikes", "p3_loneliness"]
MODELS = {"std (your PC now)": "tessdata_std", "best_int": "tessdata_bestint", "fast": "tessdata_sys"}

def prep(path, factor):
    im = Image.open(path).convert("L")
    im = ImageOps.autocontrast(im)
    return im.resize((im.width * factor, im.height * factor), Image.LANCZOS)

gts = {p: open(f"{S}/testdata/gt/{p}.txt", encoding="utf-8").read() for p in PAGES}
rows = []
for mname, mdir in MODELS.items():
    for factor in (2, 3, 4):
        for psm in (3, 6, 11):
            tot_w = tot_c = tot_l = tot_e = 0
            t0 = time.time()
            per = []
            for p in PAGES:
                img = prep(f"{S}/testdata/{p}.png", factor)
                txt = pytesseract.image_to_string(img, config=f"--tessdata-dir {M}/{mdir} --oem 1 --psm {psm}")
                ws = word_scores(gts[p], txt)
                ls = line_scores(gts[p], txt.splitlines())
                tot_w += ws["words"]; tot_c += ws["correct"]; tot_l += ls["lines"]; tot_e += ls["exact"]
                per.append(f"{ws['acc']*100:5.1f}")
            dt = time.time() - t0
            rows.append((mname, factor, psm, tot_c / tot_w * 100, tot_w - tot_c, tot_e, tot_l, dt / 3, per))
            print(f"{mname:18} x{factor} psm{psm:<2} | words right {tot_c/tot_w*100:5.1f}% ({tot_w-tot_c:3} wrong/lost) "
                  f"| exact lines {tot_e:3}/{tot_l} | {dt/3:4.1f}s/page | per page {per}", flush=True)
