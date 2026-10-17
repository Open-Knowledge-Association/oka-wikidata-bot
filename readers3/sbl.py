"""Svenskt biografiskt lexikon (SBL, P3217), Riksarkivet: the open-data extract of the lexicon (CSV, CC0, published
2024-05-13 under "Nedladdningsbara datamängder" at sok.riksarkivet.se/data-api), downloaded once and kept. Its fields
give each person's sex and the dates and places of birth and death; the places are parishes (församlingar), towns or
countries, a country with a comment naming the town ('Finland', 'i Åbo'). The article text is not in the extract;
the CV field is not read. Family articles (släktartiklar) are of kind "family" and give no facts.

Places: a Swedish parish or city is the one item of that class labelled with the name (as SBL's earlier imports to
Wikidata did), within the county when SBL adds one ('Bromma församling (AB-län)'); a town abroad by the comment,
among the towns of the country named. Approximate dates ('omkring', 'troligen', '1640omkr') are not used, nor a place
whose comment makes it unclear: uncertain ('troligen i Sthlm'), or another place that the entry records instead
('kbf i Strängnäs', church-registered there; 'enl db för Helsingborg', per the death register; 'begr i Vada', buried;
'dp i London', baptised)."""
import csv
import io
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

import dictionaries as d
import sources3 as s

WORK = "sbl"
CSV = "https://filer.riksarkivet.se/registerdata/SBL/csv/SBL_2023.csv"
SWEDEN = "Q34"
LANGS = ("sv", "en")
PARISH_TIERS = (("Q615980",), ("Q2261863",))    # parish of the Church of Sweden; city (stad) of Sweden ('Stockholms stad')
COUNTIES = {"AB": "Q104231", "C": "Q104926", "D": "Q106915", "E": "Q104940", "F": "Q103672", "G": "Q104746",
            "H": "Q103707", "I": "Q103738", "K": "Q102377", "L": "Q103659", "M": "Q103659", "N": "Q103691",
            "O": "Q103093", "P": "Q103093", "R": "Q103093", "S": "Q106789", "T": "Q104257", "U": "Q105075",
            "W": "Q103732", "X": "Q103699", "Y": "Q104891", "Z": "Q103679", "AC": "Q104877", "BD": "Q103686"}
# county letters as SBL writes them ('(AB-län)') -> today's county, which the parishes lie in (P131): the old counties
# of Kristianstad (L) and Malmöhus (M) are now Skåne, those of Göteborg och Bohus (O), Älvsborg (P) and Skaraborg (R)
# Västra Götaland
COUNTRY_NAMES = {"Storbritannien och Nordirland": "Storbritannien", "USA, Amerikas Förenta Stater": "USA"}
SWEDISH = re.compile(r"församling|\bstad$|\bkommun$|\bsocken$|-län\)$")
REFINES = re.compile(r"^(?:i|på|vid)\s", re.I)             # 'i Åbo', 'på Björnsnäs', 'vid Mantorp': where exactly
UNCLEAR = re.compile(r"\b(?:troligen|trol|sannolikt|möjligen|förmodligen|antagligen|kanske|kan|el|eller|enl|enligt|"
                     r"db|fdb|fb|kbf|fbf|begr|begravd|gravlagd|dp|döpt|bosatt|tidigare|dåv|dåvarande|eg|nu|ej|nära|"
                     r"utanför)\b", re.I)
GENDER = {"m": "Q6581097", "f": "Q6581072"}


def fetch():
    """The CSV, which is Windows-1252, as text (sources3.polite_get reads UTF-8 only): one request, under the bot's
    user agent, after the usual gap for that site."""
    d.wait_turn(urllib.parse.urlsplit(CSV).netloc, 3.0)
    if shutil.which("curl"):
        raw = subprocess.run(["curl", "-s", "-S", "-f", "-L", "--max-time", "900", "-A", d.UA, CSV],
                             capture_output=True, check=True).stdout
    else:
        with urllib.request.urlopen(urllib.request.Request(CSV, headers={"User-Agent": d.UA}), timeout=900) as r:
            raw = r.read()
    return raw.decode("cp1252", errors="replace")


_rows = None


def rows():
    global _rows
    if _rows is None:
        text = s.cached(WORK, "SBL_2023.csv", fetch)
        _rows = {r["Article id"].strip(): r for r in csv.DictReader(io.StringIO(text), delimiter=";")}
    return _rows


def candidates(n):
    return s.linked("P3217", n)


def field(r, key):
    v = d.clean(r.get(key) or "")
    return "" if v == "NULL" else v


def date(r, which, pid, label, notes):
    """'Year of birth' 1819, 'Month of birth' 5, 'Day of birth' 17 -> a date; a prefix ('omkring', 'troligen',
    'senast') or a year that is not four digits ('1640omkr', '1000-talet') is not used."""
    prefix, y = field(r, f"Prefix to year of {which}"), field(r, f"Year of {which}")
    m, day = field(r, f"Month of {which}"), field(r, f"Day of {which}")
    if not y or y == "0000":
        return None
    if prefix or not re.fullmatch(r"\d{4}", y):
        notes.append(f"{label} '{' '.join(x for x in (prefix, y) if x)}' not used: approximate or unclear")
        return None
    if (day and not m) or (m and not re.fullmatch(r"\d{1,2}", m)) or (day and not re.fullmatch(r"\d{1,2}", day)) \
            or (m and not 1 <= int(m) <= 12) or (day and not 1 <= int(day) <= 31):
        notes.append(f"{label} {'.'.join(x for x in (day, m, y) if x)} not in a clear format")
        return None
    return s.date_fact(pid, label, y, m or None, day or None, SWEDEN)


