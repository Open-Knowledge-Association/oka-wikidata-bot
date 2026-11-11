"""OKA bot: single Wikidata edits decided case by case around jz's translations from the Historical Dictionary of
Switzerland (sitelinks for new English Wikipedia articles, merges of bot-created duplicates, fixes found while
translating), in the context of Wikidata:Requests for permissions/Bot/OKA bot 2. Not a mass task: every operation
comes from a reviewed JSON file.

    python ops.py FILE.json             apply the operations as OKA bot, one at a time, each read back
    python ops.py FILE.json --dry-run   show what would change, without logging in

FILE.json is a list of operations, each with "op", "summary" and the fields below:
    label / description  id, lang, value, expect   expect missing or null: only if there is none; "*": replace any
                                                   value; a string: replace only that value
    aliases              id, lang, add?, remove?   add the aliases that are missing; remove the listed ones that are there
    claim                id, prop, value, hds?|source?, rank?, qualifiers?   add unless the same value is there
                                                   (source: a dataset item cited with P248 + P813)
    remove_claim         id, prop, value           remove the one statement with that value
    reference            id, prop, value, hds      add an HDS reference to the one statement with that value
    qualifier            id, prop, value, qualifier, old, new   change one qualifier value
    calendar             id, prop, value, calendar ("julian"/"gregorian")   the calendar model of one date
    sitelink             id, site, title, expect?  set it; changing or removing ("") an existing one needs expect
    merge                from, to                  merge, then make sure the old item redirects
    create               data, request?            a new item from wbeditentity data, refused when an item already has its
                                                   English label (request: the approval its summary links)
    raw                  params                    any other API write (no check: avoid)
A value is {"item": "Q1"}, {"string": "x"}, {"commons": "File name.jpg"}, {"time": "+2004-01-01T00:00:00Z",
"precision": 11} or {"coord": [47.5311, 9.1233], "precision": 0.0001}. "hds" is an HDS article number such as "000832": the statement is cited to the HDS (P248 Q642074,
P902, P813). An operation that is already done is skipped; one whose item differs from what it expects is refused.
Saved edits are not flagged as bot edits and are logged in hds_edits.csv."""
import csv
import datetime
import json
import sys
import time
import urllib.parse

from common import ROOT, curl_json, entity, ref_json, out, bot_site

HDS = "Q642074"
REQUEST2 = "[[Wikidata:Requests for permissions/Bot/OKA bot 2|OKA bot 2]]"
LOG = ROOT / "hds_edits.csv"
API = "https://www.wikidata.org/w/api.php"
PAUSE = 3                                          # seconds between saves (at most 20 a minute)
MAX_ERRORS = 3


