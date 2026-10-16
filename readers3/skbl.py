"""Svenskt kvinnobiografiskt lexikon (SKBL, P4963) on skbl.se (CC BY 4.0; the database is Språkbanken's Karp): each
article has a JSON version, skbl.se/sv/artikel/<ID>.json, read one a second. Only its structured fields are kept, never
the article text: the life span (dates; town, parish and map pin of birth and death), the relatives (names, kind and
life dates, not linked to other articles) and the identifiers it gives (ISNI, VIAF, LIBRIS).

All entries are women (P21). Places: the town SKBL names, the one Swedish town (or urban area) of that name, else by
sources3.town (Swedish towns or municipalities, else well-known cities anywhere), and only when SKBL's map pin lies
near the item found. Relatives: by their full name and life years with exactly one
human on Wikidata (dictionaries.person_named); parents also by the relative's sex there."""
import json
import math
import re
import urllib.parse

import dictionaries as d
import sources3 as s

WORK = "skbl"
SWEDEN = "Q34"
LANGS = ("sv", "en")
KEEP = ("url", "name", "lifespan", "relation", "id", "viaf_id", "libris_uri", "lexiconName", "skbl_status")
NEAR_KM = 25                      # SKBL's pin and the item's coordinates: the same town
SWEDISH_TOWNS = (("Q3957", "Q12813115", "Q515", "Q1549591", "Q5119"),)
# town, urban area in Sweden, city, big city, capital: Vimmerby the town, not Vimmerby Municipality, which some labels
# also call 'Vimmerby' (sources3.town finds the two comparable and takes neither)
RELATIONS = {"Far": ("P22", "father"), "Mor": ("P25", "mother"), "Make": ("P26", "spouse"), "Maka": ("P26", "spouse"),
             "Son": ("P40", "child"), "Dotter": ("P40", "child"), "Bror": ("P3373", "sibling"),
             "Syster": ("P3373", "sibling")}
SEX = {"P22": "Q6581097", "P25": "Q6581072"}


def candidates(n):
    return s.linked("P4963", n)


def entry(ident):
    url = f"https://skbl.se/sv/artikel/{urllib.parse.quote(ident)}.json"
    text = s.cached(WORK, ident + ".json", lambda: json.dumps(
        {k: v for k, v in s.polite_get(url, gap=1.0, as_json=True).items() if k in KEEP}, ensure_ascii=False))
    return json.loads(text)


def ymd(when):
    """{'date': '1862-01-26', 'comment': '1862-01-26'} -> (1862, 1, 26); a year alone -> (1862, None, None); None when
    the comment says more than the date ('omkring 1700') or the date is not plain."""
    when = when or {}
    date, comment = str(when.get("date") or "").strip(), str(when.get("comment") or "").strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d\d)-(\d\d))?", date)
    if not m or (comment and comment != date):
        return None
    y, mo, day = int(m.group(1)), m.group(2) and int(m.group(2)), m.group(3) and int(m.group(3))
    if mo is not None and not (1 <= mo <= 12 and 1 <= day <= 31):
        return None
    return y, mo, day


def shown(when):
    when = when or {}
    return str(when.get("comment") or when.get("date") or "").strip()


