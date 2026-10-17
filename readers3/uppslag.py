"""Uppslagsverket Finland (P4854) on uppslagsverket.fi, Svenska folkskolans vänner (text CC BY-SA 4.0): HTML pages
only, read one every 3 seconds (the site has no robots.txt). Of each page only the heading, the subject words of its
info panel ('personer (individer)' marks a person) and the parenthesis after the entry's name are kept, never the
text: 'Nurmi, Paavo Johannes (f. 13/6 1897 Åbo, d. 2/10 1973 Hfrs), ...'.

Dates and places are taken only from a parenthesis in exactly that form: 'f.' and 'd.', each with a day and month
('13/6') or none, a year and a plain place name, Swedish as the work writes it, a few usual abbreviations spelled out
('Hfrs', Helsingfors). Anything else ('f. omkr. 1650', 'f. 1897?', 'd. på Kimito gård') is not used. A place is the
one town or municipality of that name in Finland, else a well-known city anywhere (sources3.town)."""
import html
import json
import re
import urllib.parse

import dictionaries as d
import sources3 as s

WORK = "uppslag"
URL = "https://uppslagsverket.fi/sv/sok/view-170045-{}"
FINLAND = "Q33"
LANGS = ("sv", "fi", "en")
GAP = 3.0
ABBREVIATIONS = {"Hfrs": "Helsingfors", "Hfors": "Helsingfors", "Sthlm": "Stockholm", "Sthm": "Stockholm",
                 "Tfrs": "Tammerfors", "Pburg": "Sankt Petersburg"}
CEDED = {"Viborg": "Q14657"}        # Vyborg (Viipuri), Finnish until 1944, now in Russia: not Viborg in Denmark
KINDS = (("personer (individer)", "person"), ("orter", "place"), ("kommuner", "place"), ("företag", "organisation"),
         ("föreningar", "organisation"), ("organisationer", "organisation"))
ONE_DATE = r"(?:(\d{1,2})/(\d{1,2})\s+)?(\d{4})(?:\s+([A-ZÅÄÖ][\w:.'-]*(?:[ -][A-ZÅÄÖ][\w:.'-]*)*))?"
LIFE = re.compile(rf"f\.\s+{ONE_DATE}(?:,\s+d\.\s+{ONE_DATE})?")


def candidates(n):
    return s.linked("P4854", n)


def page_facts(page):
    """What the reader keeps of a page: heading, subject words, and the parenthesis after the name."""
    text = lambda t: d.clean(html.unescape(t or ""))
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    lead = re.search(r'<div class="left-column">\s*<strong>(.*?)</strong>([^(<]{0,160})\(([^()]{0,200})\)', page, re.S)
    subj = re.search(r'Ämnesord\s*</div>\s*<div class="col-md-8">(.*?)</div>', page, re.S)
    words = [w.strip() for w in re.split(r",(?![^(]*\))", text(re.sub(r"<[^>]+>", " ", subj.group(1)))) if w.strip()] \
        if subj else []
    return {"title": text(h1.group(1)) if h1 else "", "name": text(lead.group(1)) if lead else "",
            "names_after": text(lead.group(2)) if lead else "", "life": text(lead.group(3)) if lead else "",
            "subjects": words}


def entry(ident):
    url = URL.format(urllib.parse.quote(ident))
    return json.loads(s.cached(WORK, ident + ".json",
                               lambda: json.dumps(page_facts(s.polite_get(url, gap=GAP)), ensure_ascii=False)))


def finnish_place(name, label, notes):
    name = ABBREVIATIONS.get(name, name)
    if name in CEDED:
        return CEDED[name]
    q = None if s.region_named(name, LANGS) else s.town(name, LANGS, FINLAND, given=False)
    if not q:
        notes.append(f"{label} '{name}' not matched to one place item")
    return q


def read(ident):
    e = entry(ident)
    title = e.get("title") or e.get("name") or ident
    kinds = {k for word, k in KINDS for subject in e.get("subjects", []) if subject.casefold() == word}
    if not kinds and re.match(r"f\.\s", e.get("life", "")):
        kinds = {"person"}            # no subject words, but the work's own life line ('f. 2/1 1893 Vasa, d. ...')
    if len(kinds) != 1:
        raise LookupError(f"Uppslagsverket {ident}: kind not clear from the subject words {e.get('subjects')}")
    kind = kinds.pop()
    facts, notes = [], []
    if kind != "person":
        return {"title": title, "kind": kind, "facts": facts, "notes": notes}
    life = e.get("life", "")
    m = LIFE.fullmatch(life) if e.get("name") and not re.search(r"[;<>]", e.get("names_after", "")) else None
    if not m:
        notes.append(f"life dates '({life})' not in the usual form; not used" if life else "no life dates found")
        return {"title": title, "kind": kind, "facts": facts, "notes": notes}
    groups = m.groups()
    for (day, mo, y, where), dpid, dlabel, ppid, plabel in (
            (groups[:4], "P569", "date of birth", "P19", "place of birth"),
            (groups[4:], "P570", "date of death", "P20", "place of death")):
        if not y:
            continue
        if mo and not (1 <= int(mo) <= 12 and 1 <= int(day) <= 31):
            notes.append(f"{dlabel} {day}/{mo} {y} not a valid date")
        else:
            facts.append(s.date_fact(dpid, dlabel, y, mo, day, FINLAND))
        if where:
            q = finnish_place(where, plabel, notes)
            if q:
                facts.append(d.fact(ppid, d.item_value(q), plabel))
    return {"title": title, "kind": kind, "facts": facts, "notes": notes}
