"""Read-only: task 2 edits that added a date of birth or death which the calendar rule now leaves out (a day or month
in a place and time of the Julian calendar, see dictionaries.JULIAN_UNTIL). Writes work/task2/calendar_audit.json."""
import copy
import csv
import json
import sys

import dictionaries as d

ADDED = {"date of birth", "date of death", "date of birth (more precise)", "date of death (more precise)"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    plans = {}
    for w in ("snl", "nkl"):
        plans.update({(w, e["id"]): e for e in map(json.loads, open(d.WORK2 / f"full_plan_{w}.jsonl", encoding="utf-8"))})
    rows = [r for r in csv.DictReader(open(d.ROOT / "dictionary_edits.csv", encoding="utf-8"))
            if r["revid"] and ADDED & set(r["changes"].split("; "))]
    d.out(f"{len(rows)} saved edits added a date of birth or death")
    found = []
    for i in range(0, len(rows), 50):
        part = rows[i:i + 50]
        live = d.items(sorted({r["item"] for r in part}))
        for r in part:
            e = plans.get((r["work"], r["entry"]))
            if not e:
                continue
            before = copy.deepcopy(e)
            before["notes"] = []
            after = d.refresh_life_dates(copy.deepcopy(e), live.get(r["item"]))
            dropped = [n for n in after["notes"] if "calendar unclear there" in n]
            if dropped:
                found.append({"work": r["work"], "entry": r["entry"], "item": r["item"], "revid": r["revid"],
                              "changes": r["changes"], "dropped": dropped})
                d.out(f"{r['work']} {r['entry']} {r['item']} rev {r['revid']}: {r['changes']} || {'; '.join(dropped)}")
    (d.WORK2 / "calendar_audit.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    d.out(f"{len(found)} saved edits added a date the calendar rule now leaves out")


if __name__ == "__main__":
    main()
