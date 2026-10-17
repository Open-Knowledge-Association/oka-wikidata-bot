"""The National Biography of Finland (Kansallisbiografia, P2180), read through BiographySampo's open SPARQL endpoint
(ldf.fi/nbf/sparql, Aalto University's SeCo and the Finnish Literature Society; CC BY 4.0), which holds the
National Biography's own data (source 'Kansallisbiografia'): each person's sex, birth and death (date and the place
name the entry gives, which BiographySampo links to a place of its own and often to Wikidata), and family relations
from the entry's family section, the relative linked to his or her own entry when there is one. Two queries per
entry, one a second; only these facts are kept, never the biography text.

Facts: dates as the entry gives them (day or year; approximate ones such as '1600-luvulla' or 'ennen 1355' are not
used; Finland's calendar, Gregorian from 1753); places by BiographySampo's Wikidata link when the entry's place name
is that place's name and the item is so named, and, for a place in Finland, when sources3.town finds the same one
item; parents, spouses and children only when the relative has an entry of his or her own whose ID is on exactly
one item (and the relative's name agrees with that entry). Events or relatives that BiographySampo took from Wikidata
or estimated are not used."""
import json
import re
import urllib.parse

import dictionaries as d
import sources3 as s

WORK = "kb"
ENDPOINT = "https://ldf.fi/nbf/sparql"
NBF = "http://ldf.fi/nbf/"
KB_SOURCE = NBF + "sources/source1"            # 'Kansallisbiografia', the National Biography itself
FINLAND = "Q33"
LANGS = ("fi", "sv", "en")
GENDER = {"mies": "Q6581097", "nainen": "Q6581072"}
RELATIONS = {"Father": ("P22", "father"), "Mother": ("P25", "mother"), "Parent": (None, "parent"),
             "Wife": ("P26", "spouse"), "Husband": ("P26", "spouse"), "Spouse": ("P26", "spouse"),
             "Son": ("P40", "child"), "Daughter": ("P40", "child"), "Child": ("P40", "child")}
MONTHS = "tammi|helmi|maalis|huhti|touko|kesä|heinä|elo|syys|loka|marras|joulu"
PREFIXES = """PREFIX nbf: <http://ldf.fi/nbf/> PREFIX dct: <http://purl.org/dc/terms/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#> PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/> PREFIX gvp: <http://vocab.getty.edu/ontology#>
PREFIX bioc: <http://ldf.fi/schema/bioc/> PREFIX schema: <http://schema.org/>
"""
PERSON = PREFIXES + """SELECT ?p ?name ?link ?sex ?kind ?src ?tl ?ts ?te ?pname ?pl ?pll ?plalt ?wd WHERE {
  ?p nbf:identifier ?id ; dct:source <%(src)s> ; foaf:focus ?actor . FILTER(STR(?id) = "%(id)s")
  OPTIONAL { ?p skos:prefLabel ?name } OPTIONAL { ?p schema:relatedLink ?link } OPTIONAL { ?actor nbf:sukupuoli ?sex }
  OPTIONAL {
    { ?evt crm:P98_brought_into_life ?actor . BIND("birth" AS ?kind) }
    UNION { ?evt crm:P100_was_death_of ?actor . BIND("death" AS ?kind) }
    OPTIONAL { ?evt dct:source ?src }
    OPTIONAL { ?evt nbf:time ?t . OPTIONAL { ?t skos:prefLabel ?tl } OPTIONAL { ?t gvp:estStart ?ts }
               OPTIONAL { ?t gvp:estEnd ?te } }
    OPTIONAL { ?evt nbf:placename ?pname }
    OPTIONAL { ?evt nbf:place ?pl . OPTIONAL { ?pl skos:prefLabel ?pll } OPTIONAL { ?pl skos:altLabel ?plalt }
               OPTIONAL { ?pl nbf:wikidata ?wd } }
  }
}"""
FAMILY = PREFIXES + """SELECT ?type ?rl ?rel ?relsrc ?relid ?relname WHERE {
  ?p nbf:identifier ?id ; dct:source <%(src)s> . FILTER(STR(?id) = "%(id)s")
  ?p bioc:has_family_relation ?r . ?r a ?type ; bioc:inheres_in ?rel .
  OPTIONAL { ?r skos:prefLabel ?rl } OPTIONAL { ?rel dct:source ?relsrc } OPTIONAL { ?rel nbf:identifier ?relid }
  OPTIONAL { ?rel skos:prefLabel ?relname }
}"""


