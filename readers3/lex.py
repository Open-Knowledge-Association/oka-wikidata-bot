"""Lex (Den Store Danske, P8313) on lex.dk, which runs on the same platform as snl.no: each article has a JSON
version whose metadata is marked free to reuse ("fri"), with the same fields as SNL's (dates and places of birth and
death, sex, other names). Only that metadata is read, one request a second; the article text is never kept.
Dansk kvindebiografisk leksikon (dkl.py) is the same, on its own lex.dk host."""
import json
import re
import urllib.parse

import dictionaries as d
import sources3 as s

WORK, HOST = "lex", "lex.dk"
DENMARK = ("Q35", "Q756617")                    # towns in Denmark name Denmark or the Kingdom of Denmark in P17
LANGS = ("da", "en")
KEEP = ("title", "metadata_license_name", "metadata")


def candidates(n):
    return s.linked("P8313", n)


def entry(slug, work=WORK, host=HOST):
    url = f"https://{host}/{urllib.parse.quote(slug)}.json"
    text = s.cached(work, slug + ".json", lambda: json.dumps(
        {k: v for k, v in s.polite_get(url, gap=1.0, as_json=True).items() if k in KEEP}, ensure_ascii=False))
    return json.loads(text)


def danish_place(raw):
    """'København', 'Paris, Frankrig', 'London, England', 'Venedig, Italien': a town of the country or region named,
    else of Denmark, else a well-known city anywhere. Longer descriptions ('Løkkeby ved Tullebølle på Langeland',
    'Salona i Dalmatien, i dag Solin i Kroatien') and bare countries are left unmatched."""
    text = d.clean(raw)
    parts = [p.strip() for p in text.split(",")]
    if len(parts) > 2 or re.search(r"\b(?:ved|på|i dag|nær|syd for|nord for|øst for|vest for)\b|\(|\d", text) \
            or re.search(r"\si\s", parts[0]):
        return None
    if len(parts) == 2:
        region = s.region_named(parts[1], LANGS)
        return s.town(parts[0], LANGS, region) if region else None
    if s.region_named(parts[0], LANGS):                  # 'Frankrig': a country, no town
        return None
    return s.town(parts[0], LANGS, DENMARK, given=False)


def read(slug, work=WORK, host=HOST):
    e = entry(slug, work, host)
    facts, notes = [], []
    if e.get("metadata_license_name") != "fri":
        return {"title": e.get("title", slug), "kind": "person", "facts": [], "notes": ["metadata not marked free to reuse"]}
    m = e.get("metadata") or {}
    aliases, _ = d.other_names(m.get("alternative_form"), "person")
    facts += [d.fact("ALIAS", {"language": "da", "value": a}, "other name") for a in aliases]
    for key, pid, label in (("birth_date", "P569", "date of birth"), ("death_date", "P570", "date of death")):
        if pid == "P570" and d.doubtful_death(m, notes):     # a birth date filed as death_date (Linda Woolverton)
            continue
        v = d.snl_date(m.get(key), label, notes)       # day-exact dates before 1700 (Denmark's calendar change) not used
        if v:
            facts.append(d.fact(pid, v, label))
    for key, pid, label in (("birthplace", "P19", "place of birth"), ("place_of_death", "P20", "place of death"),
                            ("place_of_burial", "P119", "place of burial")):
        if (m.get(key) or "").strip():
            q = danish_place(m[key])
            if q:
                facts.append(d.fact(pid, d.item_value(q), label))
            else:
                notes.append(f"{label} '{d.clean(m[key])}' not matched to one place item")
    if m.get("gender") in d.GENDER:
        facts.append(d.fact("P21", d.item_value(d.GENDER[m["gender"]]), "sex or gender"))
    return {"title": e.get("title", slug), "kind": "person", "facts": facts, "notes": notes}
