"""Slovenska biografija (P1254), the biographical lexicon of the Slovenian Academy of Sciences and Arts (SAZU) and
ZRC SAZU at slovenska-biografija.si. Each person's page carries the entry's own TEI record (<person>: sex, the work's
occupation codes, birth and death with date, place and the place's coordinates) and the list of related people
('Povezane osebe'), those with an entry of their own linked to it. One page every 3 seconds under the bot's name
(robots.txt bars one named crawler only); of each page only the record and that list are read, never the texts.

Facts:
- sex (the record's TEI code: 1 male, 2 female);
- dates of birth and death from the record's machine-readable date ('when'), only when the date the page shows says
  the same and neither is marked uncertain ('notBefore'/'notAfter', 'cert') nor unverified (source '#nepreverjeno':
  taken from Wikipedia and the like); the calendar is that of the country the record names for the place;
- places of birth and death: the one settlement so named (in Slovenian, or in the language of the other name the
  record gives) in the present-day country the record names, lying within 10 km of the coordinates the record gives
  for the place (several such, none, a town's district or an unknown place: not used, with a note);
- occupations through a mapping of the work's occupation codes to items checked on Wikidata (others are noted);
- father, mother, spouses, siblings and children only when the list links the relative to his or her own entry,
  whose ID is on exactly one item; 'starši' (parents) is father or mother by the relative's sex on Wikidata, and the
  relative's year of birth on Wikidata must fit the relation."""
import html
import json
import re

import dictionaries as d
import sources3 as s
from readers3 import ndb as n
from readers3 import oebl

WORK = "sbi"
URL = "https://www.slovenska-biografija.si/oseba/sbi{}/"
GAP = 3.0
SLOVENIA = "Q215"
RADIUS = 10.0                                   # km between the record's coordinates and the place item's
SEX = {"1": n.MALE, "2": n.FEMALE}
# present-day countries as the records name them (Slovenian) -> item; others are looked up by their Slovenian label
COUNTRIES = {"Slovenija": "Q215", "Italija": "Q38", "Avstrija": "Q40", "Hrvaška": "Q224", "Nemčija": "Q183",
             "Francija": "Q142", "Češka": "Q213", "Madžarska": "Q28", "Srbija": "Q403", "Švica": "Q39",
             "Slovaška": "Q214", "Poljska": "Q36", "Bosna in Hercegovina": "Q225", "Črna gora": "Q236"}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6, "jul": 7, "avg": 8, "sep": 9, "okt": 10,
          "nov": 11, "dec": 12}
UNVERIFIED = {"#nepreverjeno"}
# the work's occupation codes -> (item, its English label, checked on Wikidata 2026-10-17; the Slovenian label is
# the code's word unless noted). 'duhovnik' is left out: Q42603 (priest) beside the Catholic priest most items have
# would only repeat it less precisely; 'kanonik' and the like are offices, not occupations.
OCCUPATIONS = {
    "politik": ("Q82955", "politician"), "slikar": ("Q1028181", "painter"), "arhitekt": ("Q42973", "architect"),
    "casnikar": ("Q1930187", "journalist"),                     # časnikar, the older word for novinar
    "novinar": ("Q1930187", "journalist"), "urednik": ("Q1607826", "editor"), "prevajalec": ("Q333634", "translator"),
    "pisatelj": ("Q36180", "writer"), "pesnik": ("Q49757", "poet"), "teolog": ("Q1234713", "theologian"),
    "filozof": ("Q4964182", "philosopher"), "matematik": ("Q170790", "mathematician"),
    "jezikoslovec": ("Q14467526", "linguist"), "zgodovinar": ("Q201788", "historian"),
    "zdravnik": ("Q39631", "physician"), "ucitelj": ("Q37226", "teacher"), "solnik": ("Q1459149", "schoolmaster"),
    "literarni-zgodovinar": ("Q13570226", "literary historian"), "naravoslovec": ("Q18805", "naturalist"),
    "tiskar": ("Q175151", "printer"), "zaloznik": ("Q2516866", "publisher"),
    "industrialec": ("Q6606110", "industrialist"), "etnograf": ("Q12347522", "ethnographer"),
    "klasicni-filolog": ("Q16267607", "classical philologist"), "graver": ("Q329439", "engraver"),
    "medaljer": ("Q1708232", "medalist"), "speleolog": ("Q16742175", "speleologist"),
    "logik": ("Q14565331", "logician"), "cebelar": ("Q852389", "beekeeper"),
    "planinec": ("Q9149093", "mountaineer"),                    # Slovenian label 'gornik'
    "skladatelj": ("Q36834", "composer"), "kipar": ("Q1281618", "sculptor"), "fotograf": ("Q33231", "photographer"),
    "botanik": ("Q2374149", "botanist"), "geolog": ("Q520549", "geologist"), "kemik": ("Q593644", "chemist"),
    "fizik": ("Q169470", "physicist"), "astronom": ("Q11063", "astronomer"), "diplomat": ("Q193391", "diplomat"),
    "pravnik": ("Q185351", "jurist"), "odvetnik": ("Q40348", "lawyer"), "notar": ("Q189010", "notary"),
    "sodnik": ("Q16533", "judge"), "lekarnar": ("Q105186", "pharmacist"), "knjiznicar": ("Q182436", "librarian"),
    "arhivar": ("Q635734", "archivist"), "organist": ("Q765778", "organist"), "dirigent": ("Q158852", "conductor"),
    "psiholog": ("Q212980", "psychologist"), "sociolog": ("Q2306091", "sociologist"),
    "arheolog": ("Q3621491", "archaeologist"), "dramatik": ("Q214917", "playwright"),
}
# relation words of the list -> relation; other relations (nečak, bratranec, zet, sorodnik ...) are not used
RELATIONS = {"oče": "father", "mati": "mother", "starši": "parent", "mož": "spouse", "žena": "spouse",
             "brat": "sibling", "brata": "sibling", "bratje": "sibling", "sestra": "sibling", "sestri": "sibling",
             "sestre": "sibling", "polbrat": "sibling", "polbrata": "sibling", "polsestra": "sibling",
             "sin": "child", "sinova": "child", "sinovi": "child", "hči": "child", "hčerka": "child",
             "hčeri": "child", "hčere": "child", "hčerke": "child", "otroci": "child"}