def candidates(n):
    return s.linked("P2180", n)


def ask(query):
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    got = json.loads(s.polite_get(url, gap=1.0))
    return [{k: v["value"] for k, v in row.items()} for row in got["results"]["bindings"]]


def entry(ident):
    text = s.cached(WORK, f"{ident}.json", lambda: json.dumps(
        {"person": ask(PERSON % {"src": KB_SOURCE, "id": ident}),
         "family": ask(FAMILY % {"src": KB_SOURCE, "id": ident})}, ensure_ascii=False))
    return json.loads(text)


def kb_date(rows, label, notes):
    """The date of one event, from its time span's label ('13.6.1897', '1973', 'toukokuussa 1731': a year), checked
    against the span's start and end; None (noted) for anything approximate ('1600-luvulla', 'ennen 1355')."""
    spans = {(r.get("tl", ""), r.get("ts", ""), r.get("te", "")) for r in rows if r.get("tl")}
    if not spans:
        return None
    if len(spans) > 1:
        notes.append(f"{label}: several dates ({', '.join(sorted(t for t, _, _ in spans))}); not used")
        return None
    tl, ts, te = spans.pop()
    text = re.sub(r"\s+", " ", tl).strip()
    m = re.fullmatch(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", text)
    if m:
        day, mo, y = map(int, m.groups())
        if ts[:10] == te[:10] == f"{y:04d}-{mo:02d}-{day:02d}":
            return y, mo, day
    m = re.fullmatch(r"(\d{4})", text) or re.fullmatch(rf"(?:{MONTHS})kuu(?:ssa)? (\d{{4}})", text)
    if m and ts[:4] == te[:4] == m.group(1):
        return int(m.group(1)), None, None
    notes.append(f"{label} '{text}' not used: approximate or unclear")
    return None


def place_check(q, name):
    """(the item is so named in Finnish, Swedish or English, the countries it lies in)."""
    lits = " ".join(f"{json.dumps(name, ensure_ascii=False)}@{lang}" for lang in LANGS + ("mul",))
    rows = d.lookup(f"SELECT ?l ?k WHERE {{ {{ VALUES ?l {{ {lits} }} wd:{q} rdfs:label|skos:altLabel ?l }} UNION "
                    f"{{ wd:{q} wdt:P17 ?k }} }}")
    return any(r and r[0] for r in rows), {r[1] for r in rows if len(r) > 1 and r[1]}


def kb_place(rows, label, notes):
    """The entry's place name ('Turku', 'Astoria, Oregon', 'Viipurin maalaiskunta'), BiographySampo's place for it
    and that place's Wikidata link: the item, when the name is the place's own (not 'Viipurin maalaiskunta' linked to
    Viipuri), the item is so named, and sources3.town agrees (for a place in Finland); or sources3.town alone."""
    names = {r["pname"] for r in rows if r.get("pname")} or {r["pll"] for r in rows if r.get("pll")}
    if not names:
        return None
    if len(names) > 1:
        notes.append(f"{label}: several places ({', '.join(sorted(names))}); not used")
        return None
    raw = names.pop()
    labels = {r[k].casefold() for r in rows for k in ("pll", "plalt") if r.get(k)}
    wds = {r["wd"].rsplit("/", 1)[-1] for r in rows if r.get("wd", "").startswith("http://www.wikidata.org/entity/Q")}
    name, _, within = (p.strip() for p in raw.partition(","))
    region = (FINLAND, False)
    if within:                                      # 'Astoria, Oregon', 'Edinburgh, Skotlanti': a town in a region
        named = s.region_named(within, LANGS)
        if not named:                               # 'Pyhäjärvi, Uudenmaan lääni', 'Sund, Ahvenanmaa': not used
            notes.append(f"{label} '{raw}' not matched to one place item")
            return None
        region = (named, True)
    if s.region_named(name, LANGS):                 # 'Ruotsi', 'Karjala': a country or region, not a town
        notes.append(f"{label} '{raw}' is a country or region; not used")
        return None
    linked = next(iter(wds)) if len(wds) == 1 and name.casefold() in labels else None
    found = s.town(name, LANGS, region[0], given=region[1])
    if linked and found and linked != found:
        notes.append(f"{label} '{raw}': BiographySampo links {linked}, the name gives {found}; not used")
        return None
    if linked and not found:
        so_named, countries = place_check(linked, name)
        if not so_named or FINLAND in countries or not countries:    # in Finland, town() must find it too
            notes.append(f"{label} '{raw}' not matched to one place item")
            return None
        found = linked
    if not found:
        notes.append(f"{label} '{raw}' not matched to one place item")
    return found


def person_title(name):
    """'Nurmi, Paavo (1897 - 1973)' -> 'Paavo Nurmi'."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name or "").strip()
    last, _, first = name.partition(",")
    return f"{first.strip()} {last.strip()}".strip()


def same_person(relation_label, entry_name):
    """'Isä Karl Gustaf Ehrström' and 'Ehrström, Karl Gustaf (1822 - 1886)': the relative's surname and first given
    name are in the family section's wording."""
    last, _, first = re.sub(r"\s*\([^)]*\)\s*$", "", entry_name or "").partition(",")
    words = set(re.findall(r"[\w'-]+", (relation_label or "").casefold()))
    first = first.split()
    return bool(last.strip() and first) and all(w.casefold() in words for w in last.split()[-1:] + first[:1])


def relatives(rows, notes):
    facts, seen = [], set()
    for r in rows:
        kind = r.get("type", "").rsplit("/", 1)[-1]
        if kind not in RELATIONS or r.get("relsrc") != KB_SOURCE or not r.get("relid") or r["rel"] in seen:
            continue                                # relatives without an entry of their own are not used
        seen.add(r["rel"])
        pid, label = RELATIONS[kind]
        if not same_person(r.get("rl"), r.get("relname")):
            notes.append(f"{label} '{d.clean(r.get('rl', ''))}' linked to the entry of {r.get('relname')}; not used")
            continue
        q = s.item_with("P2180", r["relid"])
        if not q:
            notes.append(f"{label} {person_title(r.get('relname'))}: KB ID {r['relid']} not on exactly one item")
            continue
        if pid is None:                             # 'Parent': father or mother by the relative's sex on Wikidata
            pid, label = {"Q6581097": ("P22", "father"), "Q6581072": ("P25", "mother")}.get(d.sex(q), (None, None))
            if not pid:
                continue
        facts.append(d.fact(pid, d.item_value(q), label))
    return facts


def read(ident):
    e = entry(ident)
    rows = e["person"]
    people = {r["p"] for r in rows}
    if len(people) != 1:
        raise LookupError(f"KB {ident}: {len(people)} people in BiographySampo")
    link = {r.get("link", "") for r in rows} - {""}
    if link and not any(re.search(rf"/henkilo/{re.escape(str(ident))}/?$", l) for l in link):
        raise LookupError(f"KB {ident}: BiographySampo's person links to {sorted(link)}")
    title = person_title(rows[0].get("name")) or str(ident)
    facts, notes = [], []
    sexes = {r.get("sex") for r in rows} - {None}
    if len(sexes) == 1 and next(iter(sexes)) in GENDER:
        facts.append(d.fact("P21", d.item_value(GENDER[next(iter(sexes))]), "sex or gender"))
    for kind, dpid, dlabel, ppid, plabel in (("birth", "P569", "date of birth", "P19", "place of birth"),
                                             ("death", "P570", "date of death", "P20", "place of death")):
        ev = [r for r in rows if r.get("kind") == kind]
        if not ev:
            continue
        if any(r.get("src") != KB_SOURCE for r in ev):
            notes.append(f"{kind}: BiographySampo has it from another source than the National Biography; not used")
            continue
        when = kb_date(ev, dlabel, notes)
        if when:
            facts.append(s.date_fact(dpid, dlabel, *when, country=FINLAND))
        q = kb_place(ev, plabel, notes)
        if q:
            facts.append(d.fact(ppid, d.item_value(q), plabel))
    facts += relatives(e["family"], notes)
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
