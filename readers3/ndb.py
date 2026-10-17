"""Deutsche Biographie (P7902), the index and the NDB and ADB articles of the Historical Commission at the Bavarian
Academy of Sciences, through its open data service data.deutsche-biographie.de (an empty robots.txt): the Solr index
API for an entry's metadata (CC0) and the documented download of an article's TEI file, of which only the metadata
header, the article's head line and its genealogy are kept (facts are read from them; the biography is not kept).
One request a second; www.deutsche-biographie.de itself (Crawl-delay 10) is not read.

P7902 mirrors the GND on about 985,000 items: the Deutsche Biographie indexes everyone in the GND, but writes about
only some 44,000 people (an NDB or ADB article). Only those entries are read (candidates()), and only what the NDB
or ADB editors state is used: the article's head line (dates and places of birth, death and burial), the index's
register years, sex and confession, the occupations of the index, and the relatives the NDB genealogy links to their
own entries. Dates and places the index has from the GND alone are not used.

The German helpers below (dates, places, occupations, GND numbers) are shared with saebi.py and hessbio.py."""
import html
import json
import math
import random
import re
import urllib.parse

import dictionaries as d
import sources3 as s

WORK = "ndb"
SOLR = "https://data.deutsche-biographie.de/beta/solr-open/"
REST = "https://data.deutsche-biographie.de/rest/{}.xml"
FIELDS = ("id,defgnd,gnd,defnam,r_flr,r_sex,r_fam,r_ndb,r_adb,bdisplay,ddisplay,bmin,bmax,dmin,dmax,r_bpl,r_dpl,r_tpl,"
          "f_bpl_geo,f_dpl_geo,f_tpl_geo,r_rel,r_ber")
GERMANY = "Q183"
LANGS = ("de", "en")
MALE, FEMALE = "Q6581097", "Q6581072"

# ------------------------------------------------------------------ German helpers (also used by saebi and hessbio)

MONTHS = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
          "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12}
DAY = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{3,4})")
UNSURE = re.compile(r"\?|\b(?:um|ca|circa|etwa|vor|nach|zwischen|zw|oder|bis|vermutlich|wahrscheinlich|angeblich|"
                    r"frühestens|spätestens|err|erw|erwähnt|belegt|Anfang|Ende|Mitte|Jh|Jahrhundert)\b|[\[\]/–]",
                    re.I)


def german_date(raw, pid, label, notes, country=GERMANY):
    """A date as the German-language works write it: '17.05.1819', '2.7.1859', '1855', 'August 1850' (the year).
    'getauft' or '~' (a baptism date) gives P1636 instead of a date of birth; a burial date ('beerdigt') is no date of
    death. A date in the old and the new style ('03.02.1650 (a.S.)/ 13.02.1650 (n.S.)') is read in the new style.
    Approximate or uncertain dates ('um 1600', 'vor 1700', '[1620/1621]', '1609?', 'nach Oktober 1530') are not
    used: a note says so."""
    text = d.clean(raw or "")
    if not text or text.lower() in ("k.a.", "k. a.", "unbekannt", "-"):
        return None
    if re.search(r"beerdigt|begraben|bestattet|beigesetzt", text, re.I):
        notes.append(f"{label} '{text}' not used: a burial date")
        return None
    baptism = re.search(r"getauft|\bget\.|~|Taufe", text, re.I)
    if baptism and pid == "P569":
        pid, label = "P1636", "date of baptism"
    elif baptism:
        notes.append(f"{label} '{text}' not used: a baptism date")
        return None
    text = re.sub(r"\s*(?:\(getauft\)|\(Taufe\)|getauft|get\.|~)\s*", " ", text).strip()
    styles = re.fullmatch(r"(.+?)\s*\(a\.\s*S\.\)\s*/\s*(.+?)\s*\(n\.\s*S\.\)", text)
    if styles:                                      # both calendars given: the Gregorian date, no calendar doubt
        m = DAY.fullmatch(styles.group(2).strip())
        if m:
            day, month, year = map(int, m.groups())
            return s.date_fact(pid, label, year, month, day, None) if valid_day(year, month, day) else None
    m = DAY.fullmatch(text)
    if m:
        day, month, year = map(int, m.groups())
        if valid_day(year, month, day):
            return s.date_fact(pid, label, year, month, day, country)
    m = re.fullmatch(r"(\d{3,4})", text) or re.fullmatch(r"([A-Za-zäÄ]+)\s+(\d{3,4})", text)
    if m and (len(m.groups()) == 1 or m.group(1).lower() in MONTHS):
        return s.date_fact(pid, label, int(m.groups()[-1]))
    notes.append(f"{label} '{text}' not used: not an exact date")
    return None


