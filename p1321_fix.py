"""Places of origin (P1321) that OKA bot added from the HDS with a value that is not a Swiss municipality (Thomas
Kerboul, Property talk:P1321, 23 November 2026). Through swiss_place's fallback to well-known cities, 'von Freiburg'
had become Freiburg im Breisgau instead of Fribourg, and foreigners' places of origin abroad (von Grenoble, von
Mailand, von Mülhausen) were added, which the property cannot hold.

For each HDS entry whose plan has such a value, checked against the item now:
  - the wrong statement is removed when the bot created it (the run log says "place of origin") and its only
    reference is the bot's HDS one; otherwise only the bot's reference is removed, and other editors' statements and
    references stay as they are;
  - the HDS "von" places are matched again under the fixed rule (dictionaries.swiss_municipality: Swiss municipalities
    only, aliases too), and those the item lacks are added with the HDS reference, as the run would add them.
The HDS pages come from the crawl cache: nothing is asked of the HDS.

    python p1321_fix.py             what would change
    python p1321_fix.py --save      the edits (log p1321_fixes.csv)
"""
import collections
import csv
import datetime
import json
import os
import re
import sys
from pathlib import Path

import dictionaries as d

HERE = Path(__file__).parent
PLAN = d.WORK2 / "full_plan_hds.jsonl"
EDITS = HERE / "dictionary_edits.csv"
OUT = HERE / "p1321_fixes.csv"
HDS = "Q642074"
TALK = "[[Property talk:P1321#A problematic constraint|P1321 talk]]"


def swiss(values):
    """The values that are a current or former municipality of Switzerland."""
    values, found = sorted(values), set()
    for i in range(0, len(values), 300):
        rows = d.qlever("SELECT DISTINCT ?p WHERE { VALUES ?p { " + " ".join(f"wd:{q}" for q in values[i:i + 300]) +
                        " } ?p wdt:P31 ?c . VALUES ?c { wd:Q70208 wd:Q685309 } }")
        found |= {r[0].rsplit("/", 1)[-1].strip("<>") for r in rows}
    return found


def von_names(hid):
    page = d.WORK2 / "hds" / f"{hid.zfill(6)}.html"
    if not page.exists():
        return None
    t = page.read_text(encoding="utf-8")
    paras = [p for p in re.findall(r"<p[^>]*>(.*?)</p>", t, re.S) if len(d.clean(p)) > 40]
    header = d.clean(re.split(r"\.\s+(?:Sohn|Tochter)\b", d.clean(paras[0]) if paras else "")[0])
    m = re.search(r",\s*von ([^.]+)$", header)
    return re.split(r",\s*|\s+und\s+", m.group(1)) if m else []


def ours(ref, hid):
    snaks = ref.get("snaks", {})
    return (any(s.get("datavalue", {}).get("value", {}).get("id") == HDS for s in snaks.get("P248", []))
            and any(str(s.get("datavalue", {}).get("value", "")).lstrip("0") == hid.lstrip("0") for s in snaks.get("P902", [])))


def label(q, ents):
    labels = ents.get(q, {}).get("labels", {})
    return next((labels[l]["value"] for l in ("en", "de", "fr", "mul") if l in labels), q)


def save(site, qid, claims, baserevid, summary):
    from pywikibot.data import api
    from pywikibot.exceptions import APIError
    from common import edit_slot
    try:
        edit_slot()
        r = api.Request(site=site, parameters={"action": "wbeditentity", "id": qid, "data": json.dumps({"claims": claims}),
                                               "bot": 1, "baserevid": baserevid, "token": site.tokens["csrf"],
                                               "summary": summary}).submit()
        return r["entity"]["lastrevid"], None
    except APIError as e:
        return None, f"refused by Wikidata: {e.code}"


