"""Apply a checked batch of disagreement outcomes (User:OKA bot/Disagreement checks/Batch N) as OKA bot, under the
rules of Wikidata:Requests for permissions/Bot/OKA bot 3 (approved 3 November 2026):

  prefer   the entry's value at preferred rank, reason P7452 Q98386534 (best referenced value), cited to the entry;
           Wikidata's value stays as it is
  both     the entry's value beside Wikidata's, at the same rank, cited to the entry
  report   the entry's value at deprecated rank, reason P2241 Q29998666 (error in referenced source), cited to the
           entry, so the item shows that the work states otherwise; the case goes into the work's error report
  same     nothing (one place inside the other)
  retry    nothing yet

The outcome is worked out again from the logged evidence, as on the batch page. Before each edit the item is read
again: the case is left alone when the entry's value is already there, when Wikidata's values are no longer those
that were checked, or when their sources changed kind (a source was added to an unsourced value, or the reverse).
Each edit is read back. One edit per case; at most 60 edits a minute across all of the bot's jobs (common.edit_slot).
jz checked a sample of batch 1 (searched cases) and batch 2 (sourced cases) and approved every later batch made the
same way (10 and 11 November 2026).

    python conflict_apply.py LOG BATCH [--since T] [--until T] [--save]
LOG: conflict_checks.csv (searched) or conflict_sourced.csv (sourced); BATCH: the batch page's number, linked in
each edit summary; --since/--until: the cases logged in that time window (ISO times, as in the log). Without --save,
a dry run: what each case would do, counted. Results go to conflict_applied.csv; cases already there are skipped.
"""
import collections
import csv
import datetime
import json
import sys
import time
from pathlib import Path

import dictionaries as d
from common import ROOT, only_import_refs
from conflict_check import LABELS, WORK_NAMES, decide, nested_row, show

OUT = ROOT / "conflict_applied.csv"
FIELDS = ["time", "batch", "work", "entry", "qid", "property", "decision", "entry_value", "wikidata_value", "revid",
          "result"]
REQUEST3 = "[[Wikidata:Requests for permissions/Bot/OKA bot 3|OKA bot 3]]"
ERROR_IN_SOURCE = "Q29998666"
WHAT = {"prefer": "preferred rank (best referenced value; the other value has no source)",
        "both": "beside the other value (sources disagree)",
        "report": "deprecated rank (error in referenced source: other sources give the item's value)"}


_shown = {}


def shown(v):
    """show() with the place labels remembered (each label is one request)."""
    if v.get("type") == "time":
        return show(v)
    q = v["value"]["id"]
    if q not in _shown:
        _shown[q] = show(v)
    return _shown[q]


def patient(fn, *args):
    """fn(...), waiting out long maxlag periods (pywikibot gives up after its retries) instead of stopping."""
    from pywikibot.exceptions import MaxlagTimeoutError
    while True:
        try:
            return fn(*args)
        except MaxlagTimeoutError:
            print("Wikidata has asked bots to wait (maxlag) for longer than pywikibot retries; trying again in 10 minutes",
                  flush=True)
            time.sleep(600)


def cases(log, since=None, until=None):
    for r in csv.DictReader(open(log, encoding="utf-8")):
        if since and r["time"] < since or until and r["time"] > until:
            continue
        if r["model"] != "none":
            r["decision"] = decide(json.loads(r["evidence"] or "[]"), r.get("established"), r["work"], r["property"])
        if nested_row(r):
            r["decision"] = "same"
        yield r


def plans_for(works):
    plans = {}
    for w in works:
        p = d.WORK2 / f"full_plan_{w}.jsonl"
        if p.exists():
            plans.update({(w, e["id"]): e for e in map(json.loads, open(p, encoding="utf-8"))})
    return plans


def claim_for(r, e, f, existing, today):
    """The statement to add for this outcome."""
    cite = e.get("cite")
    ref = d.reference(*((cite["work"], cite["slug"]) if cite else (e["work"], e["id"])), today)
    if r["decision"] == "prefer":
        c = d.statement(f["pid"], f["value"], ref, rank="preferred")          # with P7452 best referenced value
    elif r["decision"] == "both":
        same_rank = "preferred" if existing and all(x["rank"] == "preferred" for x in existing) else "normal"
        c = d.statement(f["pid"], f["value"], ref)
        c["rank"] = same_rank
    else:                                                                     # report
        c = d.statement(f["pid"], f["value"], ref, rank="deprecated")
        c["qualifiers"] = {"P2241": [{"snaktype": "value", "property": "P2241", "datavalue": d.item_value(ERROR_IN_SOURCE)}]}
        c["qualifiers-order"] = ["P2241"]
    for p, vs in f.get("qualifiers", {}).items():                             # 'about', a calendar note
        c.setdefault("qualifiers", {})[p] = [{"snaktype": "value", "property": p, "datavalue": v} for v in vs]
        c["qualifiers-order"] = list(dict.fromkeys(c.get("qualifiers-order", []) + [p]))
    return c


_places = {}


def same_place(a, b):
    """Two place items for one place: an old name kept as an item of its own (Kristiania, Christiania for Oslo), or
    items that say they are the same (P460, said to be the same as)."""
    if d.SAME_PLACE.get(a, a) == d.SAME_PLACE.get(b, b):
        return True
    missing = [q for q in (a, b) if q not in _places]
    if missing:
        _places.update(d.items(missing))
    p460 = lambda q: {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
                      for c in _places.get(q, {}).get("claims", {}).get("P460", [])}
    return b in p460(a) or a in p460(b)


