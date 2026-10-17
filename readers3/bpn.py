"""Biografisch Portaal van Nederland (P651), through the portal's documented API
(http://www.biografischportaal.nl/about/bioport-api-documentation: "We encourage re-use and sharing of the data set").
/persoon/json/<ID> gives the portal's own consolidated record of the person: names, sex, and the events of birth,
baptism, death and burial (date and place), with the list of source dictionaries it was built from. Only that record
is read, over plain http (the https certificate has expired), one request every 3 seconds; robots.txt bars bots from
the site's pages, which the bot never reads. The sources' biographies and their texts are not read: the API does not
give each source's own dates, so conflicts between them are not visible; the portal's record is what the entry states.
"""
import functools
import json
import re

import dictionaries as d
import sources3 as s

WORK = "bpn"
API = "http://www.biografischportaal.nl/persoon/json/{}"
GAP = 3.0                                       # seconds between API requests
NL = ("Q55", "Q29999")                          # towns in the Netherlands name it, or the Kingdom, in P17
LANGS = ("nl", "en")
CALENDAR = "Q55"                                # day-exact dates before 1701 are calendar-unclear (sources3.GREGORIAN_FROM)
SEX = {"1": "Q6581097", "2": "Q6581072"}        # the API's codes: 1 male, 2 female
PROVINCES = {"Noord-Holland": "Q701", "Zuid-Holland": "Q694", "Utrecht": "Q776", "Gelderland": "Q775",   # checked
             "Overijssel": "Q773", "Drenthe": "Q772", "Groningen": "Q752", "Friesland": "Q770", "Fryslân": "Q770",
             "Flevoland": "Q707", "Zeeland": "Q705", "Noord-Brabant": "Q1101", "Limburg": "Q1093"}
DOUBT = re.compile(r"\?|\b(?:ca|circa|omstreeks|omtrent|rond|vóór|voor|na|tussen|waarschijnlijk|vermoedelijk)\b\.?", re.I)
NOT_A_TOWN = re.compile(r"\b(?:bij|nabij|onder|omgeving|regio|streek|gemeente|provincie|eiland|op zee|aan boord)\b"
                        r"|\d|/", re.I)


def candidates(n):
    return s.linked("P651", n)


def record(ident):
    """The consolidated record, as kept in the cache: no texts, no illustrations, only the sources' names."""
    def fetch():
        got = json.loads(s.polite_get(API.format(ident), gap=GAP))     # an error page fails here and is not cached
        return json.dumps({
            "bioport_id": got.get("bioport_id"), "namen": got.get("namen") or [], "sex": got.get("sex"),
            "event": [{k: (e.get(k).strip() if isinstance(e.get(k), str) else e.get(k))
                       for k in ("type", "when", "place", "text")} for e in got.get("event") or []],
            "sources": [b.get("source_id") for b in got.get("biographies") or []]}, ensure_ascii=False)
    return json.loads(s.cached(WORK, f"{ident}.json", fetch))


def title(names):
    """'Doeleman, Johan' reads 'Johan Doeleman' ('Stadt, E.G. van de': 'E.G. van de Stadt')."""
    name = " ".join((names or [""])[0].split())
    m = re.fullmatch(r"([^,]+),\s*(.+)", name)
    return f"{m.group(2)} {m.group(1)}" if m else name


def when(event, label, notes):
    """(year, month, day) of the event's date: '1880-10-25', '1617-08' (read as its year) or '1683'; None when the
    date is missing, in another form, or marked uncertain in the event's text."""
    raw, text = (event.get("when") or "").strip(), d.clean(event.get("text") or "")
    if not raw:
        return None
    m = re.fullmatch(r"(\d{3,4})(?:-(\d\d)(?:-(\d\d))?)?", raw)
    if not m:
        notes.append(f"{label} '{raw}' not in a clear format")
        return None
    if DOUBT.search(text):
        notes.append(f"{label} {raw} not used: the portal marks it uncertain ('{text}')")
        return None
    y, mo, day = int(m.group(1)), m.group(2) and int(m.group(2)), m.group(3) and int(m.group(3))
    return (y, mo, day) if mo and day else (y, None, None)    # a month without a day: its year


@functools.lru_cache(maxsize=None)
def namesakes(name):
    """The settlements and municipalities of the Netherlands labelled with this name, with their classes."""
    lits = " ".join(f"{json.dumps(name, ensure_ascii=False)}@{lang}" for lang in LANGS + ("mul",))
    rows = d.lookup(f"SELECT DISTINCT ?i ?c WHERE {{ VALUES ?l {{ {lits} }} VALUES ?k {{ wd:Q55 wd:Q29999 }} "
                    "?i rdfs:label ?l ; wdt:P31 ?c ; wdt:P17 ?k . }")
    found = {}
    for r in rows:
        if len(r) == 2:
            found.setdefault(r[0], set()).add(r[1])
    return {q: cs for q, cs in found.items() if cs & d.settlement_classes()}


