"""Rewrite the "Check first" part of User:OKA bot/Merges/ORCID wrong in plain words (jz, 17 October 2026): one table
per question, and for each person one sentence on what was found and one question to check. Reads
work/merges/orcid_wrong.json and replaces that section of work/merges/orcid_wrong.wiki.

    python orcid_check_page.py
"""
import json
import re
from pathlib import Path

WORK = Path(__file__).resolve().parent / "work" / "merges"
SOURCE = {"only from a Wikipedia": "only from a Wikipedia", "unreferenced": "with no source"}


def years(v):
    return ", ".join(v) if v else ""


def others(r):
    """The other items with the same iD, as text."""
    out = []
    for o in r.get("other") or []:
        n = o.get("articles") or 0
        out.append(f"{{{{Q|{o['item'][1:]}}}}}" + (f" ({n} scholarly article{'s' if n != 1 else ''})" if n else ""))
    return ", ".join(out)


def gnd(r):
    g = r.get("gnd_record") or {}
    bits = [b for b in (("born " + years(g.get("born"))) if g.get("born") else "",
                        ("died " + years(g.get("died"))) if g.get("died") else "",
                        ", ".join(g.get("occupations") or [])) if b]
    return f"[https://d-nb.info/gnd/{g['id']} {g['id']}]" + (f"<br /><small>{'; '.join(bits)}</small>" if bits else "") \
        if g.get("id") else ""


def researcher_row(r):
    occ = ", ".join(r.get("occupation_labels") or []) or "not a researcher"
    g = r.get("gnd_record") or {}
    found = f"On Wikidata: {occ}."
    if g.get("occupations"):
        found += f" But the GND record describes: {', '.join(g['occupations'])}."
    if r.get("articles"):
        found += f" The item is the author of {r['articles']} scholarly article{'s' if r['articles'] != 1 else ''}."
    question = "Same person as this researcher? If not, the item has a namesake's GND ID, and the iD came with it."
    return found, question


def death_row(r):
    src = SOURCE.get((r.get("date_sources") or {}).get("a", ""), "")
    g = r.get("gnd_record") or {}
    found = f"Wikidata says died {years(r.get('died'))}" + (f" ({src})" if src else "") + "."
    if not g.get("died"):
        found += " The GND record gives no date of death."
    if others(r):
        found += f" The same iD is on {others(r)}."
    return found, "Is the date of death right?"


def birth_row(r):
    g = r.get("gnd_record") or {}
    found = f"Wikidata says born {years(r.get('born'))}, with no date of death."
    if g.get("born"):
        found += f" The GND record says born {years(g['born'])}."
    else:
        found += " The GND record gives no date of birth."
    return found, "Is the date of birth right?"


def different_row(r):
    o = (r.get("other") or [{}])[0]
    found = (f"Marked as a different person from {{{{Q|{o.get('item', 'Q0')[1:]}}}}}, which has the same iD. This item "
             f"has {r.get('articles') or 0} scholarly articles on Wikidata, the other {o.get('articles') or 0}.")
    return found, "Which of the two people does this iD belong to?"


GROUPS = [
    ("Is this person also a researcher?", "c", researcher_row,
     "Wikidata gives only a non-research occupation (athlete, actor, musician…), but the GND record or the item's "
     "articles point to a researcher of the same name."),
    ("Is the date of death right?", "a", death_row,
     "Wikidata says the person died before ORCID existed (2012), but the date looks weak."),
    ("Is the date of birth right?", "b", birth_row,
     "Wikidata says the person was born before 1900, but there is no date of death and the date looks weak."),
    ("Which of the two people has this iD?", "d", different_row,
     "Two items with the same iD are marked as different people, and both look like researchers."),
]


def section(rows):
    lines = ["== Check first ==",
             "These need a look before anything is removed: something suggests the iD might be right after all. "
             "Each row says what we found and the question to answer."]
    for title, rule, fn, intro in GROUPS:
        part = [r for r in rows if r.get("check") and r["rules"][0] == rule]
        if not part:
            continue
        lines += ["", f"=== {title} ({len(part)}) ===", intro, "", '{| class="wikitable sortable"',
                  "! Person !! What we found !! Question !! GND record !! ORCID"]
        for r in sorted(part, key=lambda x: x.get("label") or ""):
            found, question = fn(r)
            lines += ["|-", f"| {{{{Q|{r['item'][1:]}}}}} || {found} || {question} || {gnd(r)} || "
                            f"[https://orcid.org/{r['orcid']} {r['orcid']}]"]
        lines.append("|}")
    return "\n".join(lines) + "\n"


def main():
    data = json.loads((WORK / "orcid_wrong.json").read_text(encoding="utf-8"))
    page = (WORK / "orcid_wrong.wiki").read_text(encoding="utf-8")
    start = page.index("== Check first ==")
    end = page.find("\n== ", start + 5)
    page = page[:start] + section(data["rows"]) + (page[end + 1:] if end > 0 else "")
    (WORK / "orcid_wrong.wiki").write_text(page, encoding="utf-8")
    print("check-first rewritten:", sum(1 for r in data["rows"] if r.get("check")), "rows")


if __name__ == "__main__":
    main()