def dv(v):
    if "item" in v:
        return {"type": "wikibase-entityid", "value": entity(v["item"])}
    if "string" in v or "commons" in v:
        return {"type": "string", "value": v.get("string", v.get("commons"))}
    if "time" in v:
        return {"type": "time", "value": {"time": v["time"], "timezone": 0, "before": 0, "after": 0,
                                          "precision": v.get("precision", 11),
                                          "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}}
    if "coord" in v:
        return {"type": "globecoordinate", "value": {"latitude": v["coord"][0], "longitude": v["coord"][1], "altitude": None,
                                                     "precision": v.get("precision", 0.0001),
                                                     "globe": "http://www.wikidata.org/entity/Q2"}}
    raise ValueError(f"unknown value {v}")


def same(snak, v):
    got, want = (snak.get("datavalue") or {}).get("value"), dv(v)["value"]
    if isinstance(want, dict) and "id" in want:
        return isinstance(got, dict) and got.get("id") == want["id"]
    if isinstance(want, dict) and "time" in want:
        return isinstance(got, dict) and got.get("time") == want["time"]
    if isinstance(want, dict) and "latitude" in want:
        return isinstance(got, dict) and abs(got.get("latitude", 999) - want["latitude"]) < 1e-6 \
            and abs(got.get("longitude", 999) - want["longitude"]) < 1e-6
    return got == want


def hds_ref(hds, today):
    r = ref_json(HDS, today)
    r["snaks"]["P902"] = [{"snaktype": "value", "property": "P902", "datavalue": {"type": "string", "value": hds}}]
    r["snaks-order"] = ["P248", "P902", "P813"]
    return r


def matching(e, prop, v):
    return [c for c in e.get("claims", {}).get(prop, []) if same(c["mainsnak"], v)]


def plan(op, ents, today):
    """(API parameters or None, what it means). None with "already" means done; with "refused: ..." means stop."""
    k = op["op"]
    if k == "raw":
        return dict(op["params"]), "raw " + op["params"].get("action", "?")
    if k == "create":                              # a new item, unless an item already has its English label
        label = op["data"]["labels"]["en"]["value"]
        found = curl_json(API + "?" + urllib.parse.urlencode({"action": "wbsearchentities", "search": label,
                                                               "language": "en", "type": "item", "format": "json"}))
        same_name = [x["id"] for x in found.get("search", []) if x.get("label", "").lower() == label.lower()]
        if same_name:
            return None, "already" if op.get("created") in same_name else f"refused: {', '.join(same_name)} has the label"
        return {"action": "wbeditentity", "new": "item", "data": json.dumps(op["data"], ensure_ascii=False)}, \
            f"new item {label}"
    if k == "merge":
        frm = ents.get(op["from"], {})
        if frm.get("redirects", {}).get("to") == op["to"] or ("missing" in frm and op.get("done_if_missing")):
            return None, "already"
        if "redirects" in frm:
            return None, f"refused: {op['from']} redirects to {frm['redirects']['to']}"
        if not frm.get("claims") and not frm.get("sitelinks"):   # already merged, but not yet a redirect
            return {"_finish_only": True}, f"redirect {op['from']} to {op['to']} (content already merged)"
        return {"action": "wbmergeitems", "fromid": op["from"], "toid": op["to"], "ignoreconflicts": "description"}, \
            f"merge {op['from']} into {op['to']}"
    e = ents.get(op["id"], {})
    if "missing" in e or "redirects" in e:
        return None, f"refused: {op['id']} is {'missing' if 'missing' in e else 'a redirect'}"
    base = {"baserevid": e["lastrevid"]}
    if k in ("label", "description"):
        cur = e.get(k + "s", {}).get(op["lang"], {}).get("value")
        if cur == op["value"]:
            return None, "already"
        exp = op.get("expect")
        if exp is None and cur:
            return None, f"refused: {op['lang']} {k} is {cur!r}"
        if exp not in (None, "*") and cur != exp:
            return None, f"refused: {op['lang']} {k} is {cur!r}, expected {exp!r}"
        return dict(base, action="wbsetlabel" if k == "label" else "wbsetdescription", id=op["id"], language=op["lang"],
                    value=op["value"]), f"{op['lang']} {k} {cur!r} -> {op['value']!r}"
    if k == "aliases":
        have = {a["value"] for a in e.get("aliases", {}).get(op["lang"], [])}
        new = [a for a in op.get("add", []) if a not in have and a != e.get("labels", {}).get(op["lang"], {}).get("value")]
        gone = [a for a in op.get("remove", []) if a in have]        # only aliases that are there (our own wrong ones)
        if not new and not gone:
            return None, "already"
        params = dict(base, action="wbsetaliases", id=op["id"], language=op["lang"])
        if new:
            params["add"] = "|".join(new)
        if gone:
            params["remove"] = "|".join(gone)
        return params, f"{op['lang']} aliases" + (f" + {new}" if new else "") + (f" - {gone}" if gone else "")
    if k == "claim":
        if matching(e, op["prop"], op["value"]):
            return None, "already"
        c = {"type": "statement", "rank": op.get("rank", "normal"),
             "mainsnak": {"snaktype": "value", "property": op["prop"], "datavalue": dv(op["value"])}}
        if op.get("hds"):
            c["references"] = [hds_ref(op["hds"], today)]
        elif op.get("source"):                     # a dataset item, e.g. swissNAMES3D: P248 + P813
            c["references"] = [ref_json(op["source"], today)]
        if op.get("qualifiers"):
            c["qualifiers"] = {p: [{"snaktype": "value", "property": p, "datavalue": dv(v)}]
                               for p, v in op["qualifiers"].items()}
        return dict(base, action="wbeditentity", id=op["id"], data=json.dumps({"claims": [c]})), \
            f"+ {op['prop']} {json.dumps(op['value'])}"
    if k in ("remove_claim", "reference", "qualifier"):
        cs = matching(e, op["prop"], op["value"])
        if k == "remove_claim" and not cs:
            return None, "already"
        if len(cs) != 1:
            return None, f"refused: {len(cs)} statements {op['prop']} {json.dumps(op['value'])}"
        c = cs[0]
        if k == "remove_claim":
            return dict(base, action="wbremoveclaims", claim=c["id"]), f"- {op['prop']} {json.dumps(op['value'])}"
        if k == "reference":
            if any(s["datavalue"]["value"] == op["hds"] for r in c.get("references", []) for s in r["snaks"].get("P902", [])
                   if s.get("datavalue")):
                return None, "already"
            r = hds_ref(op["hds"], today)
            return dict(base, action="wbsetreference", statement=c["id"], snaks=json.dumps(r["snaks"]),
                        **{"snaks-order": json.dumps(r["snaks-order"])}), f"reference HDS {op['hds']} on {op['prop']}"
        qs = c.get("qualifiers", {}).get(op["qualifier"], [])
        if any(same(q, op["new"]) for q in qs):
            return None, "already"
        old = [q for q in qs if same(q, op["old"])]
        if len(old) != 1:
            return None, f"refused: {len(old)} qualifiers {op['qualifier']} {json.dumps(op['old'])}"
        return dict(base, action="wbsetqualifier", claim=c["id"], property=op["qualifier"], snaktype="value",
                    value=json.dumps(dv(op["new"])["value"]), snakhash=old[0]["hash"]), \
            f"{op['prop']} qualifier {op['qualifier']} -> {json.dumps(op['new'])}"
    if k == "calendar":                            # the calendar model of one date (Help:Dates: Julian before 1583)
        cs = matching(e, op["prop"], op["value"])
        if len(cs) != 1:
            return None, f"refused: {len(cs)} statements {op['prop']} {json.dumps(op['value'])}"
        c = json.loads(json.dumps(cs[0]))
        v = c["mainsnak"]["datavalue"]["value"]
        want = {"julian": "http://www.wikidata.org/entity/Q1985786",
                "gregorian": "http://www.wikidata.org/entity/Q1985727"}[op["calendar"]]
        if v["calendarmodel"] == want:
            return None, "already"
        v["calendarmodel"] = want
        return dict(base, action="wbsetclaim", claim=json.dumps(c)), f"{op['prop']} {v['time']} calendar -> {op['calendar']}"
    if k == "sitelink":
        cur = e.get("sitelinks", {}).get(op["site"], {}).get("title", "")
        if cur == op["title"]:
            return None, "already"
        if cur and cur != op.get("expect"):
            return None, f"refused: {op['site']} is {cur!r}"
        return dict(base, action="wbsetsitelink", id=op["id"], linksite=op["site"], linktitle=op["title"]), \
            f"{op['site']} {cur!r} -> {op['title']!r}"
    raise ValueError(f"unknown op {k}")


def ids_of(op):
    return [op[k] for k in ("id", "from", "to") if op.get(k)]


def fetch(site, ids):
    if not ids:                                    # a new item: nothing to read first
        return {}
    params ={"action": "wbgetentities", "ids": "|".join(ids), "props": "info|labels|descriptions|aliases|claims|sitelinks"}
    if site is None:
        ents = curl_json(API + "?" + urllib.parse.urlencode(dict(params, format="json")))["entities"]
    else:
        from pywikibot.data import api
        ents = api.Request(site=site, parameters=params).submit()["entities"]
    for e in list(ents.values()):                  # a redirected id comes back under its target's id
        if "redirects" in e:
            ents[e["redirects"]["from"]] = e
    return ents


def submit(site, params, summary, request=REQUEST2):
    from pywikibot.data import api
    from common import edit_slot
    edit_slot()                                    # at most 60 edits a minute across all of the bot's jobs
    return api.Request(site=site, parameters=dict(params, token=site.tokens["csrf"],
                                                  summary=f"{summary} ({request})")).submit()


def revid(r):
    for k in ("entity", "pageinfo", "redirected"):
        v = r.get(k)
        if isinstance(v, dict) and v.get("lastrevid"):
            return v["lastrevid"]
    return r.get("to", {}).get("lastrevid", "")


def log(row):
    new = not LOG.exists()
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "op", "item", "change", "summary", "revid", "result"])
        w.writerow(row)