def only_one(name, q, within=None):
    """q, unless another place of the Netherlands (in `within`, if given) has the same name, neither lying in the
    other, and q is not the one city among them: 'Laren' is a municipality in North Holland and a village in
    Gelderland, and the portal does not say which (town() would take the far better known one); 'Leeuwarden' is the
    city, not the hamlet of that name. A town and its municipality are one place."""
    if not q:
        return None
    found = namesakes(d.place_name(name) or name)
    others = [o for o in found if o != q]
    if not others:
        return q
    d.place_containers([q, within] + others if within else [q] + others)
    rivals = [o for o in others if (not within or d.lies_in(o, within)) and not (d.lies_in(o, q) or d.lies_in(q, o))]
    city = lambda o: bool(found.get(o, set()) & set(d.CITY_CLASSES))
    return q if not rivals or (city(q) and not any(city(o) for o in rivals)) else None


def province_town(name, province):
    return only_one(name, s.town(name, LANGS, ("within", PROVINCES[province], ("Q55",))), PROVINCES[province])


def dutch_place(raw):
    """'Soest'; 'Hoorn (Noord-Holland)' (in that province); 'Badhoevedorp (Haarlemmermeer)' (in that municipality);
    'Rees, Duitsland' and 'Alto, Wisconsin, Verenigde Staten' (in the country or state named). Other descriptions
    ('Zaankant (regio)', 'Wirdum?', 'Mülheim a/d Rijn') and bare countries are left unmatched."""
    text = d.clean(raw)
    if not text or DOUBT.search(text) or NOT_A_TOWN.search(text):
        return None
    m = re.fullmatch(r"([^(),]+?)\s*\(([^()]+)\)", text)
    if m:
        name, within = m.group(1).strip(), m.group(2).strip()
        if within in PROVINCES:
            return province_town(name, within)
        region = s.region_named(within, LANGS)
        if region:
            return s.town(name, LANGS, region)
        container = only_one(within, s.town(within, LANGS, NL))       # a municipality of the Netherlands
        return only_one(name, s.town(name, LANGS, ("within", container, ("Q55",))), container) if container else None
    if "(" in text or ")" in text:
        return None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) > 3 or not all(parts):
        return None
    if len(parts) == 3:                                                 # town, state or region, country
        country, inner = s.region_named(parts[2], LANGS), s.region_named(parts[1], LANGS)
        if not (country and inner and inner[0] == "within" and set(inner[2]) & set(country[-1])):
            return None
        return s.town(parts[0], LANGS, inner)
    if len(parts) == 2:
        if parts[1] in PROVINCES:
            return province_town(parts[0], parts[1])
        region = s.region_named(parts[1], LANGS)
        return s.town(parts[0], LANGS, region) if region else None
    if s.region_named(parts[0], LANGS):                                 # 'Indonesië': a country, no town
        return None
    q = s.town(parts[0], LANGS, NL)                                     # a town of the Netherlands, if one
    if q or any(d._any_candidate.get((d.place_name(parts[0]), d.FOREIGN_PLACES[1:], None, NL, aliases))
                for aliases in (False, True)):                          # (several there: none, not a city abroad)
        return only_one(parts[0], q)
    return s.town(parts[0], LANGS, NL, given=False)                     # else a well-known city anywhere ('Brugge')


def read(ident):
    r = record(ident)
    if not r.get("bioport_id"):
        raise ValueError(f"{ident}: no person record in the API's answer")
    facts, notes = [], []
    name = title(r.get("namen"))
    if str(r["bioport_id"]).zfill(8) != ident.zfill(8):           # merged into another person of the portal
        return {"title": name or ident, "kind": "person", "facts": [],
                "notes": [f"the portal now gives this person as {r['bioport_id']}; not used"]}
    events = {}
    for e in r.get("event") or []:
        events.setdefault(e.get("type"), []).append(e)
    for kind in ("birth", "death", "baptism", "funeral"):
        if len([e for e in events.get(kind, []) if e.get("when") or e.get("place")]) > 1:
            notes.append(f"{kind}: several in the portal's record; not used")
            events[kind] = []
    event = lambda kind: (events.get(kind) or [{}])[0]
    born = when(event("birth"), "date of birth", notes)
    if born:
        facts.append(s.date_fact("P569", "date of birth", *born, country=CALENDAR))
    else:                                                    # a baptism date stands for the missing birth date
        baptised = when(event("baptism"), "date of baptism", notes)
        if baptised:
            facts.append(s.date_fact("P1636", "date of baptism", *baptised, country=CALENDAR))
    died = when(event("death"), "date of death", notes)
    if died:
        facts.append(s.date_fact("P570", "date of death", *died, country=CALENDAR))
    for kind, pid, label in (("birth", "P19", "place of birth"), ("death", "P20", "place of death"),
                             ("funeral", "P119", "place of burial")):
        raw = event(kind).get("place")
        if isinstance(raw, str) and raw.strip():
            q = dutch_place(raw)
            if q:
                facts.append(d.fact(pid, d.item_value(q), label))
            else:
                notes.append(f"{label} '{d.clean(raw)}' not matched to one place item")
    if str(r.get("sex")) in SEX:
        facts.append(d.fact("P21", d.item_value(SEX[str(r["sex"])]), "sex or gender"))
    return {"title": name or ident, "kind": "person", "facts": facts, "notes": notes}
