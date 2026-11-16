"""Lists of disagreements for the encyclopedias themselves (jz, 11 November 2026: report the inconsistencies to the
encyclopedias where they exist; the unsure ones as possible errors, where the entry differs from another source).

    python encyclopedia_reports.py out_dir             CSV per work (reads Wikidata, edits nothing)
    python encyclopedia_reports.py --xlsx out_dir      one spreadsheet per work from those CSVs

For each work of task 2 (Store norske leksikon, Norsk kunstnerleksikon, the Historical Dictionary of Switzerland),
three sheets:
  Errors                    the web search found sources for another value and none for the entry's ("report")
  Sources disagree          searched cases where sources found support each value ("both"), unless they lean to the
                            entry's value
  Differs from other works  the entry differs from a value on Wikidata that cites a published work (sourced cases),
                            with that work named; no web search was made for these
Left out: places that are the same place under another name or one inside the other, and cases the search did not
settle ("retry", "review").
"""
import collections
import csv
import json
import re
import sys
import urllib.parse
from pathlib import Path

import dictionaries as d
from common import ROOT, only_import_refs
from conflict_apply import _places, cases, same_place
from conflict_check import LABELS, URLS, WORK_NAMES, counted

QID = re.compile(r"\((Q\d+)\)")
FIELDS = ["sheet", "entry", "entry_url", "person", "wikidata_item", "fact", "entry_value", "other_value", "assessment",
          "sources", "note"]
SHEETS = ["Errors", "Sources disagree", "Differs from other works"]
LANGS = {"snl": ["nb", "nn", "en"], "nkl": ["nb", "nn", "en"], "hds": ["de", "fr", "it", "en"]}
LEAN = {"wikidata": "Most sources found give the other value.", "split": "The sources found are split.",
        "unclear": "The sources found do not settle it.", "both": "The sources found fit both values."}


def clean(url):
    """A Google Translate proxy link → the page itself."""
    u = urllib.parse.urlsplit(url.strip())
    if u.netloc.endswith(".translate.goog"):
        host = u.netloc[:-len(".translate.goog")].replace("--", "\0").replace("-", ".").replace("\0", "-")
        q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if not k.startswith("_x_tr_")]
        return urllib.parse.urlunsplit((u.scheme, host, u.path, urllib.parse.urlencode(q), u.fragment))
    return url.strip()


def label(ent, langs):
    labels = ent.get("labels", {})
    for lang in langs + ["mul", "en"]:
        if lang in labels:
            return labels[lang]["value"]
    return next((x["value"] for x in labels.values()), ent.get("id", ""))


def fetch(qids, keep=()):
    """{qid: entity} for qids, 50 at a time, with only the labels and the claims of the properties in keep (whole
    items for 7,000 people do not fit in a job's memory)."""
    found = {}
    qids = sorted(set(qids))
    for i in range(0, len(qids), 50):
        for q, e in d.items(qids[i:i + 50]).items():
            found[q] = {"id": e.get("id"), "labels": e.get("labels", {}),
                        "claims": {p: e["claims"][p] for p in keep if p in e.get("claims", {})}}
    return found


def same_place_case(r):
    if not r["property"].startswith("place"):
        return False
    return any(same_place(a, b) for a in QID.findall(r["entry_value"]) for b in QID.findall(r["wikidata_value"]))


def sources_text(evidence, work):
    lines = []
    side = {"wikidata": "other value", "entry": "entry's value", "both": "fits both", "other": "a third value"}
    for x in counted(evidence, work):
        says = re.sub(r"\s+", " ", x.get("says", "")).strip()[:300]
        lines.append(f"{clean(x.get('url', ''))} ({side.get(x.get('supports'), x.get('supports'))}): “{says}”")
    return "\n".join(lines)


def cited_works(ent, r, langs, names):
    """The works cited for the item's other value(s) of this fact: 'stated in' labels and reference URLs."""
    pid = LABELS[r["property"]]
    entry_q = QID.findall(r["entry_value"])
    out = []
    for c in ent.get("claims", {}).get(pid, []):
        if c["rank"] == "deprecated" or "datavalue" not in c["mainsnak"]:
            continue
        v = c["mainsnak"]["datavalue"]
        if v.get("type") == "wikibase-entityid" and (v["value"]["id"] in entry_q
                                                     or any(same_place(v["value"]["id"], q) for q in entry_q)):
            continue
        if v.get("type") == "time" and r["entry_value"] and v["value"]["time"].lstrip("+").startswith(r["entry_value"]):
            continue
        if only_import_refs(c):
            continue
        for ref in c.get("references", []):
            for s in ref["snaks"].get("P248", []):
                if "datavalue" in s:
                    out.append(names.get(s["datavalue"]["value"]["id"], s["datavalue"]["value"]["id"]))
            for s in ref["snaks"].get("P854", []):
                if "datavalue" in s:
                    out.append(clean(s["datavalue"]["value"]))
    return list(dict.fromkeys(out))