def candidates(n_):
    return s.linked("P1254", n_)


def page(ident):
    def fetch():
        text = s.polite_get(URL.format(ident), gap=GAP)
        if "collapsable-tei" not in text:
            raise ValueError(f"sbi{ident}: no entry page at this address ({len(text)} bytes)")
        return text
    return s.cached(WORK, f"{ident}.html", fetch)


def attributes(raw):
    return dict(re.findall(r'([\w:]+)="([^"]*)"', raw or ""))


def tei_person(t, ident):
    """The record of the entry's own person (xml:id 'sbi<ID>'), as XML text, or None."""
    for code in re.findall(r'<code class="language-markup">(.*?)</code>', t, re.S):
        x = html.unescape(code)
        for m in re.finditer(r"<person\b([^>]*)>(.*?)</person>", x, re.S):
            if attributes(m.group(1)).get("xml:id") == f"sbi{ident}":
                return re.sub(r"<!--.*?-->", "", m.group(2), flags=re.S)
    return None


def shown_date(text):
    """(year, month, day) the page shows: '26. jun. 1913', '9. maja 1947', '24. febr. 1926', 'mar. 1993', '2015'."""
    text = " ".join(text.lower().split())
    m = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{3,4})", text)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    m = re.fullmatch(r"(?:(\d{1,2})\.\s*)?(?:([a-zčšž]+)\.?\s+)?(\d{3,4})", text)
    if not m or (m.group(2) and m.group(2)[:3] not in MONTHS) or (m.group(1) and not m.group(2)):
        return None
    return int(m.group(3)), MONTHS[m.group(2)[:3]] if m.group(2) else None, int(m.group(1)) if m.group(1) else None


def event_date(part, pid, label, country, notes):
    """The date fact of a <birth> or <death>, or None (with a note when there is a date that is not used)."""
    m = re.search(r"<date\b([^>]*?)(?:/>|>(.*?)</date>)", part, re.S)
    if not m:
        return None
    a, shown = attributes(m.group(1)), d.clean(m.group(2) or "")
    if a.get("source") in UNVERIFIED:
        notes.append(f"{label} '{shown or a.get('when', '')}' not used: marked unverified in the entry")
        return None
    if set(a) - {"when", "source"}:                  # notBefore/notAfter, cert, from/to ...: not one exact date
        notes.append(f"{label} '{shown or a}' not used: not an exact date")
        return None
    w = re.fullmatch(r"(\d{4})(?:-(\d\d)(?:-(\d\d))?)?", a.get("when", ""))
    if not w:
        notes.append(f"{label} '{shown or a.get('when', '')}' not used: not an exact date")
        return None
    when = tuple(int(x) if x else None for x in w.groups())
    if shown and shown_date(shown) != when:
        notes.append(f"{label}: the record gives {a['when']}, the page shows '{shown}'; not used")
        return None
    y, mo, day = when
    if day and not n.valid_day(y, mo, day):
        notes.append(f"{label} {a['when']} not a valid day")
        return None
    return s.date_fact(pid, label, y, mo, day, country)


