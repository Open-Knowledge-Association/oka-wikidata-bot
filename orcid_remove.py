"""Remove the ORCID iDs (P496) that QuickStatements batch #40464 copied from GND onto the wrong person: the "wrong iD"
rows of User:OKA bot/Merges/ORCID wrong (orcid_records.py: the ORCID record itself shows someone else). Task 3
(Wikidata:Requests for permissions/Bot/OKA bot 3, approved 3 November 2026) allows removing such identifiers after
review and with the import's author's agreement; Printstream agreed on 16 October 2026, and the reviewed list was
posted on their talk page on 4 November 2026. jz gave the go on 10 November 2026.

Only a statement still in the batch's shape is removed: one P496 statement with that iD, not deprecated, whose
references cite nothing but the item's own GND ID (P227). Anything else (someone added another source, deprecated
it, or the item was merged or deleted) is left alone and listed. The GND IDs stay: the GND record is right in 102
cases, and in the others it is not clear which side is wrong.

    python orcid_remove.py            dry run: what would be removed, what is left alone and why
    python orcid_remove.py --save     remove as OKA bot, each read back; logged in orcid_removals.csv
"""
import csv
import datetime
import json
import sys
import time

import ops
import orcid_records as o
from common import ROOT, out, bot_site

DATA = ROOT / "work" / "merges" / "orcid_wrong.json"
LOG = ROOT / "orcid_removals.csv"
REQUEST3 = "[[Wikidata:Requests for permissions/Bot/OKA bot 3|OKA bot 3]]"
PAUSE = 3
REASON = {"work after death": "work dated after this person's death",
          "work before birth": "work dated before this person could have done it",
          "another name": "it is in someone else's name",
          "another middle initial": "it is in someone else's name",
          "nothing dated, died before 2012": "it shows nothing dated, and this person died before ORCID existed",
          "checked by hand": "it is someone in another field or place"}


def wrong_rows():
    rows = json.loads(DATA.read_text(encoding="utf-8"))["rows"]
    return [r for r in rows if r.get("orcid_verdict") == "confirmed wrong"]


def decide(r, e):
    """(claim to remove or None, why not)."""
    if "missing" in e:
        return None, "item deleted"
    if "redirects" in e:
        return None, f"item merged into {e['redirects']['to']}"
    cs = [c for c in e.get("claims", {}).get("P496", [])
          if (c["mainsnak"].get("datavalue") or {}).get("value") == r["orcid"]]
    if not cs:
        return None, "iD no longer on the item"
    if len(cs) > 1:
        return None, f"{len(cs)} statements with this iD"
    c = cs[0]
    if c["rank"] == "deprecated":
        return None, "already deprecated"
    refs = c.get("references", [])
    if not refs:
        return None, "no reference (not the batch's statement)"
    for ref in refs:
        if set(ref["snaks"]) != {"P227"}:
            return None, "another reference was added: " + ", ".join(sorted(set(ref["snaks"]) - {"P227"}))
    # The reference may cite a GND ID that someone has since removed from the item (5 cases on 10 November 2026):
    # the iD came with that wrong GND link and is still wrong, so it goes too.
    return c, ""


def summary(r):
    return (f"Remove ORCID iD {r['orcid']}: its ORCID record is another person's ({REASON[r['orcid_group']]}). "
            "It was copied from GND by QuickStatements batch #40464. Reviewed list: [[User:OKA bot/Merges/ORCID wrong]]; "
            "agreed with the batch's author: [[User talk:Printstream#ORCIDs added via GND (batch #40464)]]")


def log(row):
    new = not LOG.exists()
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "item", "label", "orcid", "reason", "mistake_in", "revid", "result"])
        w.writerow(row)


def main():
    save = "--save" in sys.argv
    rows = wrong_rows()
    done = {(x["item"], x["orcid"]) for x in csv.DictReader(open(LOG, encoding="utf-8"))
            if x["result"] == "removed"} if LOG.exists() else set()
    site = bot_site(PAUSE) if save else None
    removed = left = 0
    for i in range(0, len(rows), 50):
        chunk = rows[i:i + 50]
        ents = ops.fetch(site, sorted({r["item"] for r in chunk}))
        for r in chunk:
            tag = f"{r['item']} {r['label']} {r['orcid']}"
            if (r["item"], r["orcid"]) in done:
                continue
            c, why = decide(r, ents.get(r["item"], {"missing": ""}))
            now = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            if c is None:
                left += 1
                out(f"{tag}: left alone ({why})")
                if save:
                    log([now, r["item"], r["label"], r["orcid"], r["orcid_group"], o.mistake_in(r), "", f"left alone: {why}"])
                continue
            if not save:
                removed += 1
                out(f"{tag}: would remove ({r['orcid_group']}; mistake in {o.mistake_in(r)})")
                continue
            e = ents[r["item"]]
            res = ops.submit(site, {"action": "wbremoveclaims", "claim": c["id"], "baserevid": e["lastrevid"]},
                             summary(r), REQUEST3)
            rev = ops.revid(res) or res.get("pageinfo", {}).get("lastrevid", "")
            time.sleep(PAUSE)
            again, _ = decide(r, ops.fetch(site, [r["item"]]).get(r["item"], {"missing": ""}))
            ok = again is None
            removed += ok
            log([now, r["item"], r["label"], r["orcid"], r["orcid_group"], o.mistake_in(r), rev,
                 "removed" if ok else "NOT CONFIRMED by read-back"])
            out(f"{tag}: {'removed' if ok else 'NOT CONFIRMED'} r{rev}")
            if not ok:
                sys.exit("stopped: a removal was not confirmed by the read-back")
    out(f"{'removed' if save else 'would remove'} {removed}, left alone {left}, of {len(rows)} wrong iDs")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
