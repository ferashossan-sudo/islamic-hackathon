"""Summarize eval runs: pass rate per category with 95% Wilson intervals, stability across runs.

    uv run python eval/metrics.py eval/runs/critical_ours_<stamp>.jsonl [more run files ...] [--md eval/report.md]
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def summarize(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_case = defaultdict(list)
    for r in rows:
        by_case[r["id"]].append(r)
    runs = max(r["run"] for r in rows)
    cats = defaultdict(lambda: [0, 0])
    stable = 0
    chats = sum(1 for r in rows if r.get("chat"))
    answers = sum(1 for r in rows if r["kind"] == "answer")
    for case_id, rs in by_case.items():
        ok = all(r["passed"] for r in rs)
        cats[rs[0]["category"]][0] += ok
        cats[rs[0]["category"]][1] += 1
        stable += len({(r["kind"], r["entry_id"]) for r in rs}) == 1
    total_ok = sum(v[0] for v in cats.values())
    total = sum(v[1] for v in cats.values())
    lo, hi = wilson(total_ok, total)
    lines = [f"### {path.stem}", "",
             f"- Cases passing in all {runs} run(s): **{total_ok}/{total}** (95% CI {lo:.0%}–{hi:.0%})",
             f"- Same (kind, entry) in every run: {stable}/{total}",
             f"- Answers with a dialogue reply that passed G13 and G14: {chats}/{answers}" if answers else "",
             "", "| Category | Passed | n | 95% CI |", "|---|---|---|---|"]
    for cat, (k, n) in sorted(cats.items()):
        a, b = wilson(k, n)
        lines.append(f"| {cat} | {k} | {n} | {a:.0%}–{b:.0%} |")
    failing = sorted({r["id"] + ": " + " ".join(r["failed_checks"]) for r in rows if not r["passed"]})
    if failing:
        lines += ["", "Failures:", *[f"- {f}" for f in failing]]
    return "\n".join(x for x in lines if x is not None) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--md", help="append the summary to this markdown file")
    args = parser.parse_args()
    text = "\n".join(summarize(Path(p)) for p in args.runs)
    print(text)
    if args.md:
        with open(args.md, "a", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