def build(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    searched = [r for r in cases(ROOT / "conflict_checks.csv")]
    sourced = [r for r in cases(ROOT / "conflict_sourced.csv")]
    print(len(searched), "searched,", len(sourced), "sourced cases", flush=True)
    rows = searched + sourced
    places = {q for r in rows if r["property"].startswith("place")
              for q in QID.findall(r["entry_value"] + r["wikidata_value"])}
    _places.update(fetch(places, ("P460",)))
    people = fetch((r["qid"] for r in rows), tuple(LABELS.values()))
    print(len(people), "people read", flush=True)
    stated = {s["datavalue"]["value"]["id"] for e in people.values() for cs in e.get("claims", {}).values() for c in cs
              for ref in c.get("references", []) for s in ref["snaks"].get("P248", []) if "datavalue" in s}
    stated_items = fetch(stated)
    out = collections.defaultdict(list)
    counts = collections.Counter()
    for r in rows:
        w, langs = r["work"], LANGS[r["work"]]
        if r["decision"] in ("same", "prefer", "retry", "review", "replace") or same_place_case(r):
            counts[(w, "left out: " + r["decision"])] += 1
            continue
        ent = people.get(r["qid"], {})
        row = {"entry": r["entry"], "entry_url": URLS[w].format(r["entry"]), "person": label(ent, langs),
               "wikidata_item": f"https://www.wikidata.org/wiki/{r['qid']}", "fact": r["property"],
               "entry_value": r["entry_value"], "other_value": r["wikidata_value"], "note": r["note"]}
        if r["model"] != "none":
            ev = json.loads(r["evidence"] or "[]")
            if r["decision"] == "report":
                row["sheet"] = "Errors"
                row["assessment"] = ("Sources found give the other value; none gives the entry's."
                                     if r["established"] == "wikidata" else
                                     "Sources found give yet another value; none gives the entry's.")
            elif r["established"] == "entry":
                counts[(w, "left out: sources lean to the entry")] += 1
                continue
            else:
                row["sheet"] = "Sources disagree"
                row["assessment"] = LEAN.get(r["established"], "")
            row["sources"] = sources_text(ev, w)
        else:
            names = {q: label(e, langs) for q, e in stated_items.items()}
            works = cited_works(ent, r, langs, names)
            if not works:
                counts[(w, "left out: the other value no longer cites a work")] += 1
                continue
            row.update(sheet="Differs from other works", assessment="The other value cites: " + "; ".join(works),
                       sources="", note="")
        counts[(w, row["sheet"])] += 1
        out[w].append(row)
    for w, rs in out.items():
        rs.sort(key=lambda x: (SHEETS.index(x["sheet"]), x["person"].casefold(), x["fact"]))
        with open(out_dir / f"{w}.csv", "w", encoding="utf-8", newline="") as f:
            wr = csv.DictWriter(f, FIELDS)
            wr.writeheader()
            wr.writerows(rs)
    for k, n in sorted(counts.items()):
        print(k, n)
    cited = collections.Counter((w, x) for w, rs in out.items() for r in rs if r["sheet"] == SHEETS[2]
                                for x in r["assessment"].removeprefix("The other value cites: ").split("; "))
    for (w, x), n in sorted(cited.items(), key=lambda kv: -kv[1])[:40]:
        print("cited", w, x, n)


def xlsx(out_dir):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    heads = {"person": ("Person", 28), "entry_url": ("Entry", 40), "fact": ("Fact", 14),
             "entry_value": ("Value in the entry", 24), "other_value": ("Other value (on Wikidata)", 30),
             "assessment": ("Assessment", 45), "sources": ("Sources found (link, which value, quote)", 80),
             "note": ("Note", 60), "wikidata_item": ("Wikidata item", 34)}
    for path in sorted(out_dir.glob("*.csv")):
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        wb = Workbook()
        wb.remove(wb.active)
        for sheet in SHEETS:
            rs = [r for r in rows if r["sheet"] == sheet]
            if not rs:
                continue
            ws = wb.create_sheet(sheet)
            cols = [k for k in heads if not (sheet == SHEETS[2] and k in ("sources", "note"))]
            ws.append([heads[k][0] for k in cols])
            for c, k in enumerate(cols, 1):
                ws.cell(1, c).font = Font(bold=True)
                ws.column_dimensions[ws.cell(1, c).column_letter].width = heads[k][1]
            for r in rs:
                ws.append([r[k] for k in cols])
                n = ws.max_row
                for c, k in enumerate(cols, 1):
                    cell = ws.cell(n, c)
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
                    if k in ("entry_url", "wikidata_item"):
                        cell.hyperlink = r[k]
                        cell.font = Font(color="0563C1", underline="single")
            ws.freeze_panes = "B2"
            ws.auto_filter.ref = ws.dimensions
        target = path.with_suffix(".xlsx")
        wb.save(target)
        print(target, {s: sum(r["sheet"] == s for r in rows) for s in SHEETS})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if a != "--xlsx"]
    (xlsx if "--xlsx" in sys.argv else build)(Path(args[0]))
