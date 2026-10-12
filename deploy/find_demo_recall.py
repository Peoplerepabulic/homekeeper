"""Pick a REAL CPSC recall for the demo kitchen fridge -> src/demo_recall.json.

1. List candidates:
       python deploy/find_demo_recall.py --search refrigerator
2. Read a candidate's description, choose a brand + model string that appears
   in it, and lock it in (verified with the same match_recalls() the daily
   scan uses, so the live cold path re-finds this exact recall):
       python deploy/find_demo_recall.py --pick <RecallID> --brand <B> --model <M>
3. Re-seed: python src/seed.py
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "agent"))
sys.path.insert(0, os.path.join(_ROOT, "src"))

import home_analyst as ha  # noqa: E402

OUT = os.path.join(_ROOT, "src", "demo_recall.json")


def _text(r: dict) -> str:
    names = " ".join(p.get("Name") or "" for p in r.get("Products") or [])
    return f"{r.get('Title') or ''} {names}".lower()


def search(keyword: str, limit: int) -> None:
    hits = [r for r in ha.fetch_recalls() if keyword.lower() in _text(r)]
    hits.sort(key=lambda r: r.get("RecallDate") or "", reverse=True)
    for r in hits[:limit]:
        print(f"\n[{r.get('RecallID')}] {r.get('RecallDate', '')[:10]}  {r.get('Title')}")
        print(f"  {r.get('URL')}")
        print(f"  {(r.get('Description') or '')[:400]}")
    print(f"\n{len(hits)} matches for '{keyword}' (showing {min(limit, len(hits))}).")


def pick(recall_id: str, brand: str, model: str) -> None:
    rec = next((r for r in ha.fetch_recalls() if str(r.get("RecallID")) == recall_id), None)
    if rec is None:
        sys.exit(f"RecallID {recall_id} not found.")
    if not ha.match_recalls([rec], brand, model):
        sys.exit(f"'{brand}' + '{model}' do not both appear in recall {recall_id}; "
                 "daily_scan would not match it. Pick strings from its text.")
    out = {
        "recall_id": str(rec.get("RecallID")),
        "title": rec.get("Title"),
        "url": rec.get("URL"),
        "recall_date": (rec.get("RecallDate") or "")[:10],
        "hazard": ", ".join(h.get("Name", "") for h in rec.get("Hazards") or [] if h.get("Name")),
        "brand": brand,
        "model": model,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {OUT}:\n{json.dumps(out, indent=2)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--search")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--pick")
    ap.add_argument("--brand")
    ap.add_argument("--model")
    a = ap.parse_args()
    if a.pick:
        if not (a.brand and a.model):
            sys.exit("--pick needs --brand and --model")
        pick(a.pick, a.brand, a.model)
    else:
        search(a.search or "refrigerator", a.limit)


if __name__ == "__main__":
    main()
