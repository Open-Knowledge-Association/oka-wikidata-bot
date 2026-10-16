"""Read-only: SNL's notes on Julian dates (lifespan_comment), and task 2 edits that added a date which those notes or
the place of birth (Sweden or Finland before 1753) put in doubt. Prints the variants and the edits to check."""
import collections
import csv
import json
import re
import sys

import dictionaries as d


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    comments, by_entry = collections.Counter(), {}
    for work in ("snl", "nkl"):
        for p in (d.WORK2 / work).glob("*.json"):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            m = j.get("metadata") or {}
            c = m.get("lifespan_comment") or ""
            if "julian" in c.lower():
                key = re.sub(r"\d+\.?\s*\w*\s*\d{3,4}", "<date>", c)[:150]
                comments[key] += 1
                by_entry[(work, p.stem)] = m
    d.out(f"{sum(comments.values())} entries with a Julian note; variants:")
    for k, n in comments.most_common(25):
        d.out(f"{n:5}  {k}")
    edits = [r for r in csv.DictReader(open(d.ROOT / "dictionary_edits.csv", encoding="utf-8")) if r["revid"]]
    added = [r for r in edits if re.search(r"(^|; )(date of birth|date of death)(?! \(more precise\))", r["changes"])
             and "source for date" not in r["changes"].split("; ")[0]]
    d.out(f"\n{len(edits)} saved edits; those adding a date of birth or death (not only a source):")
    hits = 0
    for r in edits:
        changes = r["changes"].split("; ")
        if not any(c in ("date of birth", "date of death", "date of birth (more precise)", "date of death (more precise)")
                   for c in changes):
            continue
        m = by_entry.get((r["work"], r["entry"].replace("/", "_")))
        if m:
            hits += 1
            d.out(f"JULIAN NOTE  {r['work']} {r['entry']} {r['item']} rev {r['revid']}: {r['changes']} || "
                  f"{m.get('birth_date')} / {m.get('death_date')} | {m.get('lifespan_comment')[:120]}")
    d.out(f"{hits} saved edits added a date to an entry with a Julian note")


if __name__ == "__main__":
    main()