def finish_merge(site, op):
    """After wbmergeitems the old item normally redirects; if not, clear its leftovers and redirect it."""
    from pywikibot.exceptions import APIError
    for attempt in (1, 2):
        frm = fetch(site, [op["from"]])[op["from"]]
        if "redirects" in frm:
            return "redirects"
        try:
            submit(site, {"action": "wbcreateredirect", "from": op["from"], "to": op["to"]},
                   op.get("summary") or f"Merge duplicate into {op['to']}")
        except APIError as e:
            if e.code != "origin-not-empty" or attempt == 2:
                return f"redirect refused: {e.code}"
            left = {k: {l: {"language": l, "value": ""} for l in frm.get(k, {})} for k in ("labels", "descriptions")}
            submit(site, {"action": "wbeditentity", "id": op["from"], "data": json.dumps({k: v for k, v in left.items() if v})},
                   f"Clear what is left after the merge into {op['to']}")
            time.sleep(PAUSE)
    return "redirects" if "redirects" in fetch(site, [op["from"]])[op["from"]] else "not redirected"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    ops = json.load(open(args[0], encoding="utf-8"))
    for op in ops:
        if not op.get("summary") and op["op"] != "merge":
            sys.exit(f"every operation needs a summary: {op}")
    today = "+" + datetime.datetime.utcnow().strftime("%Y-%m-%d") + "T00:00:00Z"
    site = None if dry else bot_site(PAUSE)
    from_err = __import__("pywikibot.exceptions", fromlist=["APIError"]).APIError if site else Exception
    saved = skipped = refused = errors = 0
    for op in ops:
        ents = fetch(site, ids_of(op))
        params, what = plan(op, ents, today)
        tag = f"{op['op']} {' '.join(ids_of(op))}"
        if params is None:
            out(f"{tag}: {what}")
            if what == "already":
                skipped += 1
            else:
                refused += 1
            continue
        if dry:
            out(f"{tag}: would {what}")
            continue
        try:
            r = {} if params.get("_finish_only") else submit(site, params, op.get("summary") or f"Merge duplicate into {op.get('to')}",
                                                             op.get("request", REQUEST2))
        except from_err as e:
            errors += 1
            out(f"{tag}: ERROR {getattr(e, 'code', '')} {e}")
            log([datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z", op["op"], " ".join(ids_of(op)), what,
                 op.get("summary", ""), "", f"error {getattr(e, 'code', '')}"])
            if errors >= MAX_ERRORS:
                out("stopping after %d errors" % errors)
                break
            time.sleep(PAUSE)
            continue
        rev = revid(r)
        time.sleep(PAUSE)
        if op["op"] == "merge":
            result = finish_merge(site, op)
            ok = result == "redirects"
        elif op["op"] == "raw":
            result, ok = "saved (not checked)", True
        elif op["op"] == "create":
            new_id = r.get("entity", {}).get("id", "")
            ok = bool(new_id) and "missing" not in fetch(site, [new_id]).get(new_id, {"missing": ""})
            result = f"created {new_id}" if ok else "NOT CONFIRMED by read-back"
            what += f" = {new_id}"
        else:
            again, _ = plan(op, fetch(site, ids_of(op)), today)
            ok = again is None
            result = "checked" if ok else "NOT CONFIRMED by read-back"
        saved += ok
        errors += not ok
        log([datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z", op["op"], " ".join(ids_of(op)), what,
             op.get("summary", ""), rev, result])
        out(f"{tag}: {what} | r{rev} {result}")
    out(f"done: {saved} saved, {skipped} already done, {refused} refused, {errors} errors" + (" (dry run)" if dry else ""))


if __name__ == "__main__":
    main()
