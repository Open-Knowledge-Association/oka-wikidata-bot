"""Hessische Biografie (P13226) on LAGIS Hessen, read through the JSON API its pages are built from (one record every
3 seconds; robots.txt bars only the map tools). Of each record only the head fields are used ('geboren' and
'gestorben' as '25.11.1616 Herzberg', 'Beruf', 'Konfession', the GND number) and the family section; the biography,
career and source lists are not kept.

Dates and places are read with the Deutsche Biographie's German helpers (readers3/ndb.py): exact dates only, places
by name with the same rules ('Rhena (heute Korbach)', 'Breslau heute Wrocław (Polen)'; 'X bei Y' and doubtful names
are left with a note). Relatives (father, mother, spouse; children and siblings from the list of relatives) only when
the record links them to their own Hessische Biografie entry, that entry's ID is on exactly one item, and the years
fit the relation."""
import html
import json
import re

import dictionaries as d
import sources3 as s
from readers3 import ndb as n

WORK = "hessbio"
API = "https://www.lagis-hessen.de/de/api/idrecng/sn/hessische-biografie/id/{}"
KEEP_SECTIONS = ("Familie", "Andere Namen", "Nachnutzung")
HEAD = (("geboren", ("P569", "date of birth"), ("P19", "place of birth")),
        ("gestorben", ("P570", "date of death"), ("P20", "place of death")))
FAMILY = {"Vater": "father", "Mutter": "mother", "Partner": "spouse"}
KIN = {"Sohn": "child", "Tochter": "child", "Bruder": "sibling", "Schwester": "sibling", "Halbbruder": "sibling",
       "Halbschwester": "sibling", "Vater": "father", "Mutter": "mother", "Ehemann": "spouse", "Ehefrau": "spouse"}
LINKED = re.compile(r'\s*<a href="[^"]*/alle-eintraege/(\d+)[^"]*">([^<]+)</a>\s*(?:<([^>]+)>)?\s*,?\s*(\d{3,4})?')
# only a link at the very start of a field or list item names the relative: later links in the same text are
# other people ('Arnold, Mathilde, Tochter des <a>Arnold, Wilhelm</a>' links the partner's father)


def candidates(n_):
    return s.linked("P13226", n_)


def record(ident):
    """The entry's record from the API the LAGIS pages are built from: its head fields (dates and places, occupation,
    confession, GND) and the family section; the biography, career and source lists are not kept."""
    def fetch():
        r = s.polite_get(API.format(ident), gap=3.0, as_json=True)
        keep = {k: r.get(k) for k in ("id", "title", "subTitle", "headFields")}
        keep["sections"] = {k: (r.get("recordData") or {}).get(k, {}).get("cols") for k in KEEP_SECTIONS}
        return json.dumps(keep, ensure_ascii=False)
    return json.loads(s.cached(WORK, f"{ident}.json", fetch))


def split_head(raw):
    """'25.11.1616 Herzberg' -> ('25.11.1616', 'Herzberg'); '1943 Halle an der Saale' -> ('1943', ...); a value that
    does not start with a date -> (None, the whole text)."""
    m = re.match(r"\s*((?:\d{1,2}\.\s*\d{1,2}\.\s*)?\d{3,4})\b\s*(.*)$", raw or "")
    return (m.group(1), m.group(2).strip(" ,;")) if m else (None, (raw or "").strip())


def relatives(family, born, notes):
    """Relation facts for the relatives the record links to their own entries."""
    facts, seen = [], set()
    linked = []
    for key, kind in FAMILY.items():
        m = LINKED.match(html.unescape(family.get(key) or ""))
        if m:
            linked.append((kind, m.group(1), d.clean(m.group(2)), m.group(4)))
    for item in re.findall(r"<li>(.*?)</li>", html.unescape(family.get("Verwandte") or ""), re.S):
        m = LINKED.match(item)
        kind = KIN.get((m.group(3) or "").strip()) if m else None
        if kind:
            linked.append((kind, m.group(1), d.clean(m.group(2)), m.group(4)))
    for kind, rid, name, year in linked:
        if (kind, rid) in seen:
            continue
        seen.add((kind, rid))
        if not n.years_fit(kind, born, int(year) if year else None):
            notes.append(f"{kind} '{name}' ({year}): the years do not fit; not used")
            continue
        q = s.item_with("P13226", rid)
        if not q:
            notes.append(f"{kind} '{name}': entry {rid} is on no single item; not used")
            continue
        f = n.relative_fact(kind, q, notes, name)
        if f:
            facts.append(f)
    return facts


def read(ident):
    r = record(ident)
    title = d.clean(r.get("title") or str(ident))
    head = {h["label"]: [str(sp.get("value")) for sp in h.get("specs") or [] if sp.get("value") is not None]
            for h in r.get("headFields") or []}
    facts, notes, born = [], [], None
    for key, (pid_date, label_date), (pid_place, label_place) in HEAD:
        values = head.get(key) or []
        if len(values) != 1:
            if values:
                notes.append(f"{label_date}: several values; not used")
            continue
        when, where = split_head(values[0])
        if when:
            f = n.german_date(when, pid_date, label_date, notes)
            if f:
                facts.append(f)
                if pid_date == "P569":
                    born = int(f["value"]["value"]["time"][1:5])
        if where:
            q = n.german_place(where, label_place, notes)
            if q:
                facts.append(d.fact(pid_place, d.item_value(q), label_place))
    for words in head.get("Beruf") or []:
        facts += n.occupation_facts(re.split(r",\s*", words), notes)
    for faith in head.get("Konfession") or []:
        faith = d.clean(faith)
        if faith in n.FAITHS:
            facts.append(d.fact("P140", d.item_value(n.FAITHS[faith]), "religion"))
        else:
            notes.append(f"religion '{faith}' not used: not mapped")
    for gnd in head.get("GND-Explorer") or []:
        f = n.gnd_fact(gnd, notes)
        if f:
            facts.append(f)
    facts += relatives((r.get("sections") or {}).get("Familie") or {}, born, notes)
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