def valid_day(year, month, day):
    return 1 <= month <= 12 and 1 <= day <= (29 if month == 2 and year % 4 == 0 else
                                             (28, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month])


LANG_TAGS = {"poln": ("Q36", "pl"), "tschech": ("Q213", "cs"), "russ": ("Q159", "ru"), "lett": ("Q211", "lv"),
             "estn": ("Q191", "et"), "litau": ("Q37", "lt"), "lit": ("Q37", "lt"), "frz": ("Q142", "fr"),
             "franz": ("Q142", "fr"), "ital": ("Q38", "it"), "ungar": ("Q28", "hu"), "slowak": ("Q214", "sk"),
             "slowen": ("Q215", "sl"), "kroat": ("Q224", "hr"), "rumän": ("Q218", "ro"), "ukr": ("Q212", "uk"),
             "dän": ("Q35", "da"), "niederl": ("Q55", "nl")}
COUNTRY_LANG = {q: lang for q, lang in LANG_TAGS.values()}
NEAR = re.compile(r"\b(?:bei|b\.|nahe|nächst|unweit|Nähe|Umgebung|Gegend|Kreis|Landkreis|Amt|Bezirk|Gemeinde|"
                  r"Ortsteil|Stadtteil|Gut|Schloss|Schloß|Burg|Kloster|Hof)\b", re.I)
DOUBT = re.compile(r"\?|vermutl|wahrscheinl|angebl|vielleicht", re.I)
CEMETERY = re.compile(r"friedhof|kirchhof|gottesacker|gruft|kirche|dom\b|münster|kapelle|kloster|mausoleum|"
                      r"begräbnis|grab", re.I)
SITE = re.compile(r"\b(?:West|Ost|Nord|Süd)\b")      # 'Berlin (West)': the city itself


def named_town(names, country):
    """The one town or municipality of the country labelled with one of these exact names, for names town() does
    not take ('Halle (Saale)', 'Lauterbach (Hessen)') and the spellings of 'X/ Y' ('Frankfurt am Main')."""
    names = [n for n in dict.fromkeys(names) if n and d.PLAIN.match(n)]
    if not names:
        return None
    lits = " ".join(f"{json.dumps(n, ensure_ascii=False)}@{lang}" for n in names for lang in ("de", "mul"))
    in_country, of_country = d.country_where(country)
    rows = d.lookup(f"SELECT DISTINCT ?i ?c WHERE {{ VALUES ?l {{ {lits} }} ?i rdfs:label ?l ; wdt:P31 ?c "
                    f"{in_country} . {of_country} }}")
    found = {r[0] for r in rows if len(r) > 1 and r[1] in d.settlement_classes()}
    return next(iter(found)) if len(found) == 1 else None


def coordinates(q):
    rows = d.lookup(f"SELECT ?c WHERE {{ wd:{q} wdt:P625 ?c }}")
    found = []
    for r in rows:
        m = re.search(r"Point\(([-\d.]+) ([-\d.]+)\)", r[0] if r else "")
        if m:
            found.append((float(m.group(2)), float(m.group(1))))
    return found