def main():
    save_ = "--save" in sys.argv
    plan = [json.loads(line) for line in open(PLAN, encoding="utf-8")]
    planned = {(e["id"]): {f["value"]["value"]["id"] for f in e["facts"] if f["pid"] == "P1321"} for e in plan}
    values = {q for vs in planned.values() for q in vs}
    ok = swiss(values)
    bad_values = values - ok
    todo = [e for e in plan if planned[e["id"]] & bad_values]
    print(f"{len(values)} planned place-of-origin values, {len(bad_values)} not a Swiss municipality, "
          f"in {len(todo)} entries", flush=True)
    created = collections.defaultdict(bool)
    for r in csv.DictReader(open(EDITS, encoding="utf-8")):
        if r["work"] == "hds" and "place of origin" in r["changes"].split("; "):
            created[r["entry"]] = True
    site = None
    if save_:
        from common import bot_site
        site = bot_site(1)
    d.REQUEST2 = "[[Wikidata:Requests for permissions/Bot/OKA bot 2|OKA bot task 2]]"
    today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    done = set()
    if OUT.exists():
        done = {r["entry"] for r in csv.DictReader(open(OUT, encoding="utf-8")) if r["revid"] or r["result"].startswith("no ")}
    todo = [e for e in todo if e["id"] not in done]
    counts = collections.Counter()
    new = not OUT.exists()
    with open(OUT if save_ else os.devnull, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new and save_:
            w.writerow(["time", "entry", "qid", "wrong", "right", "revid", "result"])
        for b in range(0, len(todo), 50):
            batch = todo[b:b + 50]
            live = d.items(sorted({e["qid"] for e in batch}))
            names = {e["id"]: von_names(e["id"]) for e in batch}
            right = {e["id"]: [q for q in (d.swiss_municipality(n) for n in (names[e["id"]] or [])) if q] for e in batch}
            lab_ents = d.items(sorted(bad_values & {q for e in batch for q in planned[e["id"]]}
                                      | {q for rs in right.values() for q in rs}))
            for e in batch:
                ent = live.get(e["qid"], {})
                if not ent or names[e["id"]] is None:
                    counts["no item or no cached page"] += 1
                    continue
                stmts = ent.get("claims", {}).get("P1321", [])
                claims, did = [], []
                wrong = [c for c in stmts if c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") in
                         planned[e["id"]] & bad_values]
                for c in wrong:
                    q = c["mainsnak"]["datavalue"]["value"]["id"]
                    mine = [r for r in c.get("references", []) if ours(r, e["id"])]
                    others = [r for r in c.get("references", []) if not ours(r, e["id"])]
                    if not mine:
                        did.append(f"{label(q, lab_ents)}: not the bot's, left alone")
                    elif created[e["id"]] and not others:
                        claims.append({"id": c["id"], "remove": ""})
                        did.append(f"{label(q, lab_ents)} removed")
                    else:
                        claims.append({**c, "references": others})
                        did.append(f"HDS reference removed from {label(q, lab_ents)}")
                entry = {**e, "facts": [d.fact("P1321", d.item_value(q), "place of origin") for q in right[e["id"]]]}
                labels = d.place_labels(live, [entry])
                add, changes, _ = d.build(ent, entry, today, labels) if entry["facts"] else ([], [], [])
                claims += add
                adds = [label(q, lab_ents) for q in right[e["id"]]] if changes else []
                if changes:
                    did.append(f"{', '.join(adds)}: {'; '.join(changes)}")
                wrong_l = ", ".join(label(q, lab_ents) for q in planned[e["id"]] & bad_values)
                print(f"{e['id']} {e['qid']} von {', '.join(names[e['id']])}: {' | '.join(did) or 'nothing to change'}")
                if not claims:
                    counts["nothing to change"] += 1
                    if save_:
                        w.writerow([today, e["id"], e["qid"], wrong_l, ", ".join(adds), "", "no change: " + " | ".join(did)])
                    continue
                counts["would edit" if not save_ else "edited"] += 1
                if not save_:
                    continue
                summary = (f"Place of origin from the HDS corrected: {wrong_l} is not a Swiss municipality"
                           + (f"; {', '.join(adds)} instead" if adds else "") + f" ({TALK}; {d.REQUEST2})")
                rev, err = save(site, e["qid"], claims, ent.get("lastrevid"), summary)
                w.writerow([datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), e["id"],
                            e["qid"], wrong_l, ", ".join(adds), rev or "", err or " | ".join(did)])
                fh.flush()
            print(f"{b + len(batch)} of {len(todo)}: {dict(counts)}", flush=True)
    print("done:", dict(counts))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