def variants(name):
    """SBL's spelling and Wikidata's labels differ in small ways: 'Sankt Olai' / 'S:t Olai', 'Linköpings
    Domkyrkoförsamling' / 'Linköpings domkyrkoförsamling'."""
    lower = lambda t: re.sub(r"(?<=\s)([A-ZÅÄÖ][a-zåäö]*församling(?:en)?)\b", lambda m: m.group(1).lower(), t)
    short = re.sub(r"\bSankta\b", "S:ta", re.sub(r"\bSankt\b", "S:t", name))
    return list(dict.fromkeys([name, short, lower(name), lower(short)]))


def swedish_place(name):
    """A parish ('Klara församling', 'Bromma församling (AB-län)') or city ('Stockholms stad') in Sweden: the one item
    of that class with the name as label (or, within the county named, as alias); else, for a name that is no parish,
    the one town or municipality so named. Two names ('Väsby församling, Katarina församling', 'Kungsholm eller
    Ulrika Eleonora') are not matched."""
    county = None
    m = re.fullmatch(r"(.+?)\s*\(([A-Z]{1,2})-län\)", name)
    if m:
        name, county = m.group(1).strip(), COUNTIES.get(m.group(2))
        if not county:
            return None
    if re.search(r",|\beller\b|\boch\b|\(|\d", name):
        return None
    for v in variants(name):
        q = d.place(v, PARISH_TIERS, ("sv",), SWEDEN, within=county, min_links=10 ** 9)   # one candidate only
        if q or d._any_candidate.get((d.place_name(v), PARISH_TIERS, county, SWEDEN, False)):
            return q                                     # found, or several parishes of that name: unclear
    if county or "församling" in name:
        return None                                      # a parish not found as such is not looked for as a town
    return s.town(name, LANGS, SWEDEN)


def place_abroad(region, comment):
    """'Finland' with 'i Åbo', 'Estland' with 'i Reval/Tallinn', 'USA' with 'i Chicago, Illinois', 'Tyskland' with
    'i Stralsund, S:t Nikolai': the town the comment names, among the towns of that country (or region)."""
    m = re.fullmatch(r"i\s+([^,;()]+?)(?:,\s*([^,;()]+?))?\.?", comment, re.I)
    if not m:
        return None
    town, part = m.group(1).strip(), (m.group(2) or "").strip()
    inner = s.region_named(part, LANGS) if part and not re.search(r"\d|\.", part) else None
    if inner and inner[0] == "within":                    # 'Chicago, Illinois', 'London, England'
        region = inner
    found = {s.town(t.strip(), LANGS, region) for t in town.split("/") if t.strip()}
    return found.pop() if len(found) == 1 else None      # 'Reval/Tallinn': both names the same town


def place(r, which, pid, label, notes):
    name, comment = field(r, f"Place of {which}"), field(r, f"Comment on place of {which}")
    if not name:
        return None
    shown = name + (f" ({comment})" if comment else "")
    if comment and (not REFINES.match(comment) or UNCLEAR.search(comment)):
        notes.append(f"{label} '{shown}' not used: the comment makes it unclear")
        return None
    if SWEDISH.search(name):
        q = swedish_place(name)
    else:
        country = s.region_named(COUNTRY_NAMES.get(name, name), LANGS)
        if country and country[0] == "country" and SWEDEN not in country[1]:
            q = place_abroad(country, comment) if comment else None
        elif country:
            q = None                                      # 'Sverige', or a region: no town
        else:
            q = swedish_place(name)
    if q:
        return d.fact(pid, d.item_value(q), label)
    notes.append(f"{label} '{shown}' not matched to one place item")
    return None


def read(ident):
    r = rows().get(str(ident).strip())
    if r is None:
        raise LookupError(f"SBL article {ident} is not in the open-data extract of 2024-05-13")
    kind = {"Person article": "person", "Family article": "family"}.get(field(r, "Type of article"))
    if not kind:
        raise LookupError(f"SBL article {ident}: article type '{field(r, 'Type of article')}' not known")
    surname, given = field(r, "Surname"), field(r, "Given name")
    if kind == "family":
        return {"title": f"{surname}, släkt", "kind": "family", "facts": [], "notes": []}
    facts, notes = [], []
    for which, pid, label in (("birth", "P569", "date of birth"), ("death", "P570", "date of death")):
        f = date(r, which, pid, label, notes)
        if f:
            facts.append(f)
    for which, pid, label in (("birth", "P19", "place of birth"), ("death", "P20", "place of death")):
        f = place(r, which, pid, label, notes)
        if f:
            facts.append(f)
    sex = field(r, "Gender").lower()
    if sex in GENDER:
        facts.append(d.fact("P21", d.item_value(GENDER[sex]), "sex or gender"))
    return {"title": " ".join(x for x in (given, surname) if x), "kind": "person", "facts": facts, "notes": notes}