def km(a, b):
    (la1, lo1), (la2, lo2) = [(math.radians(x), math.radians(y)) for x, y in (a, b)]
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def german_place(raw, label, notes, home=GERMANY, burial=False, at=None):
    """The place item for a place name as these works write it: 'Dresden'; 'Halle/ Saale' or 'Frankfurt/Main' (the
    town so labelled: 'Halle (Saale)', 'Frankfurt am Main'); 'Breslau (poln. Wrocław)' (the town of that name in
    Poland); 'Venedig (Italien)' (a town of the country named); 'Bartenstein heute Bartoszyce (Polen)'; 'Rhena
    (heute Korbach)' (Rhena, else what it belongs to today); 'Lauterbach (Hessen)' (an item so labelled); for a
    burial, 'Dresden (Innerer Neustädter Friedhof)' or 'München, Südlicher Friedhof' is the town. Names marked
    uncertain, 'X bei Y', districts, estates and other descriptions are left unmatched with a note. `at`: the
    (latitude, longitude) the work gives for the place; an item more than 25 km away is not used."""
    text = d.clean(raw or "").strip(" ,.;")
    if not text or text.lower() in ("k.a.", "k. a.", "unbekannt", "n.n.", "-"):
        return None
    q = None
    if DOUBT.search(text):
        notes.append(f"{label} '{text}' uncertain; not used")
        return None
    lang_tag = re.fullmatch(r"(.+?)\s*\((\w+)\.\s+([^()]+)\)", text)
    heute = re.fullmatch(r"(.+?)\s*[(,]?\s*\bheute\s+(?:zu\s+|Teil von\s+|Ortsteil von\s+|Stadtteil von\s+)?"
                         r"([^()]+?)\s*(?:\(([^()]+)\))?\s*\)?", text)
    paren = re.fullmatch(r"([^()]+?)\s*\(([^()]+)\)", text)
    slash = re.fullmatch(r"([^/()]+?)\s*/\s*([^/()]+)", text)
    comma = re.fullmatch(r"([^,()]+),\s*(.+)", text)
    if re.match(r"(?:in|im|auf|an|am|bei|zu|zwischen|vor|nahe)\s", text, re.I):
        pass                                        # 'in der Leine': no place
    elif lang_tag and lang_tag.group(2).lower() in ("sorb", "wend", "obersorb", "niedersorb"):
        q = plain_place(lang_tag.group(1), home)    # 'Bautzen (sorb. Budyšin)': the German name, at home
    elif lang_tag and lang_tag.group(2).lower() in LANG_TAGS:
        country, lang = LANG_TAGS[lang_tag.group(2).lower()]
        if not NEAR.search(lang_tag.group(3)):
            q = s.town(lang_tag.group(3), (lang,) + LANGS, country)
    elif heute:
        old, now, land = heute.group(1).strip(" ,("), heute.group(2).strip(), heute.group(3)
        region = s.region_named(land, LANGS) if land else None
        if region:                                  # 'Breslau heute Wrocław (Polen)': the town of that name there
            langs = tuple(COUNTRY_LANG[c] for c in region[-1] if c in COUNTRY_LANG) + LANGS
            q = s.town(now, langs, region) if not NEAR.search(now) else None
        elif not land and not NEAR.search(old):     # 'Rhena (heute Korbach)': Rhena, else the town it is in today
            q = plain_place(old, home) or (s.town(now, LANGS, home) if not NEAR.search(now) else None)
    elif paren and not NEAR.search(paren.group(1)):
        town_, extra = paren.group(1).strip(), paren.group(2).strip()
        if burial and CEMETERY.search(extra) or SITE.fullmatch(extra):
            q = plain_place(town_, home)
        else:
            q = named_town([f"{town_} ({extra})"], home)
            region = None if q else s.region_named(extra, LANGS)
            if region:                              # 'Venedig (Italien)', 'Lauterbach (Hessen)'
                langs = tuple(COUNTRY_LANG[c] for c in region[-1] if c in COUNTRY_LANG) + LANGS
                q = s.town(town_, langs, region)
    elif slash and not NEAR.search(text):
        x, y = slash.group(1).strip(), slash.group(2).strip()
        q = named_town([f"{x} ({y})", f"{x} am {y}", f"{x} an der {y}", f"{x} im {y}", f"{x} in {y}", f"{x}/{y}"],
                       home)
    elif comma and not NEAR.search(comma.group(1)):
        town_, rest = comma.group(1).strip(), comma.group(2).strip()
        if burial and CEMETERY.search(rest):
            q = plain_place(town_, home)            # 'Weimar, Fürstengruft': buried in Weimar
        else:
            region = s.region_named(rest, LANGS)
            if region:                              # 'Paris, Frankreich'
                langs = tuple(COUNTRY_LANG[c] for c in region[-1] if c in COUNTRY_LANG) + LANGS
                q = s.town(town_, langs, region)
    elif not NEAR.search(text) and not re.search(r"[(),/]", text):
        q = plain_place(text, home)
    if q and at:
        where = coordinates(q)
        if where and min(km(at, w) for w in where) > 25:
            notes.append(f"{label} '{text}' matched {q}, which lies {min(km(at, w) for w in where):.0f} km from where "
                         "the work places it; not used")
            return None
    if not q:
        notes.append(f"{label} '{text}' not matched to one place item")
    return q


def plain_place(name, home):
    """A plain town name: the one town of the home country so named, else a well-known city anywhere."""
    name = name.strip()
    if not name or re.search(r"[()\d,/]", name):
        return None
    return s.town(name, LANGS, home, given=False)


