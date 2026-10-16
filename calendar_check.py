"""Read-only: what SNL's day-exact dates between 1583 and 1699 are, judged against the dates Wikidata already has.

For each entry the plan left out ("calendar before 1700 unclear"), the item's dates of that kind are compared with
SNL's digits: the same digits with the Julian model, the same digits with the Gregorian model, or the same day
converted (SNL's digits read as Julian = the item's Gregorian date, or the reverse). Grouped by the country of the
entry's place of birth (Denmark-Norway or elsewhere). Writes work/task2/calendar_check.json and prints a summary.
"""
import collections
import json
import re
import sys

import dictionaries as d

DK_NO = {"Q20", "Q35", "Q756617", "Q189", "Q4628", "Q223"}   # Norway, Denmark, the Kingdom, Iceland, Faroes, Greenland
NOTE = re.compile(r"^(date of birth|date of death) (\d{1,2})\.(\d{1,2})\.(\d{4}) not used: calendar before 1700 unclear")


def main(limit=600):
    sys.stdout.reconfigure(encoding="utf-8")
    cases = []
    for w in ("snl", "nkl"):
        for e in map(json.loads, open(d.WORK2 / f"full_plan_{w}.jsonl", encoding="utf-8")):
            for n in e.get("notes", []):
                m = NOTE.match(n)
                if m and 1582 < int(m.group(4)) < 1700:
                    born = next((f["value"]["value"]["id"] for f in e["facts"] if f["pid"] == "P19"), None)
                    cases.append({"work": w, "entry": e["id"], "qid": e["qid"], "pid": "P569" if "birth" in m.group(1) else "P570",
                                  "ymd": (int(m.group(4)), int(m.group(3)), int(m.group(2))), "born_at": born})
    d.out(f"{len(cases)} left-out day dates 1583-1699")
    cases = cases[:limit]
    places = sorted({c["born_at"] for c in cases if c["born_at"]})
    country = {}
    for i in range(0, len(places), 200):
        part = places[i:i + 200]
        for r in d.qlever("SELECT ?p ?c WHERE { VALUES ?p { " + " ".join(f"wd:{q}" for q in part) + " } ?p wdt:P17 ?c }"):
            if len(r) == 2:
                country.setdefault(r[0], set()).add(r[1])
    live, qids = {}, sorted({c["qid"] for c in cases})
    for i in range(0, len(qids), 50):                # only the date claims are kept: whole items fill the memory
        for q, e in d.items(qids[i:i + 50]).items():
            live[q] = {"claims": {p: e.get("claims", {}).get(p, []) for p in ("P569", "P570")}}
    tally = collections.Counter()
    rows = []
    for c in cases:
        y, mo, dd = c["ymd"]
        group = "Denmark-Norway" if country.get(c["born_at"], set()) & DK_NO else ("elsewhere" if c["born_at"] else "place unknown")
        claims = [x for x in live.get(c["qid"], {}).get("claims", {}).get(c["pid"], [])
                  if x["mainsnak"]["snaktype"] == "value" and x["mainsnak"]["datavalue"]["value"].get("precision") == 11]
        kind = "item has no day-exact date"
        for x in claims:
            v = x["mainsnak"]["datavalue"]["value"]
            vy, vm, vd = d._ymd(v)
            julian = v.get("calendarmodel") == d.JULIAN
            if (vy, vm, vd) == (y, mo, dd):
                kind = "same digits, Julian model" if julian else "same digits, Gregorian model"
                break
            if not julian and d._jdn(y, mo, dd, julian=True) == d._jdn(vy, vm, vd):
                kind = "item: SNL's day converted to Gregorian"
                break
            if julian and d._jdn(vy, vm, vd, julian=True) == d._jdn(y, mo, dd):
                kind = "item: SNL's day converted to Julian"
                break
            kind = "item has another day"
        sourced = sorted({p for x in claims for r in x.get("references", []) for p in r["snaks"]
                          if p in ("P248", "P143", "P854")})
        tally[(group, kind)] += 1
        rows.append({**c, "group": group, "kind": kind, "refs": sourced})
    (d.WORK2 / "calendar_check.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    for (group, kind), n in sorted(tally.items()):
        d.out(f"{group:15} {kind:42} {n}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 600)