def coordinates(q):
    rows = d.lookup(f"SELECT ?c WHERE {{ wd:{q} wdt:P625 ?c }}")
    out = []
    for r in rows:
        m = re.search(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", r[0] if r else "")
        if m:
            out.append((float(m.group(2)), float(m.group(1))))
    return out


def km(a, b):
    (la1, lo1), (la2, lo2) = [(math.radians(x), math.radians(y)) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def place(where, label, notes):
    """{'place': 'Umeå', 'parish': 'Umeå landsförsamling', 'pin': {...}} -> the town's item, or None (noted)."""
    name = d.clean(str((where or {}).get("place") or ""))
    if not name:
        return None
    if re.search(r"[,()\d/]|\b(?:nära|vid|utanför|troligen|eller)\b", name):
        notes.append(f"{label} '{name}' not matched to one place item")
        return None
    q = d.place(name, SWEDISH_TOWNS, LANGS, SWEDEN) or s.town(name, LANGS, SWEDEN, given=False)
    if not q:
        notes.append(f"{label} '{name}' not matched to one place item")
        return None
    pin = (where or {}).get("pin") or {}
    try:
        here = float(pin["lat"]), float(pin["lon"])
    except (KeyError, TypeError, ValueError):
        return q
    there = coordinates(q)
    if there and min(km(here, c) for c in there) > NEAR_KM:
        notes.append(f"{label} '{name}' matched {q}, but SKBL's map pin is {min(km(here, c) for c in there):.0f} km "
                     "away; not used")
        return None
    return q


def isni_valid(v):
    """ISO 7064 MOD 11-2, as ISNI uses; '0000000000000000' (SKBL's placeholder) fails."""
    if not re.fullmatch(r"0{7}\d{8}[\dX]", v):
        return False
    total = 0
    for ch in v[:15]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    return v[15] == ("X" if check == 10 else str(check))


def relative_name(r):
    """'/Jenny/', 'Hamrin, gift Tegström' -> ['Jenny Hamrin', 'Jenny Tegström']: the names to look for."""
    first = d.clean(re.sub(r"/", "", str(r.get("firstname") or "")))
    last = d.clean(str(r.get("lastname") or ""))
    lasts = [p.strip() for p in re.split(r",\s*(?:gift|född|f\.|g\.|omgift|senare)\s+", last) if p.strip()]
    return [f"{first} {l}" for l in lasts if first and l and not re.search(r"[\d()?]", first + l)]


def relatives(rels, notes):
    facts = []
    for r in rels or []:
        kind = str(r.get("type") or "").strip()
        if kind not in RELATIONS:
            continue
        pid, label = RELATIONS[kind]
        born, died = ymd(r.get("born")), ymd(r.get("death"))
        by, dy = born and born[0], died and died[0]
        if not by and not dy:
            continue
        found = {q for name in relative_name(r) for q in [d.person_named(name, by, dy)] if q}
        if len(found) != 1:
            continue
        q = found.pop()
        if pid in SEX and d.sex(q) != SEX[pid]:
            notes.append(f"{label} {' / '.join(relative_name(r))} matched {q}, whose sex on Wikidata differs; not used")
            continue
        quals = None
        start = ymd(r.get("from")) if pid == "P26" else None
        if start:                                     # the year of marriage; a day only from 1753 (Sweden's calendar)
            quals = {"P580": [d.time_value(*start) if start[0] >= 1753 else d.time_value(start[0])]}
        facts.append(d.fact(pid, d.item_value(q), label, quals))
    return facts


def read(ident):
    e = entry(ident)
    name = e.get("name") or {}
    title = d.clean(re.sub(r"/", "", f"{name.get('firstname', '')} {name.get('lastname', '')}")) or ident
    facts, notes = [d.fact("P21", d.item_value("Q6581072"), "sex or gender")], []   # a lexicon of women
    life = e.get("lifespan") or {}
    for key, pid, label in (("from", "P569", "date of birth"), ("to", "P570", "date of death")):
        when = (life.get(key) or {}).get("date")
        if not shown(when):
            continue
        v = ymd(when)
        if v:
            facts.append(s.date_fact(pid, label, *v, country=SWEDEN))
        else:
            notes.append(f"{label} '{shown(when)}' not used: approximate or unclear")
    for key, pid, label in (("from", "P19", "place of birth"), ("to", "P20", "place of death")):
        q = place((life.get(key) or {}).get("place"), label, notes)
        if q:
            facts.append(d.fact(pid, d.item_value(q), label))
    facts += relatives(e.get("relation"), notes)
    for i in e.get("id") or []:
        v = str(i.get("id") or "").strip().upper()
        if str(i.get("type") or "").upper() == "ISNI" and isni_valid(v):
            facts.append(d.fact("P213", d.string_value(v), "ISNI"))
    vid = str(e.get("viaf_id") or "").strip()
    if re.fullmatch(r"[1-9]\d{1,21}", vid):
        current = d.viaf_current(vid)
        if current:
            facts.append(d.fact("P214", d.string_value(current), "VIAF ID"))
        else:
            notes.append(f"VIAF ID {vid} could not be checked against VIAF; not copied")
    for lib in e.get("libris_uri") or []:
        lib = str(lib).strip()
        if re.fullmatch(r"[a-z\d]{12,}", lib):
            facts.append(d.fact("P5587", d.string_value(lib), "Libris-URI"))
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