# occupation words of the German-language works -> item (labels checked on Wikidata, 2026-10-16)
OCCUPATIONS = {
    "Politiker": "Q82955", "Jurist": "Q185351", "Rechtsanwalt": "Q40348", "Lehrer": "Q37226", "Theologe": "Q1234713",
    "Komponist": "Q36834", "Schriftsteller": "Q36180", "Architekt": "Q42973", "Fotograf": "Q33231",
    "Philosoph": "Q4964182", "Sänger": "Q177220", "Dirigent": "Q158852", "Hochschullehrer": "Q1622272",
    "Landwirt": "Q131512", "Kaufmann": "Q215536", "Maler": "Q1028181", "Bildhauer": "Q1281618",
    "Journalist": "Q1930187", "Historiker": "Q201788", "Arzt": "Q39631", "Ingenieur": "Q81096",
    "Unternehmer": "Q131524", "Bibliothekar": "Q182436", "Archivar": "Q635734", "Verleger": "Q2516866",
    "Buchhändler": "Q998550", "Diplomat": "Q193391", "Offizier": "Q189290", "Chemiker": "Q593644",
    "Physiker": "Q169470", "Mathematiker": "Q170790", "Botaniker": "Q2374149", "Zoologe": "Q350979",
    "Geologe": "Q520549", "Apotheker": "Q105186", "Bankier": "Q806798", "Schauspieler": "Q33999",
    "Übersetzer": "Q333634", "Dichter": "Q49757", "Lyriker": "Q49757", "Kunsthistoriker": "Q1792450",
    "Musiker": "Q639669", "Pädagoge": "Q1231865", "Organist": "Q765778", "Richter": "Q16533", "Notar": "Q189010",
    "Grafiker": "Q1925963", "Zeichner": "Q15296811", "Astronom": "Q11063", "Bauingenieur": "Q13582652",
    "Psychologe": "Q212980", "Soziologe": "Q2306091", "Biologe": "Q864503", "Dramatiker": "Q214917",
    "Kartograf": "Q1734662", "Kartograph": "Q1734662", "Essayist": "Q11774202", "Volkskundler": "Q3075052",
    "Ägyptologe": "Q1350189", "Linguist": "Q14467526", "Erfinder": "Q205375", "Geiger": "Q1259917",
}
FEMININE = {"Kauffrau": "Kaufmann", "Ärztin": "Arzt"}
CONFESSIONAL = re.compile(r"^(?:katholische[r]?|evangelische[r]?|lutherische[r]?|reformierte[r]?|jüdische[r]?|"
                          r"protestantische[r]?)\s+", re.I)


def occupation(word):
    """The item for one occupation word ('Malerin' is 'Maler'; 'katholischer Theologe' is 'Theologe'), or None."""
    w = CONFESSIONAL.sub("", d.clean(word).strip(" .;"))
    w = FEMININE.get(w, w)
    if w not in OCCUPATIONS and w.endswith("in") and w[:-2] in OCCUPATIONS:
        w = w[:-2]
    if w not in OCCUPATIONS and w.endswith("in") and w[:-2].replace("ä", "a").replace("ö", "o") in OCCUPATIONS:
        w = w[:-2].replace("ä", "a").replace("ö", "o")
    return OCCUPATIONS.get(w)


def occupation_facts(words, notes):
    facts = []
    for w in words:
        w = d.clean(w).strip(" .;")
        if not w:
            continue
        q = occupation(w)
        if q:
            facts.append(d.fact("P106", d.item_value(q), "occupation"))
        else:
            notes.append(f"occupation '{w}' not mapped")
    return facts


def valid_gnd(gnd):
    """A GND number with a correct check digit: '118540238', '1058140337', '11815821X' (the person numbers: 11 less
    the weighted sum modulo 11) or '4012995-0', '10149449-X' (the older form with a hyphen: the sum modulo 11)."""
    gnd = (gnd or "").strip()
    m = re.fullmatch(r"([1-9]\d{0,7})-([0-9X])", gnd)
    hyphen = bool(m)
    m = m or re.fullmatch(r"(1[012]?\d{7})([0-9X])", gnd)
    if not m:
        return False
    total = sum(int(c) * w for c, w in zip(reversed(m.group(1)), range(2, 20)))
    check = total % 11 if hyphen else (11 - total % 11) % 11
    return m.group(2) == ("X" if check == 10 else str(check))


def gnd_fact(gnd, notes):
    """The GND number the work gives for its subject, when valid and on no item yet (on another item it would be a
    second item for one GND record; on this one there is nothing to add)."""
    gnd = (gnd or "").strip()
    if not gnd:
        return None
    if not valid_gnd(gnd):
        notes.append(f"GND ID '{gnd}' has no valid check digit; not used")
        return None
    rows = d.lookup(f"SELECT ?i WHERE {{ ?i wdt:P227 {json.dumps(gnd)} }}")
    if rows:
        return None
    return d.fact("P227", d.string_value(gnd), "GND ID")