def country_of(name):
    """The present-day country the record names ('Italija'): its item, or None."""
    if name in COUNTRIES:
        return COUNTRIES[name]
    region = s.region_named(name, ("sl",)) if name and d.PLAIN.match(name) else None
    return region[1][0] if region and region[0] == "country" and len(region[1]) == 1 else None


def near_place(names, country, at):
    """The one settlement of the country labelled (or also called) by one of the names and lying within RADIUS km
    of the record's coordinates: (item, None), or (None, why)."""
    lits = " ".join(dict.fromkeys(f"{json.dumps(name, ensure_ascii=False)}@{tag}" for name, lang in names
                                  for tag in (lang, "sl", "mul") if d.PLAIN.match(name)))
    if not lits:
        return None, "no plain name"
    rows = d.lookup(f"SELECT DISTINCT ?i ?c ?at WHERE {{ VALUES ?l {{ {lits} }} {{ ?i rdfs:label ?l }} UNION "
                    f"{{ ?i skos:altLabel ?l }} ?i wdt:P31 ?c ; wdt:P17 wd:{country} ; wdt:P625 ?at . }}")
    settlements, far = d.settlement_classes(), {}
    for r in rows:
        point = re.search(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", r[2] if len(r) > 2 else "", re.I)   # QLever: 'POINT('
        if point and r[1] in settlements:
            km = n.km(at, (float(point.group(2)), float(point.group(1))))
            far[r[0]] = min(far.get(r[0], km), km)
    close = sorted(q for q, km in far.items() if km <= RADIUS)
    if len(close) > 1:              # a town and the municipality named after it: the town, which lies in the other
        d.place_containers(close)
        close = [q for q in close if not any(d.lies_in(o, q) for o in close)]
    if len(close) == 1:
        return close[0], None
    if close:
        return None, f"{len(close)} settlements of that name lie within {RADIUS:.0f} km"
    if far:
        return None, f"no settlement of that name within {RADIUS:.0f} km of the entry's coordinates"
    return None, "no settlement of that name in that country"


def event_place(part, label, notes):
    """(place item or None, country item or None) of a <birth> or <death>."""
    m = re.search(r"<placeName\b([^>]*)>(.*?)</placeName>", part, re.S)
    if not m:
        return None, None
    a, body = attributes(m.group(1)), m.group(2)
    settlements = [(attributes(x.group(1)), d.clean(x.group(2)))
                   for x in re.finditer(r"<settlement\b([^>]*)>(.*?)</settlement>", body, re.S)]
    country_name = d.clean((re.search(r"<country\b[^>]*>(.*?)</country>", body, re.S) or [None, ""])[1])
    country = country_of(country_name)
    names = [(name, sa.get("xml:lang", "sl")) for sa, name in settlements if sa.get("type") != "historic" and name]
    shown = ", ".join(dict.fromkeys([nm for nm, _ in names] + ([country_name] if country_name else [])))
    if not names or any(nm.lower() in ("neznano", "neznan", "?") for nm, _ in names):
        return None, country
    if a.get("source") in UNVERIFIED:
        notes.append(f"{label} '{shown}' not used: marked unverified in the entry")
        return None, country
    if "cert" in a or re.search(r"\bcert=", body):
        notes.append(f"{label} '{shown}' not used: marked uncertain in the entry")
        return None, country
    if re.search(r"<district\b", body):
        notes.append(f"{label} '{shown}' names a part of a settlement; not matched to one place item")
        return None, country
    geo = re.search(r"<geo>\s*([-\d.]+)\s+([-\d.]+)\s*</geo>", body)
    if not country or not geo:
        notes.append(f"{label} '{shown}' not matched to one place item (no {'country' if not country else 'coordinates'})")
        return None, country
    q, why = near_place(names, country, (float(geo.group(1)), float(geo.group(2))))
    if not q:
        notes.append(f"{label} '{shown}' not matched to one place item ({why})")
    return q, country


def occupation_facts(codes, notes):
    known, facts = oebl.checked(OCCUPATIONS), []
    for code in codes:
        if code in known:
            facts.append(d.fact("P106", d.item_value(known[code][0]), "occupation"))
        else:
            notes.append(f"occupation '{code}' not mapped")
    return facts


def year_born(q):
    """The item's year of birth on Wikidata, when it gives one year."""
    years = {int(m.group(1)) for r in d.lookup(f"SELECT ?b WHERE {{ wd:{q} wdt:P569 ?b }}")
             if r and (m := re.match(r"(\d{4})-", r[0]))}
    return years.pop() if len(years) == 1 else None


def related(t):
    """[(relation word, entry ID, name)] of the list of related people, for each person the list gives as nothing
    but a link to his or her own entry."""
    start = t.find('<div class="relations')
    if start < 0:
        return []
    ends = [i for i in (t.find('<div class="images', start), t.find('<div class="modal', start),
                        t.find('<div class="tei"', start)) if i > 0]
    block = t[start:min(ends) if ends else len(t)]
    found = []
    for word, value in re.findall(r'<div class="type">([^<]*)</div>\s*<div class="value">(.*?)</div>', block, re.S):
        word = d.clean(word).rstrip(":").strip().lower()
        items = re.findall(r"<li>(.*?)</li>", value, re.S) or [value]
        for item in items:
            m = re.fullmatch(r'\s*<a href="/oseba/sbi(\d+)/">([^<]*)</a>\s*', item)
            if m:
                found.append((word, m.group(1), d.clean(m.group(2))))
    return found


def relatives(t, born, notes):
    facts, seen = [], set()
    for word, rid, name in related(t):
        kind = RELATIONS.get(word)
        if not kind:
            continue
        q = s.item_with("P1254", rid)
        if not q:
            notes.append(f"{kind} '{name}': entry {rid} is on no single item; not used")
            continue
        if kind == "parent":
            kind = {n.MALE: "father", n.FEMALE: "mother"}.get(d.sex(q))
            if not kind:
                notes.append(f"parent '{name}' ({q}): no single sex on Wikidata, so neither father nor mother; not used")
                continue
        if (kind, q) in seen:
            continue
        seen.add((kind, q))
        if not n.years_fit(kind, born, year_born(q)):
            notes.append(f"{kind} '{name}' ({q}): the years of birth do not fit; not used")
            continue
        f = n.relative_fact(kind, q, notes, name)
        if f:
            facts.append(f)
    return facts


def title_of(t, ident):
    h1 = re.search(r"<h1>(.*?)(?:<small>|</h1>)", t, re.S)
    name = d.clean(h1.group(1)) if h1 else f"sbi{ident}"
    last, sep, first = name.partition(",")
    first = first.split(",")[0].strip()                 # 'Andrioli, Frančišek Ksaverij, pl.' (pl.: a noble)
    return f"{first} {last.strip()}" if sep and first else name


def read(ident):
    t = page(ident)
    title = title_of(t, ident)
    person = tei_person(t, ident)
    if person is None:
        raise LookupError(f"sbi{ident}: the page has no record of this entry")
    facts, notes, born = [], [], None
    sex = re.search(r'<sex value="(\d)"\s*/?>', person)
    if sex and sex.group(1) in SEX:
        facts.append(d.fact("P21", d.item_value(SEX[sex.group(1)]), "sex or gender"))
    for tag, (dpid, dlabel), (ppid, plabel) in (("birth", ("P569", "date of birth"), ("P19", "place of birth")),
                                                ("death", ("P570", "date of death"), ("P20", "place of death"))):
        m = re.search(rf"<{tag}\b([^>]*)>(.*?)</{tag}>", person, re.S)
        if not m:
            continue
        if "cert" in m.group(1):
            notes.append(f"{tag}: marked uncertain in the entry; not used")
            continue
        q, country = event_place(m.group(2), plabel, notes)
        f = event_date(m.group(2), dpid, dlabel, country or SLOVENIA, notes)
        if f:
            facts.append(f)
            if dpid == "P569":
                born = int(f["value"]["value"]["time"][1:5])
        if q:
            facts.append(d.fact(ppid, d.item_value(q), plabel))
    codes = re.findall(r'<occupation\b[^>]*\bcode="#([^"]+)"', person)
    facts += occupation_facts(list(dict.fromkeys(codes)), notes)
    facts += relatives(t, born, notes)
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
