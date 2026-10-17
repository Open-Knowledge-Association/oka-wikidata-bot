"""Wien Geschichte Wiki (P7842, the page ID), the City of Vienna's history wiki: each entry's {{Person}} template,
read through the wiki's MediaWiki API (robots.txt bars /api/ and some index.php actions, not api.php), one page every
3 seconds. Only the template's own fields are used, never the text.

Facts: sex ('Geschlecht'); dates of birth and death as '28.01.1855' (exact dates only, read with the Deutsche
Biographie's German helpers; 'Sterbedatum unbekannt' is respected); places of birth and death by the GND number the
template gives for the place ('GND Geburtsort', 'GND Sterbeort') when exactly one item has it, else by name with the
German helpers; occupations through the checked mapping of readers3/ndb.py; the person's GND number. Relatives are
named by page title only, so they are not used."""
import json
import re

import dictionaries as d
import sources3 as s
from readers3 import ndb as n

WORK = "wgw"
API = "https://www.geschichtewiki.wien.gv.at/api.php"
AUSTRIA = "Q40"
SEX = {"weiblich": n.FEMALE, "männlich": n.MALE}
EVENTS = (("Geburtsdatum", "Geburtsort", "GND Geburtsort", ("P569", "date of birth"), ("P19", "place of birth")),
          ("Sterbedatum", "Sterbeort", "GND Sterbeort", ("P570", "date of death"), ("P20", "place of death")))


def candidates(n_):
    return s.linked("P7842", n_)


def person_params(wikitext):
    """The top-level fields of the page's {{Person}} template (nested templates such as {{Familie}} are skipped)."""
    m = re.search(r"\{\{\s*Person\s*\n", wikitext)
    if not m:
        return {}
    params, depth = {}, 1
    for line in wikitext[m.end():].split("\n"):
        if depth == 1 and line.startswith("|") and "=" in line:
            key, value = line[1:].split("=", 1)
            if "{{" not in value:
                params[key.strip()] = value.strip()
        depth += line.count("{{") - line.count("}}")
        if depth <= 0:
            break
    return params


def page(curid):
    def fetch():
        r = json.loads(s.polite_get(f"{API}?action=query&pageids={curid}&prop=revisions&rvprop=content|ids"
                                    "&rvslots=main&format=json&formatversion=2", gap=3.0))
        p = r["query"]["pages"][0]
        rev = (p.get("revisions") or [{}])[0]
        text = rev.get("slots", {}).get("main", {}).get("content", "")
        return json.dumps({"pageid": p.get("pageid"), "title": p.get("title"), "revid": rev.get("revid"),
                           "person": person_params(text)}, ensure_ascii=False)
    return json.loads(s.cached(WORK, f"{curid}.params.json", fetch))


def place(name, gnd, label, notes):
    if gnd:
        q = s.item_with("P227", gnd.strip())
        if q:
            return q
    return n.german_place(name, label, notes, home=AUSTRIA) if name else None


def read(ident):
    p = page(ident)
    t = p.get("person") or {}
    if not t:
        raise LookupError(f"Wien Geschichte Wiki {ident}: no person template")
    title = t.get("Personenname") or p.get("title") or str(ident)
    facts, notes = [], []
    if t.get("Geschlecht") in SEX:
        facts.append(d.fact("P21", d.item_value(SEX[t["Geschlecht"]]), "sex or gender"))
    for date_key, place_key, gnd_key, (pid_date, label_date), (pid_place, label_place) in EVENTS:
        unknown = date_key == "Sterbedatum" and t.get("Sterbedatum unbekannt", "").lower() == "ja"
        if t.get(date_key) and not unknown:
            when = re.sub(r"^(\d{4})/(\d{2})/(\d{2})$", r"\3.\2.\1", t[date_key].strip())   # '1952/02/15' too
            f = n.german_date(when, pid_date, label_date, notes, country=AUSTRIA)
            if f:
                facts.append(f)
        if t.get(place_key) or t.get(gnd_key):
            q = place(t.get(place_key), t.get(gnd_key), label_place, notes)
            if q:
                facts.append(d.fact(pid_place, d.item_value(q), label_place))
    if t.get("Beruf"):
        facts += n.occupation_facts(re.split(r"[;,]\s*", t["Beruf"]), notes)
    f = n.gnd_fact(t.get("GND"), notes)
    if f:
        facts.append(f)
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