def years_fit(kind, subject, relative):
    """Whether a relative's years of birth fit the relation: a parent born 12 to 75 years before the subject, a
    child as much after, a sibling within 30 years, a spouse within 45. Unknown years fit."""
    if not subject or not relative:
        return True
    gap = subject - relative
    return {"father": 12 <= gap <= 75, "mother": 12 <= gap <= 60, "child": 12 <= -gap <= 75,
            "sibling": abs(gap) <= 30, "spouse": abs(gap) <= 45}[kind]


def relative_fact(kind, q, notes, name):
    """A relation fact, after checking the relative's sex on Wikidata for a father or mother."""
    if kind in ("father", "mother"):
        sex = d.sex(q)
        if sex and sex != (MALE if kind == "father" else FEMALE):
            notes.append(f"{kind} '{name}' ({q}) is not {'male' if kind == 'father' else 'female'} on Wikidata; "
                         "not used")
            return None
    return d.fact(d.REL_PIDS[kind], d.item_value(q), kind)


# ------------------------------------------------------------------ the Deutsche Biographie

def solr(query, fields, rows=10, start=0):
    url = f"{SOLR}?q={urllib.parse.quote(query)}&fl={fields}&rows={rows}&start={start}&wt=json"
    return s.polite_get(url, gap=1.0, as_json=True)["response"]


def articles():
    """The GND numbers of the people with an NDB or ADB article (not families): about 41,000 of the Deutsche
    Biographie's 1.3 million index entries, read from its open Solr index 5,000 at a time."""
    def fetch():
        found, start = [], 0
        while True:
            got = solr("(r_ndb:true OR r_adb:true) AND r_fam:false AND defgnd:*", "id,defgnd", rows=5000, start=start)
            found += [(doc["defgnd"], doc["id"]) for doc in got["docs"]]
            start += 5000
            if start >= got["numFound"]:
                return json.dumps(sorted(found))
    return json.loads(s.cached(WORK, "articles.json", fetch))


def candidates(n, pool=400):
    """Entries with an NDB or ADB article whose GND number exactly one item carries as P7902, in a reproducible
    random order (the selection differs from sources3.linked() alone: P7902 mirrors the GND on 985,000 items, most
    of them people the Deutsche Biographie only indexes from the GND, with no article of its own)."""
    gnds = sorted({g for g, _ in articles()})
    random.Random(20261016).shuffle(gnds)
    values = " ".join(json.dumps(g) for g in gnds[:pool])
    return s.linked("P7902", n, extra=f"VALUES ?id {{ {values} }}")


def index_entry(gnd):
    """The Solr index documents of the entry with this GND number: metadata only (CC0), never the article text."""
    def fetch():
        got = solr(f'defgnd:"{gnd}"', FIELDS)
        return json.dumps(got["docs"], ensure_ascii=False)
    return json.loads(s.cached(WORK, f"{gnd}.solr.json", fetch))


def article_xml(sfz):
    """The entry's TEI file, cut down to its metadata (the header's person, places and relations), the article's
    head line and its genealogy; the biography itself is not kept."""
    def fetch():
        t = s.polite_get(REST.format(sfz), gap=1.0)
        head = re.search(r"<profileDesc>.*?</profileDesc>", t, re.S)
        parts = [head.group(0) if head else ""]
        parts += re.findall(r'<div type="(?:kopf|geneal)".*?</div>', t, re.S)
        return "<entry>" + "\n".join(parts) + "</entry>"
    return s.cached(WORK, f"{sfz}.xml", fetch)


def expand(xml):
    """Text of a piece of TEI: abbreviations expanded ('*' -> 'geboren'), tags dropped."""
    t = re.sub(r"<choice>\s*<abbr[^>]*>.*?</abbr>\s*<expan>(.*?)</expan>\s*</choice>", r"\1", xml, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())


