"""Run whole conversations through the real pipeline, the way the page sends them, and keep the transcripts.

    uv run --env-file .env python eval/run_dialogues.py CASES.json [--drafts] [--sleep 6] [--only id,id]

CASES is a JSON list (or JSONL) of {id, category, turns, expect_kinds, expect_entries, must_not, judge_focus}.
Each turn carries the same history (last 4 items) and context (previous entry, recent entries, repeat count) as
static/app.js. Transcripts go to eval/private/dialogues/ (never committed): they hold model text. The summary
printed here has kinds, entries and automatic checks only.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from app import arabic, config, converse, limits, main, pipeline, router  # noqa: E402
from app.schemas import ChatContext, ChatRequest, HistoryItem, RecentItem  # noqa: E402
from auto_checks import check_text  # noqa: E402
from run_eval import load_drafts, plain_text, segments_text, sharia_in_chat  # noqa: E402

OUT = ROOT / "eval" / "private" / "dialogues"
REPEAT_SIMILARITY = 0.8  # as in static/app.js


def read_cases(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]


async def run_case(case: dict, settings, sleep: float, events: list) -> dict:
    context, history, sent, turns = ChatContext(), [], [], []
    for n, message in enumerate(case["turns"], 1):
        await asyncio.sleep(sleep)
        normalized = arabic.normalize(message)
        context = context.model_copy(update={
            "repeat_count": sum(1 for m in sent if arabic.trigram_similarity(m, normalized) >= REPEAT_SIMILARITY)})
        events.clear()
        started = time.time()
        status, resp = await pipeline.handle(
            ChatRequest(message=message, turn=n, context=context, history=history[-4:]), settings)
        chat = next((b for b in resp.blocks if b["type"] == "chat"), None)
        card = resp.model_copy(update={"blocks": [b for b in resp.blocks if b["type"] != "chat"]})
        reply = segments_text(chat["segments"]) if chat else ""
        turns.append({"user": message, "status": status, "kind": resp.kind, "entry": resp.entry_id,
                      "degraded": resp.degraded, "keys": [b.get("key") for b in resp.blocks if b.get("key")],
                      "chat": reply, **sharia_in_chat(resp.blocks),
                      "card": plain_text(card), "seconds": round(time.time() - started, 1),
                      "calls": [{k: e.get(k) for k in ("call", "model", "ok")} for e in events if e.get("event") == "llm"],
                      "g13": [e.get("reason") for e in events if e.get("event") == "g13"],
                      "checks": check_text(reply + "\n" + plain_text(card))})
        if status == 200:
            sent.append(normalized)
            history.append(HistoryItem(role="user", text=message[:1200]))
            if chat:
                history.append(HistoryItem(role="assistant", text=chat["history_text"][:1200]))
            history = history[-4:]
            if resp.entry_id:
                context = ChatContext(prev_entry_id=resp.entry_id, repeat_count=context.repeat_count,
                                      recent=(context.recent + [RecentItem(entry_id=resp.entry_id, kind=resp.kind,
                                                                           layer=resp.layer or "summary")])[-10:])
    last = turns[-1]
    failed = []
    if case.get("expect_kinds") and last["kind"] not in case["expect_kinds"]:
        failed.append(f"kind:{last['kind']}")
    if case.get("expect_entries") and last["kind"] == "answer" and last["entry"] not in case["expect_entries"]:
        failed.append(f"entry:{last['entry']}")
    # Optional per-turn expectations: {"turn": n, "kinds": [...], "entries": [...], "keys_include": [...]}.
    for exp in case.get("expect_turns", []):
        t = turns[exp["turn"] - 1] if exp["turn"] <= len(turns) else None
        if t is None:
            failed.append(f"t{exp['turn']}:missing")
            continue
        if exp.get("kinds") and t["kind"] not in exp["kinds"]:
            failed.append(f"t{exp['turn']}:kind:{t['kind']}")
        if exp.get("entries") and t["kind"] == "answer" and t["entry"] not in exp["entries"]:
            failed.append(f"t{exp['turn']}:entry:{t['entry']}")
        missing = [k for k in exp.get("keys_include", []) if k not in t["keys"]]
        if missing:
            failed.append(f"t{exp['turn']}:keys:{','.join(missing)}")
    for t in turns:
        if t["checks"]["misquoted_verses"] or t["checks"]["certainty_claims"]:
            failed.append("auto:" + ",".join(k for k, v in t["checks"].items() if v and k != "has_link"))
    return {**case, "transcript": turns, "auto_failed": failed}


async def main_async(args) -> int:
    cases = read_cases(Path(args.cases))
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]
    settings = config.for_evaluation(main.settings)
    if args.drafts:
        pipeline.load(load_drafts())
    limits.LIMITER.check = lambda *a, **k: 0  # the evaluation is not a visitor
    limits.DAILY_LLM_CALLS = 10 ** 6
    events: list = []
    for module in (pipeline, router, converse):
        module.log_event = lambda **kw: events.append(kw)
    stamp = datetime.now(timezone(timedelta(hours=3))).strftime("%Y%m%d-%H%M")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{Path(args.cases).stem}_{stamp}.jsonl"
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for case in cases:
            row = await run_case(case, settings, args.sleep, events)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            last = row["transcript"][-1]
            mark = "✓" if not row["auto_failed"] else "✗"
            print(f"{mark} {row['id']} turns={len(row['turns'])} last={last['kind']} {last['entry'] or ''} "
                  f"chat={'yes' if last['chat'] else 'no'} {' '.join(row['auto_failed'])}", flush=True)
    print("transcripts:", out)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cases")
    parser.add_argument("--drafts", action="store_true", help="team-only: load valid drafts as approved, in memory")
    parser.add_argument("--sleep", type=float, default=6.0)
    parser.add_argument("--only")
    sys.exit(asyncio.run(main_async(parser.parse_args())))
