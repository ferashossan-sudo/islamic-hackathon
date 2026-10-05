"""G14 on its own: does the verifier flag invented claims in a dialogue reply and pass grounded ones?

    uv run --env-file .env python eval/check_verifier.py [--sleep 5]

Each case pairs an entry in content/kb.json with a reply; "flag" cases add one claim the entry does not make
(an invented study, history, number, ruling, or scientific overclaim). Prints one line per case and the totals.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import converse, kb  # noqa: E402
from app.config import for_evaluation, load_settings  # noqa: E402

CASES = ROOT / "eval" / "cases" / "verifier.jsonl"


async def main_async(sleep: float) -> int:
    s = for_evaluation(load_settings())
    entries = {e["id"]: e for e in kb.read_all()}
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    right = failed_calls = 0
    for case in cases:
        verdict = await converse.unsupported_claims(case["reply"], entries[case["entry"]], s)
        got = "error" if verdict is None else ("pass" if verdict == [] else "flag")
        right += got == case["expect"]
        failed_calls += got == "error"
        print(f"{'✓' if got == case['expect'] else '✗'} {case['id']}: expected {case['expect']}, got {got}")
        await asyncio.sleep(sleep)
    print(f"\n{s.verify_model}: {right}/{len(cases)} correct, {failed_calls} failed calls")
    return 0 if right == len(cases) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, default=5.0)
    sys.exit(asyncio.run(main_async(parser.parse_args().sleep)))