def head_dates(kopf):
    """{'geboren': '15. 6. 1842 München', 'gestorben': ..., 'begraben': ..., 'getauft': ...} from the head line."""
    text = expand(kopf)
    found = {}
    marks = list(re.finditer(r"\b(geboren|getauft|gestorben|begraben)\b", text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        found.setdefault(m.group(1), text[m.end():end].strip(" ,.;"))
    return found


def head_date(expr, pid, label, notes, header_day, register):
    """The date the head line gives ('15. 6. 1842 München' -> 15.6.1842) when it is plain and agrees with the
    header's reading of it and with the index's register year; 'um 1735', '6. oder 8.4.1514' are not used."""
    m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{3,4})(?![\d/–-])", expr) or re.match(r"(\d{3,4})(?![\d/–.-])", expr)
    after = expr[m.end():m.end() + 16] if m else expr
    if not m or re.match(r"\s*(?:\(?\s*\?|oder\b|/|bis\b|–|-\s*\d|\(\s*(?:um|ca|vermutl|wahrscheinl|nicht))", after):
        notes.append(f"{label} '{' '.join(expr.split()[:4])}' not used: not an exact date")
        return None, None
    g = [int(x) for x in m.groups()]
    year, month, day = (g[2], g[1], g[0]) if len(g) == 3 else (g[0], None, None)
    if register and year != register:
        notes.append(f"{label}: the article's head gives {m.group(0)}, the index {register}; not used")
        return None, None
    if day and header_day and header_day != f"{year:04d}-{month:02d}-{day:02d}":
        notes.append(f"{label}: the article's head gives {m.group(0)}, its metadata {header_day}; not used")
        return None, None
    if day and not valid_day(year, month, day):
        return None, None
    return s.date_fact(pid, label, year, month, day, GERMANY), expr[m.end():].strip(" ,.")


def register_year(part):
    """The index's year (gebMin = gebMax and a plain register entry), or None."""
    reg = re.search(r'<date type="register"[^>]*>(.*?)</date>', part or "", re.S)
    lo = re.search(r'<date type="(?:geb|tod)Min">(\d+)</date>', part or "")
    hi = re.search(r'<date type="(?:geb|tod)Max">(\d+)</date>', part or "")
    reg = d.clean(reg.group(1)) if reg else ""
    if lo and hi and lo.group(1) == hi.group(1) and re.fullmatch(r"\d{3,4}", reg) and int(reg) == int(lo.group(1)):
        return int(reg)
    return None


def register_note(part, label, notes):
    reg = re.search(r'<date type="register"[^>]*>(.*?)</date>', part or "", re.S)
    reg = d.clean(reg.group(1)) if reg else ""
    if reg and not re.fullmatch(r"\d{3,4}", reg):
        notes.append(f"{label} '{reg}' not used: not an exact date")


def header_place(part, label, notes, burial=False):
    """The place the header gives for the birth or death (the head line's place, with its GND and OpenStreetMap
    identifiers): the item with the place's GND ID when given, else by its name."""
    m = re.search(r'<placeName type="(?:birth|death)"([^>]*)>(.*?)</placeName>', part or "", re.S)
    if not m:
        return None
    attrs, name = m.group(1), d.clean(m.group(2))
    gnd = re.search(r'gnd="([^"]+)"', attrs)
    at = re.search(r'corresp="([-\d.]+),([-\d.]+)"', attrs)
    at = (float(at.group(1)), float(at.group(2))) if at else None
    if gnd and valid_gnd(gnd.group(1)):
        q = s.item_with("P227", gnd.group(1))
        where = coordinates(q) if q and at else []
        if q and (not where or min(km(at, w) for w in where) <= 25):
            return q
    return german_place(name, label, notes, burial=burial, at=at)


FAITHS = {"katholisch": "Q9592", "römisch-katholisch": "Q9592", "(römisch)-katholisch": "Q9592",
          "evangelisch": "Q23540", "lutherisch": "Q75809", "evangelisch-lutherisch": "Q75809",
          "reformiert": "Q101849", "evangelisch-reformiert": "Q101849", "jüdisch": "Q9268", "israelitisch": "Q9268",
          "mosaisch": "Q9268"}
REL_CODES = {"V": "father", "M": "mother", "B": "sibling", "Schw": "sibling", "S": "child", "T": "child",
             "K": "child"}
REL_WORD = re.compile(r"schwager|schwägerin|schwieger|vetter|cousin|neffe|nichte|onkel|tante|enkel|großvater|"
                      r"großmutter|urgroß|stief|halb|pflege|adoptiv|witwe|witwer|gatte|gattin|verlobt|freund", re.I)


def genealogy(geneal):
    """[(relation, mark, sfz)] the NDB genealogy states with a link to the relative's own entry: 'V' (Vater), 'M'
    (Mutter), 'B'/'Schw' (siblings), '⚭' (spouse), 'S'/'T'/'K' (children). A part of the genealogy (up to ';' or a
    line break) is read only under the mark it begins with; links are taken only before any other mark, marriage,
    relation word or 'd.' ('T d. X': X is the relative's parent) in that part, so that a relative's own family is
    never taken for the subject's. A father, mother or spouse is the first link, with no comma before it."""
    t = re.sub(r'<choice>\s*<abbr type="geneal">\s*(?:<hi[^>]*>)?\s*([^<]*?)\s*(?:</hi>)?\s*</abbr>.*?</choice>',
               lambda m: f" ⟨{m.group(1).strip()}⟩ ", geneal, flags=re.S)
    t = re.sub(r"<choice>\s*<abbr[^>]*>\s*⚭\s*</abbr>.*?</choice>", " ⚭ ", t, flags=re.S)
    t = re.sub(r"<choice>\s*<abbr[^>]*>.*?</abbr>\s*<expan>(.*?)</expan>\s*</choice>", r"\1", t, flags=re.S)
    t = re.sub(r'<ref target="(sfz[\w-]+)\.html"[^>]*>(.*?)</ref>',
               lambda m: f" ⟦{m.group(1)}|{re.sub(r'[<>⟦⟧|()]', ' ', re.sub(r'<[^>]+>', ' ', m.group(2)))}⟧ ", t,
               flags=re.S)
    t = re.sub(r"<lb[^>]*/>", " ; ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"\bverheiratet\b", " ⚭ ", t)
    found = []
    for segment in d.split_outside_parens(t, ";"):
        current, mark, blocked, taken, since, depth = None, None, False, 0, "", 0
        for m in re.finditer(r"⟨[^⟩]*⟩|⟦[^⟧]*⟧|⚭|\(|\)|[^⟨⟦⚭()]+", segment):
            tok = m.group(0)
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth = max(0, depth - 1)
            elif depth > 0:
                continue
            elif tok.startswith("⟨"):
                code = tok[1:-1]
                lead = not since.strip(" ,-–0123456789") and (current is None or current == "child")
                if lead and code in ("S", "T", "K") and current in (None, "child"):
                    current, mark, blocked, taken, since = "child", code, False, 0, ""
                elif lead and current is None:
                    current, mark, blocked, taken, since = REL_CODES.get(code, "other"), code, False, 0, ""
                else:
                    blocked = True                  # another relation, or a mark in a relative's description
            elif tok == "⚭":
                if current is None and not since.strip(" -–"):
                    current, mark, blocked, taken, since = "spouse", "⚭", False, 0, ""
                elif current == "spouse" and taken == 0:
                    since += " "
                else:
                    blocked = True                  # the relative's own marriage
            elif tok.startswith("⟦"):
                sfz = tok[1:].split("|", 1)[0]
                ok = (current not in (None, "other") and not blocked and not re.search(r"\b[de]\.(?:\s|$)", since)
                      and not REL_WORD.search(since))
                if current in ("father", "mother", "spouse"):
                    ok = ok and taken == 0 and "," not in since
                if ok:
                    found.append((current, mark, sfz))
                taken += 1
                since = ""
            else:
                since += tok
                if current == "spouse" and re.search(r"(?:^|\s)\d\)\s*$", tok):
                    blocked, taken, since = False, 0, ""     # 'verheiratet 1) ... 2) ...': each marriage
    return list(dict.fromkeys(found))


def relatives_index(subject, sfzs):
    """The index documents of the linked relatives (GND number, sex, years), in one request."""
    if not sfzs:
        return {}
    def fetch():
        got = solr("id:(" + " OR ".join(sfzs) + ")", "id,defgnd,defnam,r_sex,bmin,bmax", rows=len(sfzs))
        return json.dumps(got["docs"], ensure_ascii=False)
    return {doc["id"]: doc for doc in json.loads(s.cached(WORK, f"{subject}.relatives.json", fetch))}


def year_of(lo, hi):
    return lo // 10000 if lo and hi and lo // 10000 == hi // 10000 else None


def title_of(defnam):
    name = re.sub(r"\s*\([^)]*\)", "", defnam or "").strip()
    parts = [p.strip() for p in name.split(",")]
    return f"{parts[1]} {parts[0]}" if len(parts) == 2 and parts[1] else name


def read(gnd):
    docs = index_entry(gnd)
    if len(docs) != 1:
        return {"title": gnd, "kind": "person", "facts": [],
                "notes": [f"the index has {len(docs)} entries for this GND number; not used"]}
    doc = docs[0]
    title, kind = title_of(doc.get("defnam")), "family" if doc.get("r_fam") else "person"
    if not (doc.get("r_ndb") or doc.get("r_adb")):
        return {"title": title, "kind": kind, "facts": [],
                "notes": ["no NDB or ADB article: the index entry, from the GND, is not used"]}
    x = article_xml(doc["id"])
    person = (re.search(r"<person .*?</person>", x, re.S) or [""])[0]
    birth = (re.search(r"<birth>.*?</birth>", person, re.S) or [""])[0]
    death = (re.search(r"<death>.*?</death>", person, re.S) or [""])[0]
    kopf = (re.search(r'<div type="kopf".*?</div>', x, re.S) or [""])[0]
    facts, notes = [], []
    head = head_dates(kopf)
    born = None
    for key, pid, label, part in (("geboren", "P569", "date of birth", birth), ("gestorben", "P570", "date of death", death)):
        text_day = re.search(r'<date type="text">(\d{4}-\d\d-\d\d)</date>', part)
        register = register_year(part)
        expr = head.get(key) or (head.get("getauft") if key == "geboren" else None)
        baptism = key == "geboren" and "geboren" not in head and bool(expr)
        f = None
        if expr and re.match(r"\D{0,25}\d", expr):   # the head line gives a date (else perhaps only a place)
            f, _ = head_date(expr, "P1636" if baptism else pid, "date of baptism" if baptism else label, notes,
                             text_day.group(1) if text_day else None, register)
        elif register:
            f = s.date_fact(pid, label, register)
        else:
            register_note(part, label, notes)
        if f:
            facts.append(f)
            if f["pid"] == "P569":
                born = int(f["value"]["value"]["time"][1:5])
    for part, pid, label in ((birth, "P19", "place of birth"), (death, "P20", "place of death")):
        q = header_place(part, label, notes)
        if q:
            facts.append(d.fact(pid, d.item_value(q), label))
    if "begraben" in head and doc.get("r_tpl"):
        at = re.search(r"@([-\d.]+),([-\d.]+)$", (doc.get("f_tpl_geo") or [""])[0])
        q = german_place(doc["r_tpl"][0], "place of burial", notes, burial=True,
                         at=(float(at.group(1)), float(at.group(2))) if at else None)
        if q:
            facts.append(d.fact("P119", d.item_value(q), "place of burial"))
    sex = re.search(r'<person [^>]*sex="([12])"', person)
    if sex:
        facts.append(d.fact("P21", d.item_value(MALE if sex.group(1) == "1" else FEMALE), "sex or gender"))
    faith = re.search(r"<faith[^>]*>(.*?)</faith>", person, re.S)
    faith = d.clean(faith.group(1)) if faith else ""
    if faith in FAITHS:
        facts.append(d.fact("P140", d.item_value(FAITHS[faith]), "religion"))
    elif faith and faith != "keine Angabe":
        notes.append(f"religion '{faith}' not used" + (": uncertain" if "?" in faith else ": not mapped"))
    occupation_text = re.search(r"<occupation[^>]*>(.*?)</occupation>", person, re.S)
    if occupation_text:
        facts += occupation_facts(d.clean(occupation_text.group(1)).split(";"), notes)
    f = gnd_fact(doc.get("defgnd"), notes)
    if f:
        facts.append(f)
    geneal = "".join(re.findall(r'<div type="geneal".*?</div>', x, re.S))
    links = genealogy(geneal) if geneal else []
    known = relatives_index(doc["id"], sorted({sfz for _, _, sfz in links}))
    for kind_, mark, sfz in links:
        rel = known.get(sfz)
        if not rel or not rel.get("defgnd"):
            continue
        name = title_of(rel.get("defnam"))
        sex_needed = {"V": 1, "M": 2, "B": 1, "Schw": 2}.get(mark)    # (a child's mark may be a list: '1 S, 2 T')
        if sex_needed and rel.get("r_sex") != sex_needed:
            notes.append(f"{kind_} '{name}': the index gives another sex than the genealogy's mark; not used")
            continue
        if not years_fit(kind_, born or year_of(doc.get("bmin"), doc.get("bmax")),
                         year_of(rel.get("bmin"), rel.get("bmax"))):
            notes.append(f"{kind_} '{name}': the years do not fit; not used")
            continue
        q = s.item_with("P7902", rel["defgnd"])
        if not q:
            notes.append(f"{kind_} '{name}' (GND {rel['defgnd']}) is on no single item; not linked")
            continue
        f = relative_fact(kind_, q, notes, name)
        if f:
            facts.append(f)
    return {"title": title, "kind": kind, "facts": facts, "notes": notes}
