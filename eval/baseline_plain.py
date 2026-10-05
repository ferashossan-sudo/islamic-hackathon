"""Baseline: the same questions answered by the same free model WITHOUT our approved answers or guards.

    uv run --env-file .env python eval/baseline_plain.py --set critical [--model gemini-3.5-flash-lite] [--sleep 6]
    uv run python eval/auto_checks.py eval/private/runs/<ours>.texts.jsonl eval/private/runs/<set>_plain_<stamp>.texts.jsonl

The system instruction is what a general assistant would get: answer in Arabic, accurately, with evidence.
Answers are written to eval/private/runs/ (never committed) in the format auto_checks.py reads.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import gemini  # noqa: E402
from app.config import for_evaluation, load_settings  # noqa: E402

SETS = {"critical": ROOT / "eval" / "cases" / "critical.jsonl", "dev": ROOT / "eval" / "cases" / "dev.jsonl",
        "drafts": ROOT / "eval" / "cases" / "dev_drafts.jsonl"}
SYSTEM = ("أنت مساعد إسلامي عام. أجب السائل بالعربية إجابة دقيقة ومقنعة، واستشهد بالآيات والأحاديث ومصادرها "
          "وبالمعلومات العلمية عند الحاجة.")


async def main_async(args) -> int:
    s = for_evaluation(load_settings())
    cases = [json.loads(line) for line in SETS[args.set].read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        cases = cases[:args.limit]
    stamp = datetime.now(timezone(timedelta(hours=3))).strftime("%Y%m%d-%H%M")
    out = ROOT / "eval" / "private" / "runs" / f"{args.set}_plain_{stamp}.texts.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    failed = 0
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for case in cases:
            await asyncio.sleep(args.sleep)
            try:
                text, usage = await gemini.generate(s.gemini_api_key, args.model, SYSTEM, case["turns"][-1], None, 60,
                                                    temperature=0.0, max_tokens=1500)
            except Exception as exc:  # noqa: BLE001 - count and continue
                failed += 1
                print("✗", case["id"], type(exc).__name__)
                continue
            f.write(json.dumps({"id": case["id"], "run": 1, "system": f"plain:{usage['model']}", "text": text},
                               ensure_ascii=False) + "\n")
            print("✓", case["id"], usage["model"])
    print(f"{len(cases) - failed}/{len(cases)} answered. Texts: {out}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", choices=list(SETS), default="critical")
    parser.add_argument("--model", default="gemini-3.5-flash-lite,gemini-3.1-flash-lite")
    parser.add_argument("--sleep", type=float, default=6.0)
    parser.add_argument("--limit", type=int)
    sys.exit(asyncio.run(main_async(parser.parse_args())))
