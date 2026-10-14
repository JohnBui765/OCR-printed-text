"""Run the prototype on the three pages: score words/lines, write .docx, report timing."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
import build as B          # noqa: E402
from score import line_scores, word_scores  # noqa: E402

# usage: python prototype/run_all.py <repo-root> <tessdata-dir>
# tessdata-dir must hold eng.traineddata (tessdata "std" or tesseract.js 4.0.0_best_int: same LSTM)
S = Path(sys.argv[1])
TD = sys.argv[2]
out = S / "out"
out.mkdir(parents=True, exist_ok=True)
for page in ["p1_cottage", "p2_bikes", "p3_loneliness"]:
    t0 = time.time()
    A = B.analyse(str(S / "testdata" / f"{page}.png"), TD)
    t1 = time.time()
    lines = []
    for l in A["lines"]:
        parts = [" ".join(w.text for w in l.label), " ".join(w.text for w in l.words), " ".join(w.text for w in l.margin)]
        lines.append(" ".join(p for p in parts if p))
    gt = (S / "testdata" / "gt" / f"{page}.txt").read_text(encoding="utf-8")
    ws = word_scores(gt, "\n".join(lines))
    ls = line_scores(gt, lines)
    info = B.build(str(S / "testdata" / f"{page}.png"), TD, str(out / f"{page}.docx"))
    print(f"{page}: words right {ws['acc']*100:.1f}% ({ws['words']-ws['correct']} wrong/lost) | exact lines {ls['exact']}/{ls['lines']} | analysis {t1-t0:.1f}s")
    print("   missing:", ws["missing"][:30])
    print("   extra:  ", ws["extra"][:30])
    print("   layout:", json.dumps(info))
    (out / f"{page}.lines.txt").write_text("\n".join(lines), encoding="utf-8")
