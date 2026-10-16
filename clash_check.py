"""Read-only: task 2 edits already saved on items whose life years do not fit the entry's (dictionaries.life_years_clash),
with what each edit added, for review and correction. Writes work/task2/clash_check.json."""
import csv
import json
import sys

import dictionaries as d


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    plans = {}
    for w in ("snl", "nkl", "sml", "hds"):
        p = d.WORK2 / f"full_plan_{w}.jsonl"
        if p.exists():
            plans.update({(w, e["id"]): e for e in map(json.loads, open(p, encoding="utf-8"))})
    saved = [r for r in csv.DictReader(open(d.ROOT / "dictionary_edits.csv", encoding="utf-8")) if r["revid"]]
    out = []
    for i in range(0, len(saved), 50):
        batch = saved[i:i + 50]
        live = d.items(sorted({r["item"] for r in batch}))
        for r in batch:
            e = plans.get((r["work"], r["entry"]))
            ent = live.get(r["item"], {})
            if not e or not ent:
                continue
            note = d.life_years_clash(ent.get("claims", {}), e)
            if note:
                out.append({"work": r["work"], "entry": r["entry"], "item": r["item"], "revid": r["revid"],
                            "changes": r["changes"], "note": note})
                d.out(f"{r['work']} {r['entry']} {r['item']} rev {r['revid']}: {r['changes']} || {note}")
    (d.WORK2 / "clash_check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    d.out(f"{len(out)} of {len(saved)} saved edits are on items whose life years do not fit the entry")


if __name__ == "__main__":
    main()