def check_live(r, ent, f):
    """(existing claims, why it is left alone or '')."""
    if not ent:
        return [], "item not found"
    if ent.get("redirects") or ent.get("id") != r["qid"]:
        return [], "item merged since the check"
    existing = [c for c in ent.get("claims", {}).get(f["pid"], [])
                if c["rank"] != "deprecated" and c["mainsnak"]["snaktype"] == "value"]
    if any(d.same(c["mainsnak"]["datavalue"], f["value"]) for c in existing):
        return existing, "the entry's value is already on the item"
    if any(d.same(c["mainsnak"]["datavalue"], f["value"]) for c in ent.get("claims", {}).get(f["pid"], [])):
        return existing, "the entry's value is already on the item (deprecated)"
    if not existing:
        return existing, "Wikidata's value is gone"
    if f["value"]["type"] == "wikibase-entityid" and any(
            same_place(c["mainsnak"]["datavalue"]["value"]["id"], f["value"]["value"]["id"]) for c in existing):
        return existing, "the same place under another name (no disagreement)"
    if "; ".join(shown(c["mainsnak"]["datavalue"]) for c in existing) != r["wikidata_value"]:
        return existing, "Wikidata's values changed since the check"
    cited = any(c.get("references") and not only_import_refs(c) for c in existing)
    if cited != (r["model"] == "none"):
        return existing, "the sources of Wikidata's value changed since the check"
    return existing, ""


def summary(r, batch, value):
    return (f"Add {r['property']} {value} from {WORK_NAMES[r['work']]}, {WHAT[r['decision']]}; the item says "
            f"{r['wikidata_value']}. Case checked in [[User:OKA bot/Disagreement checks/Batch {batch}]] ({REQUEST3})")


def save(site, qid, claim, baserevid, text):
    from pywikibot.data import api
    from pywikibot.exceptions import APIError
    from common import edit_slot
    edit_slot()
    try:
        res = api.Request(site=site, parameters={"action": "wbeditentity", "id": qid, "bot": 1, "baserevid": baserevid,
                                                 "data": json.dumps({"claims": [claim]}),
                                                 "token": site.tokens["csrf"], "summary": text}).submit()
        return res["entity"]["lastrevid"], None
    except APIError as e:
        return None, f"refused by Wikidata: {e.code}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
    log, batch = Path(args[0]), args[1]
    save_ = "--save" in sys.argv
    rows = list(cases(log if log.is_absolute() else ROOT / log, opt("--since"), opt("--until")))
    done = {(x["work"], x["entry"], x["property"]) for x in csv.DictReader(open(OUT, encoding="utf-8"))} \
        if OUT.exists() else set()
    todo = [r for r in rows if (r["work"], r["entry"], r["property"]) not in done]
    print(f"{len(rows)} cases, {len(todo)} not done yet:", dict(collections.Counter(r["decision"] for r in todo)), flush=True)
    plans = plans_for({r["work"] for r in todo})
    site = None
    if save_:
        from common import bot_site
        site = bot_site(1)
    counts = collections.Counter()
    new = not OUT.exists()
    with open(OUT, "a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        if new and save_:
            w.writeheader()
        for i in range(0, len(todo), 50):
            chunk = todo[i:i + 50]
            live = d.items(sorted({r["qid"] for r in chunk}))
            today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
            for r in chunk:
                row = {k: r.get(k, "") for k in FIELDS if k in r}
                row.update(batch=batch, revid="", time=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                e = plans.get((r["work"], r["entry"]))
                f = next((x for x in (e or {}).get("facts", []) if x["pid"] == LABELS[r["property"]]), None)
                if r["decision"] not in WHAT:
                    result = "nothing to do" if r["decision"] == "same" else "no answer yet"
                elif not f:
                    result = "left alone: the entry's fact is not in the plan"
                else:
                    ent = live.get(r["qid"], {})
                    existing, why = check_live(r, ent, f)
                    if why:
                        result = "left alone: " + why
                    elif not save_:
                        result = f"would add ({r['decision']})"
                    else:
                        claim = claim_for(r, e, f, existing, today)
                        rev, err = patient(save, site, r["qid"], claim, ent.get("lastrevid"),
                                           summary(r, batch, shown(f["value"])))
                        if err:
                            result = err
                        else:
                            time.sleep(1)
                            back = d.items([r["qid"]]).get(r["qid"], {})
                            ok = any(d.same(c["mainsnak"]["datavalue"], f["value"]) and c["rank"] == claim["rank"]
                                     for c in back.get("claims", {}).get(f["pid"], []))
                            row["revid"] = rev
                            result = f"added ({r['decision']})" if ok else "NOT CONFIRMED by read-back"
                counts[result.split(":")[0] if result.startswith("left alone") else result] += 1
                row["result"] = result
                if save_:
                    w.writerow(row)
                    fh.flush()
                if not save_ and counts[result] <= 3:
                    print(f"  {r['work']} {r['entry']} {r['qid']} {r['property']}: {result} | item {r['wikidata_value']}"
                          f" | entry {r['entry_value']}", flush=True)
                if result == "NOT CONFIRMED by read-back":
                    sys.exit("stopped: an edit was not confirmed by the read-back")
            print(f"{i + len(chunk)} of {len(todo)}:", dict(counts), flush=True)
    print("done:", dict(counts))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
