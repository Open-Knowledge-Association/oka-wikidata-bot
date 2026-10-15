"""OKA bot, task 2 (Wikidata:Requests for permissions/Bot/OKA bot 2): facts from two reference works, the
Historical Dictionary of Switzerland (HDS) and Store norske leksikon (SNL), each referenced to the entry.

  python dictionaries.py sample     pick test entries of each kind and build their edits (read-only)
  python dictionaries.py preview    show what each edit would change (read-only)
  python dictionaries.py run        save the edits as OKA bot, then list them at User:OKA bot/Test run 2

Rules:
- facts only, never text. From SNL only the article metadata, which snl.no marks as free to reuse;
- an entry and an item match through the entry's identifier (P902, P4342), or for SNL people without one through
  the same name and the same full date of birth, with exactly one candidate;
- a fact is added only when the item has no value for that property; an equal value gets the entry as reference;
  a different value is left alone and listed;
- places, occupations and other items are used only when the name resolves to exactly one item of the right kind;
- full dates only from 1813 (Switzerland) and 1700 (Norway), when every region used the Gregorian calendar.
"""
import collections
import copy
import csv
import datetime
import functools
import html
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from common import ROOT, UA, cites, curl_json, entity, only_import_refs, ref_json, statement

WORK2 = ROOT / "work" / "task2"
REQUEST2 = "[[Wikidata:Requests for permissions/Bot/OKA bot 2|test run]]"
REPORT_PAGE = "User:OKA bot/Test run 2"
WORKS = {"hds": ("Q642074", "P902", "the Historical Dictionary of Switzerland"),
         "snl": ("Q746368", "P4342", "Store norske leksikon"),
         "nkl": ("Q11992482", "P5081", "Norsk kunstnerleksikon"),           # the other works on snl.no, same API
         "sml": ("Q19378370", "P5082", "Store medisinske leksikon")}
SNL_HOSTS = {"snl": "snl.no", "nkl": "nkl.snl.no", "sml": "sml.snl.no"}
IDS = {"P902", "P4342", "P5081", "P5082", "P227", "P214", "P1248", "P4574", "P2504", "P2333"}   # compared as text
LEGAL_FORMS = {"aksjeselskap": "Q15042660", "as": "Q15042660", "allmennaksjeselskap": "Q14942889",   # Norwegian forms
               "stiftelse": "Q7616418", "samvirkeforetak": "Q59554118", "enkeltpersonforetak": "Q16969092"}
MOUNTAINS = (("Q8502", "Q207326"), ("Q54050",))         # mountain, summit; then hill
KM2 = "Q712226"
GREGORIAN = "http://www.wikidata.org/entity/Q1985727"
JULIAN = "http://www.wikidata.org/entity/Q1985786"
SWISS_PLACES = (("Q70208", "Q685309"),)         # municipality / former municipality of Switzerland
NORWEGIAN_PLACES = (("Q515", "Q1549591"), ("Q755707",),   # city, big city; municipality of Norway; then former
                    ("Q18663579",), ("Q15092344", "Q486972"))  # municipality (Norderhov, not its village); urban area
SETTLEMENT = "settlement"   # a tier of every class below human settlement or municipality: see settlement_classes()
CITY_CLASSES = ("Q515", "Q1549591", "Q5119", "Q200250", "Q1637706")    # city, big city, capital, metropolis, million
WORLD_CITIES = (CITY_CLASSES,)                  # well-known cities anywhere (see world_city())
FOREIGN_PLACES = (CITY_CLASSES, (SETTLEMENT,))  # in a given country or region: a city first, then any town
NO_LANGS, CH_LANGS = ("nb", "nn", "en"), ("de", "fr", "it", "rm")
FOREIGN_LANGS = NO_LANGS + ("da", "sv", "de", "fr", "es", "it", "nl", "pt", "pl")   # 'Antwerpen', 'Lisboa', 'Köln'
MULTI = {"P106", "P1321", "P26", "P40", "P3373", "P1549", "P1448", "P742", "P937"}                        # several values are normal: a new one is added beside the others
PLACE_PROPS = {"P19", "P20", "P159", "P1321", "P36", "P119", "P937", "P610"}
GENDER = {"m": "Q6581097", "k": "Q6581072", "f": "Q6581072"}
OCCUPATIONS = {                                  # SNL word -> (item, its English label, checked at start)
    "maler": ("Q1028181", "painter"), "grafiker": ("Q11569986", "printmaker"), "forfatter": ("Q36180", "writer"),
    "politiker": ("Q82955", "politician"), "skuespiller": ("Q33999", "actor"), "komponist": ("Q36834", "composer"),
    "journalist": ("Q1930187", "journalist"), "lege": ("Q39631", "physician"), "arkitekt": ("Q42973", "architect"),
    "billedhugger": ("Q1281618", "sculptor"), "sanger": ("Q177220", "singer"), "musiker": ("Q639669", "musician"),
    "fotograf": ("Q33231", "photographer"), "ingeniør": ("Q81096", "engineer"), "advokat": ("Q40348", "lawyer"),
    "lærer": ("Q37226", "teacher"), "historiker": ("Q201788", "historian"), "lyriker": ("Q49757", "poet"),
    "oversetter": ("Q333634", "translator"), "filosof": ("Q4964182", "philosopher"), "økonom": ("Q188094", "economist"),
    "offiser": ("Q189290", "military officer"), "forretningsmann": ("Q43845", "businessperson"),
    "teolog": ("Q1234713", "theologian"), "prest": ("Q42603", "priest"), "zoolog": ("Q350979", "zoologist"),
    "botaniker": ("Q2374149", "botanist"), "geolog": ("Q520549", "geologist"), "fysiker": ("Q169470", "physicist"),
    "kjemiker": ("Q593644", "chemist"), "matematiker": ("Q170790", "mathematician"), "redaktør": ("Q1607826", "editor"),
    "jurist": ("Q185351", "jurist"), "forfattar": ("Q36180", "writer"), "ornitolog": ("Q1225716", "ornithologist"),
    "bibliotekar": ("Q182436", "librarian"), "skolemann": ("Q974144", "educator"), "skulemann": ("Q974144", "educator"),
    "sjefbibliotekar": ("Q10728547", "chief librarian"), "pressemann": ("Q1930187", "journalist"),
    "salmebokutgiver": ("Q1607826", "editor"),
}


def out(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- fetching (polite: one request per second per site)

_last = {}
_lock = threading.Lock()    # planning computes several entries at once: requests are spaced across threads
QLEVER, WDQS = "https://qlever.dev/api/wikidata", "https://query.wikidata.org/sparql"
QLEVER_GAP = 1.0            # seconds between QLever requests: it allows about one a second (and bursts of
                            # about 13), refusing the rest with 429 (measured 2026-10-15)
GAPS = {QLEVER: QLEVER_GAP, WDQS: 0.5}
SLOWER = 3.0                # seconds the Wikidata Query Service usually takes longer than QLever
PREFIXES = ("PREFIX wikibase: <http://wikiba.se/ontology#> PREFIX skos: <http://www.w3.org/2004/02/skos/core#> "
            "PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> PREFIX schema: <http://schema.org/> ")


def wait_turn(key, gap):
    """Wait until `key` (a site or query service) may be asked again: `gap` seconds after the previous request."""
    with _lock:
        at = max(time.time(), _last.get(key, 0) + gap)
        _last[key] = at
    time.sleep(max(0.0, at - time.time()))


def get(url, as_json=False):
    wait_turn(urllib.parse.urlsplit(url).netloc, 1.0)
    if as_json:
        return curl_json(url)
    import shutil
    if shutil.which("curl"):
        return subprocess.run(["curl", "-s", "-L", "--max-time", "120", "-A", UA, url], capture_output=True,
                              text=True, encoding="utf-8").stdout
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
        return r.read().decode("utf-8")


def cached(name, fetch):
    path = WORK2 / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fetch(), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def qlever(query):
    """A big query (whole identifier lists, 100 names at a time): QLever, or the Wikidata Query Service when QLever
    keeps refusing (Toolforge's address is shared with other tools)."""
    query = PREFIXES + query
    import shutil
    for attempt in range(1, 21):                    # an error page is not an empty result: wait and retry
        wait_turn(QLEVER, QLEVER_GAP)
        if shutil.which("curl"):
            text = subprocess.run(["curl", "-s", QLEVER, "--max-time", "300", "-H",
                                   "Accept: text/tab-separated-values", "--data-urlencode", "query=" + query],
                                  capture_output=True, text=True, encoding="utf-8").stdout
        else:                                       # Toolforge has no curl
            req = urllib.request.Request(QLEVER, urllib.parse.urlencode({"query": query}).encode(),
                                         {"User-Agent": UA, "Accept": "text/tab-separated-values"})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    text = r.read().decode("utf-8")
            except OSError as e:
                text = str(e)
        if text.startswith("?"):
            break
        busy = "429" in text[:400] or "Too Many Requests" in text[:400]
        if busy and attempt >= 3:
            status, other, _ = post_query(WDQS, query)
            if status == 200 and other.startswith("?"):
                text = other
                break
        time.sleep(min(15 * attempt, 120) if busy else min(10 * attempt, 120))   # QLever refills about one a second
    else:
        raise RuntimeError(f"query service failed: {text[:200]}")
    return tsv_rows(text)


def tsv_rows(text):
    rows = [line.split("\t") for line in text.strip().splitlines()[1:]]
    return [[v.strip("<>").rsplit("/", 1)[-1].strip('"') if v.startswith("<") else v.split('"')[1] if '"' in v else v
             for v in row] for row in rows]


_rest = {}                          # query service -> resting until (after it answered 429)
_refusals = {}                      # query service -> 429 answers in a row
asked = collections.Counter()       # requests per query service, and how many it refused with 429


def post_query(endpoint, query):
    """(HTTP status, body, Retry-After) of one query; status 0 when the service could not be reached."""
    import shutil
    if shutil.which("curl"):                        # the PC, whose Python has an outdated certificate store
        got = subprocess.run(["curl", "-s", "-D", "-", "-w", "\n%{http_code}", endpoint, "--max-time", "120", "-A", UA,
                              "-H", "Accept: text/tab-separated-values", "--data-urlencode", "query=" + query],
                             capture_output=True, text=True, encoding="utf-8").stdout
        body, _, code = got.rpartition("\n")
        head = ""
        while body.startswith("HTTP/"):             # header blocks (a '100 Continue' may come first), then the body
            block, _, body = body.partition("\n\n")  # (text mode has turned CRLF into LF)
            head += block
        retry_after = re.search(r"(?im)^retry-after:\s*(\d+)", head)
        return int(code) if code.isdigit() else 0, body, retry_after.group(1) if retry_after else None
    req = urllib.request.Request(endpoint, urllib.parse.urlencode({"query": query}).encode(),
                                 {"User-Agent": UA, "Accept": "text/tab-separated-values"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8"), None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Retry-After")
    except OSError as e:
        return 0, str(e), None


def lookup(query):
    """A small query (a name, an item). QLever answers in a quarter second but allows about one query a second
    (and rate-limits Toolforge's shared address); the Wikidata Query Service, which holds the same Wikidata, often
    takes seconds. So the latter is asked only when QLever's turn is more than a few seconds away, or it rests
    after a 429. A query neither service accepts is reported and counts as no match."""
    query = PREFIXES + query
    refused, text = set(), ""
    for attempt in range(1, 41):
        with _lock:
            now = time.time()
            at, endpoint = min(((max(now, _last.get(e, 0) + gap, _rest.get(e, 0)), e)
                                for e, gap in GAPS.items() if e not in refused),
                               key=lambda c: c[0] + (SLOWER if c[1] == WDQS else 0))
            _last[endpoint] = at
            asked[endpoint] += 1
        time.sleep(max(0.0, at - time.time()))
        status, text, retry_after = post_query(endpoint, query)
        if status == 200 and text.startswith("?"):
            _refusals[endpoint] = 0
            return tsv_rows(text)
        if status == 429:                           # rest longer each time in a row: 5 s, 10 s ... 2 minutes
            with _lock:
                asked[endpoint + " 429"] += 1
                _refusals[endpoint] = _refusals.get(endpoint, 0) + 1
                _rest[endpoint] = time.time() + (int(retry_after) if (retry_after or "").isdigit()
                                                 else min(5 * 2 ** (_refusals[endpoint] - 1), 120))
        elif status == 400:                         # the query itself: try the other service, then give up
            refused.add(endpoint)
            if len(refused) == len(GAPS):
                out(f"query refused by both services, counted as no match: {query[len(PREFIXES):][:300]}")
                return []
        else:
            time.sleep(min(5 * attempt, 60))        # an error page is not an empty result: wait and retry
    raise RuntimeError(f"query services failed: {text[:200]}")


def items(ids):
    found = {}
    for i in range(0, len(ids), 50):
        for attempt in range(1, 8):                  # Wikidata answers bursts of reads with an error page: wait, retry
            try:
                found.update(curl_json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
                                       "&props=info|labels|aliases|claims&ids=" + "|".join(ids[i:i + 50]))["entities"])
                break
            except (ValueError, KeyError, subprocess.CalledProcessError):
                time.sleep(20 * attempt)
        else:
            raise RuntimeError("Wikidata did not answer wbgetentities")
        time.sleep(1)
    return found


# ---------------------------------------------------------------- values

def time_value(year, month=None, day=None, julian=False):
    cal = GREGORIAN if year > 1582 and not julian else JULIAN   # Help:Dates: Julian before 1583, or when the source says so
    if day:
        return {"type": "time", "value": {"time": f"+{year:04d}-{month:02d}-{day:02d}T00:00:00Z", "timezone": 0,
                                          "before": 0, "after": 0, "precision": 11, "calendarmodel": cal}}
    return {"type": "time", "value": {"time": f"+{year:04d}-00-00T00:00:00Z", "timezone": 0, "before": 0, "after": 0,
                                      "precision": 9, "calendarmodel": cal}}


def item_value(q):
    return {"type": "wikibase-entityid", "value": entity(q)}


def string_value(s):
    return {"type": "string", "value": s}


def text_value(text, lang):
    return {"type": "monolingualtext", "value": {"text": text, "language": lang}}


def quantity_value(amount, unit):
    return {"type": "quantity", "value": {"amount": f"+{amount}", "unit": f"http://www.wikidata.org/entity/{unit}"}}


def same(a, b):
    va, vb = a.get("value"), b.get("value")
    if a.get("type") != b.get("type"):
        return False
    if a["type"] == "wikibase-entityid":
        return va.get("id") == vb.get("id")
    if a["type"] == "monolingualtext":
        return (va["text"].casefold(), va["language"]) == (vb["text"].casefold(), vb["language"])
    if a["type"] == "quantity":                      # '1648' (SNL) and '+1648.03' are the same area, rounded
        return va.get("unit") == vb.get("unit") and round(float(va["amount"])) == round(float(vb["amount"]))
    if a["type"] == "time":
        if va["precision"] != vb["precision"]:
            return False
        digits = {9: 5, 10: 8}.get(va["precision"])     # a year may be stored as 1899-00-00 or 1899-01-01
        if digits:
            return va["time"][:digits] == vb["time"][:digits]
        return (va["time"], va.get("calendarmodel")) == (vb["time"], vb.get("calendarmodel"))
    return str(va).strip().upper() == str(vb).strip().upper()


def fact(pid, value, label, qualifiers=None):
    return {"pid": pid, "value": value, "label": label, "qualifiers": qualifiers or {}}


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).replace("​", "").strip()


_places = {}


CANTON_ITEMS = {"ZH": "Q11943", "BE": "Q11911", "LU": "Q12121", "UR": "Q12404", "SZ": "Q12433", "OW": "Q12573",
                "NW": "Q12592", "GL": "Q11922", "ZG": "Q11933", "FR": "Q12640", "SO": "Q11929", "BS": "Q12172",
                "BL": "Q12146", "SH": "Q12697", "AR": "Q12079", "AI": "Q12094", "SG": "Q12746", "GR": "Q11925",
                "AG": "Q11972", "TG": "Q12713", "TI": "Q12724", "VD": "Q12771", "VS": "Q834", "NE": "Q12738",
                "GE": "Q11917", "JU": "Q12755"}


def swiss_place(raw):
    """HDS place names: 'Netstal (heute Gemeinde Glarus)' is Netstal; 'Biel (BE)' is Biel among the municipalities
    of canton Bern, where aliases count too (the item is called 'Biel/Bienne'); 'München' is a well-known city."""
    name = re.sub(r"\s*\((?:heute|ehemals|früher)\b[^)]*\)", "", clean(raw)).strip()
    m = re.fullmatch(r"(.+?)\s*\(([A-Z]{2})\)", name)
    if m and m.group(2) in CANTON_ITEMS:
        return place(m.group(1), SWISS_PLACES, CH_LANGS, "Q39", within=CANTON_ITEMS[m.group(2)])
    return place(name, SWISS_PLACES, CH_LANGS, "Q39") or world_city(name, CH_LANGS)


def norwegian_place(raw):
    """SNL place values: a link to an SNL article; 'Madrid, Spania', 'Lombard, Illinois, USA', 'Venezia (Italia)' or
    'Wroclaw i Polen': a town of that country or region; 'Aker (Oslo)' or 'Skafså i Tokke': the first place, which
    lies in the second (or is its old name, as Kristiania is Oslo's, also written 'Kristiania (nå Oslo)'); else a city,
    municipality or settlement in Norway, by name or else by old name; else a well-known city anywhere."""
    q = snl_linked(raw)
    if q:
        return q
    text = clean(raw)
    parts = [p.strip() for p in split_outside_parens("Kristiania" if text == "Kra." else text, ",")]
    now = re.fullmatch(r"(.+?)\s*\((?:nå|no):?\s+(.+)\)", parts[0])
    if now and re.search(r",|\si\s", now.group(2)):       # 'Breslau (nå: Wroclaw i Polen)': the place as named now
        parts = [p.strip() for p in split_outside_parens(now.group(2), ",")] + parts[1:]
    elif now:                                           # 'Kristiania (nå Oslo)' reads as 'Kristiania (Oslo)'
        parts[0] = f"{now.group(1)} ({now.group(2)})"
    name = parts[0]
    m = re.fullmatch(r"(.+?)\s*\((.+?)\)", name) or re.fullmatch(r"(.+?)\s+i\s+(.+)", name)
    region = region_of(parts[-1]) if len(parts) > 1 else region_of(m.group(2)) if m else None
    if len(parts) > 2:
        inner = region_of(parts[-2])
        if inner and inner[0] == "within":              # 'Lombard, Illinois, USA': within Illinois
            region = inner
    if region and "Q20" not in region[-1]:              # abroad
        return foreign_place(m.group(1) if m and len(parts) == 1 else name, region)
    if m:
        part, whole = m.group(1).strip(), m.group(2).strip()
        q = place_or_alias(part, NORWEGIAN_PLACES, NO_LANGS, "Q20")
        if q:
            return q
        within = place(whole, NORWEGIAN_PLACES, NO_LANGS, "Q20")
        if within and named(within, part):
            return within
        return place(part, (sum(NORWEGIAN_PLACES, ()),), NO_LANGS, "Q20", within=within) if within else None
    return place_or_alias(name, NORWEGIAN_PLACES, NO_LANGS, "Q20") or world_city(name, NO_LANGS)


def foreign_place(name, region):
    """A town abroad, inside the region named (a US state, England) or else of the country: a city first, then any
    settlement or municipality; the one so named (else so aliased), or the one far better known. A settlement does
    not count when a country or region has the name (Wales: not the village called Wales)."""
    within, countries = (region[1], region[2]) if region[0] == "within" else (None, region[1])
    for tiers in FOREIGN_PLACES[:1], FOREIGN_PLACES[1:]:
        q = place(name, tiers, FOREIGN_LANGS, countries, within=within) if within else \
            place_or_alias(name, tiers, FOREIGN_LANGS, countries)
        if q:
            return None if tiers == FOREIGN_PLACES[1:] and is_region(name, unless=q) else q
        if _any_candidate.get((place_name(name), tiers, within, countries, False), True):
            return None                                 # several cities of the name: unclear
    return None


def world_city(name, langs):
    """A well-known city anywhere: of a city class, with 20 Wikipedia articles and three times the next candidate;
    when no item of those classes has the name, any town with 100 articles and three times the next, as some big
    cities are typed by country only (Madrid: municipality of Spain). Not when the name is a country's (Norge)."""
    q = place(name, WORLD_CITIES, langs, None, min_links=20)
    if q or _any_candidate.get((place_name(name), WORLD_CITIES, None, None, False), True):
        return q
    q = place(name, ((SETTLEMENT,),), langs, None, min_links=100)
    return None if q and is_region(name, unless=q) else q


@functools.lru_cache(maxsize=None)
def named(q, name):
    """Whether this item has the name as a label or alias (e.g. Oslo is also called Kristiania)."""
    lits = " ".join(f'{json.dumps(name, ensure_ascii=False)}@{lang}' for lang in NO_LANGS + CH_LANGS + ("mul",))
    return bool(lookup(f"SELECT ?l WHERE {{ VALUES ?l {{ {lits} }} wd:{q} rdfs:label|skos:altLabel ?l }}"))


@functools.lru_cache(maxsize=None)
def snl_linked(raw):
    """The item an SNL metadata value links to (its first snl.no link), found through the SNL ID on Wikidata."""
    m = re.search(r'href="https?://snl\.no/([^"#?]+)"', raw or "")
    if not m:
        return None
    qs = by_id("P4342").get(urllib.parse.unquote(m.group(1)), set())
    return next(iter(qs)) if len(qs) == 1 else None


def place(name, tiers, langs, country, within=None, min_links=5, aliases=False):
    """The one item whose label (or default label) is exactly this name, trying the class tiers in order (e.g. city
    before municipality, the usual target of a place of birth); None if a tier has several or no tier has one.
    With `within` (a canton, municipality or state), aliases count as well, but only for items located in it; with
    `aliases`, they count everywhere. Without a country, only well-known places qualify (min_links Wikipedia articles
    and three times the next candidate)."""
    name = place_name(name)
    if not name:
        return None
    key = (name, tiers, within, country, aliases)
    if key not in _places:
        lits = " ".join(f'{json.dumps(name, ensure_ascii=False)}@{lang}' for lang in langs + ("mul",))
        names = "{ ?i rdfs:label ?l } UNION { ?i skos:altLabel ?l }" if within or aliases else "?i rdfs:label ?l ."
        inside = f"?i wdt:P131* wd:{within} ." if within else ""
        in_country, of_country = country_where(country)
        rows = lookup(f"SELECT DISTINCT ?i ?c ?links WHERE {{ VALUES ?l {{ {lits} }} {class_values(tiers)} "
                      f"{names} ?i wdt:P31 ?c {in_country} ; wikibase:sitelinks ?links . {inside} {of_country} }}")
        _any_candidate[key] = any_in_tiers(rows, tiers)
        _places[key] = tier_choice(rows, tiers, country, min_links)
    return _places[key]


_any_candidate = {}                                 # place() key -> whether any item of the tiers had the name


def place_or_alias(name, tiers, langs, country, min_links=5):
    """place(); when no item of the tiers has the name as its label, the one that has it as an alias (Kristiania is
    Oslo's old name)."""
    q = place(name, tiers, langs, country, min_links=min_links)
    if q or _any_candidate.get((place_name(name), tiers, None, country, False), True):
        return q
    return place(name, tiers, langs, country, min_links=min_links, aliases=True)


def place_name(name):
    name = clean(name).split(",")[0].strip()          # 'Lyngdal, Agder' -> 'Lyngdal'
    return None if not name or re.search(r"[()\d]", name) else name


def country_where(country):
    """The P17 condition, as the part after ?i wdt:P31 ?c and a clause after it: one country (Norway), or any of
    several (a country and the realm it belongs to, as Denmark and the Kingdom of Denmark)."""
    if not country:
        return "", ""
    if isinstance(country, str):
        return f"; wdt:P17 wd:{country}", ""
    return "; wdt:P17 ?k", "VALUES ?k { " + " ".join(f"wd:{c}" for c in country) + " }"


_settlements = None


def settlement_classes():
    """Every class below human settlement (Q486972) or municipality (Q15284), less the counties of the US; plus the
    municipalities of Switzerland, which sit elsewhere in the class tree."""
    global _settlements
    if _settlements is None:
        found = {r[0] for r in qlever("SELECT DISTINCT ?c WHERE { { ?c wdt:P279* wd:Q486972 } UNION "
                                      "{ ?c wdt:P279* wd:Q15284 } }")}
        found -= {r[0] for r in qlever("SELECT DISTINCT ?c WHERE { ?c wdt:P279* wd:Q47168 }")}
        _settlements = found | {"Q70208", "Q685309"}
    return _settlements


def tier_classes(classes):
    return settlement_classes() if classes == (SETTLEMENT,) else classes


def class_values(tiers):
    """VALUES for the tiers' classes; none when a tier takes every settlement (those rows are sorted out afterwards)."""
    if any(classes == (SETTLEMENT,) for classes in tiers):
        return ""
    return "VALUES ?c { " + " ".join(f"wd:{c}" for classes in tiers for c in classes) + " }"


def any_in_tiers(rows, tiers):
    return any(r[1] in tier_classes(classes) for r in rows for classes in tiers)


def tier_choice(rows, tiers, country, min_links):
    """place()'s rule on (item, class, sitelinks) rows found for all tiers at once, applied tier by tier."""
    for classes in tiers:
        inside = tier_classes(classes)
        links = {r[0]: int(r[2].split("^")[0].strip('"')) if not r[2].isdigit() else int(r[2])
                 for r in rows if r[1] in inside}
        ranked = sorted(links.items(), key=lambda kv: -kv[1])
        if (len(ranked) == 1 and (country or ranked[0][1] >= min_links)) or \
                (len(ranked) > 1 and ranked[0][1] >= min_links and ranked[0][1] >= 3 * ranked[1][1]):
            return ranked[0][0]                          # one candidate, or one far better known (a city, not its urban area)
        if ranked:
            return None                                  # several comparable candidates of the same kind: too unclear
    return None


PLAIN = re.compile(r'^[^"\\\t\n\r]+$')                   # names whose literal reads back unchanged in a list query


def prefetch_places(calls):
    """Look up many place() calls (name, tiers, langs, country, min_links, aliases, within) 100 names to a query
    instead of one each, and keep the answers as place() would. Odd names are left to place() itself."""
    groups = {}
    for name, tiers, langs, country, min_links, aliases, within in calls:
        name = place_name(name)
        if name and PLAIN.match(name) and (name, tiers, within, country, aliases) not in _places:
            groups.setdefault((tiers, langs, country, min_links, aliases, within), set()).add(name)
    for (tiers, langs, country, min_links, aliases, within), names in groups.items():
        names, (in_country, of_country) = sorted(names), country_where(country)
        labelled = "{ ?i rdfs:label ?l } UNION { ?i skos:altLabel ?l }" if aliases or within else "?i rdfs:label ?l ."
        inside = f"?i wdt:P131* wd:{within} ." if within else ""
        for i in range(0, len(names), 100):
            chunk = names[i:i + 100]
            lits = " ".join(f'{json.dumps(n, ensure_ascii=False)}@{lang}' for n in chunk for lang in langs + ("mul",))
            found = {}
            for label, *row in qlever(f"SELECT DISTINCT ?l ?i ?c ?links WHERE {{ VALUES ?l {{ {lits} }} "
                                      f"{class_values(tiers)} {labelled} ?i wdt:P31 ?c {in_country} ; "
                                      f"wikibase:sitelinks ?links . {inside} {of_country} }}"):
                found.setdefault(label, []).append(row)
            for n in chunk:
                key = (n, tiers, within, country, aliases)
                _any_candidate[key] = any_in_tiers(found.get(n, []), tiers)
                _places[key] = tier_choice(found.get(n, []), tiers, country, min_links)


COUNTRY_KINDS = ("Q6256", "Q3624078")               # country, sovereign state: towns in it name it (or its realm) in P17
REGION_KINDS = ("Q3336843", "Q35657", "Q107390", "Q10864048")   # constituent country (England), US state, federated
_regions, _region_items = {}, {}                   # state, first-level division: towns lie in it (P131)


def region_name(raw):
    """'Slovakia (den gang Habsburg-imperiet)' is Slovakia; 'Sovjetunionen (nå Russland)' and 'Tyskland (nå i
    Frankrike)' are Russia and France now."""
    now = re.search(r"\((?:nå|no):?\s+([^)]+)\)", raw)
    if now:
        return re.sub(r"^i\s+", "", now.group(1).strip())
    return re.sub(r"\s*\([^)]*\)?|\)", "", raw).strip()


def region_of(raw):
    """The country or region SNL places a town in: ('country', the countries its towns name in P17: 'Danmark' is
    Denmark or the Kingdom of Denmark), or ('within', the region its towns lie in, its countries: 'England',
    'California'). None when no present-day country or region has the name, or several have ('Georgia'); a former
    one (the Russian Empire) is not used, as towns name today's country."""
    name = region_name(raw)
    if name not in _regions:
        prefetch_regions([name])
    return _regions.get(name)


def is_region(name, unless=None):
    """Whether a country or region other than `unless` has this name (England, Wales, Norge; but Tashkent is a city
    and a region in one item)."""
    name = region_name(name)
    if name not in _regions:
        prefetch_regions([name])
    return bool(_region_items.get(name, set()) - {unless})


def prefetch_regions(names):
    names = sorted({n for n in names if n and n not in _regions and PLAIN.match(n)})
    kinds = " ".join(f"wd:{k}" for k in COUNTRY_KINDS + REGION_KINDS)
    for i in range(0, len(names), 100):
        chunk = names[i:i + 100]
        lits = " ".join(f'{json.dumps(n, ensure_ascii=False)}@{lang}' for n in chunk for lang in NO_LANGS + ("mul",))
        found = {}
        for r in qlever(f"SELECT DISTINCT ?l ?i ?k ?country ?end WHERE {{ VALUES ?l {{ {lits} }} VALUES ?k {{ {kinds} }} "
                        "?i rdfs:label ?l ; wdt:P31 ?k . OPTIONAL { ?i wdt:P17 ?country } OPTIONAL { ?i wdt:P576 ?end } }"):
            label, item, kind, country, end = r + [""] * (5 - len(r))
            _region_items.setdefault(label, set()).add(item)
            if not end:                                 # a present-day country or region
                found.setdefault(label, {}).setdefault(item, [set(), {item}])
                found[label][item][0].add(kind)
                found[label][item][1].update({country} - {""})
        for n in chunk:
            regions = found.get(n, {})
            if len(regions) != 1:
                _regions[n] = None
                continue
            item, (kinds_, countries) = next(iter(regions.items()))
            countries = tuple(sorted(countries))
            _regions[n] = ("within", item, countries) if kinds_ & set(REGION_KINDS) else ("country", countries)


# ---------------------------------------------------------------- HDS
# The HDS publishes CC0 open data (hls-dhs-dss.ch/de/opendata): article lists with the life years of every
# biography, and files linking HDS IDs with GND and VIAF. That is what the bot uses. Its article pages are guarded
# against bots, so the bot does not fetch them; pages already read are used for the richer facts of the test run.

_open = None


def hds_open():
    global _open
    if _open is None:
        d = WORK2 / "opendata"
        bio = {r["ID"].zfill(6): r for r in csv.DictReader(open(d / "liste_bio_d_utf8.csv", encoding="utf-8"))}
        names = {r["ID"].zfill(6): " ".join(x for x in (r["Lemma"], r["Complement"]) if x)
                 for fn in ("liste_fam_d_utf8.csv", "liste_geo_d_utf8.csv", "liste_tem_d_utf8.csv") if (d / fn).exists()
                 for r in csv.DictReader(open(d / fn, encoding="utf-8"))}
        links, owners = {}, {}
        for fn, pid in (("BEACON-GND-HLS.txt", "P227"), ("BEACON-VIAF-HLS.txt", "P214")):
            for line in open(d / fn, encoding="utf-8"):
                parts = line.rstrip("\n").split("|")
                m = len(parts) >= 3 and not line.startswith("#") and re.search(r"articles/(\d{6})/", parts[2])
                if m:
                    links.setdefault(m.group(1), []).append((pid, parts[0].strip()))
                    owners.setdefault((pid, parts[0].strip()), set()).add(m.group(1))
        checked = {}                                  # check_links.py: which links VIAF and the GND have retired
        if (WORK2 / "link_check.jsonl").exists():
            for line in open(WORK2 / "link_check.jsonl", encoding="utf-8"):
                r = json.loads(line)
                if not r["status"].startswith("error"):
                    checked[("P214" if r["kind"] == "viaf" else "P227", r["id"])] = r
        _open = {"bio": bio, "names": names, "links": links, "owners": owners, "checked": checked}
    return _open


_viaf, _viaf_lock = {}, threading.Lock()


def viaf_current(vid):
    """VIAF merges clusters; an abandoned one names the cluster that replaced it. None when VIAF could not be asked
    (it throttles) or the cluster is gone without a successor: then the bot does not copy the ID."""
    with _viaf_lock:                                # one request at a time, a second apart, whatever the threads
        if vid not in _viaf:
            found = None
            try:
                time.sleep(1)
                d = curl_json(f"https://viaf.org/viaf/{vid}", "-H", "Accept: application/json")
                if "ns0:abandoned_viaf_record" in d:
                    target = (d["ns0:abandoned_viaf_record"].get("ns0:redirect") or {}).get("ns0:directto")
                    found = str(target) if target else None
                elif str((d.get("ns1:VIAFCluster") or {}).get("ns1:viafID", "")) == vid:
                    found = vid
            except (ValueError, KeyError, AttributeError, RuntimeError, OSError, subprocess.CalledProcessError):
                pass
            _viaf[vid] = found
        return _viaf[vid]


def life_years(precision):
    """'1825 - 1908' -> (1825, 1908); uncertain years (marked //) are not used."""
    m = re.fullmatch(r"(\d{4}) -(?: (\d{4}))?", (precision or "").strip())
    return (int(m.group(1)), m.group(2) and int(m.group(2))) if m else (None, None)


def hds_facts(hid, kind):
    od = hds_open()
    facts, notes = [], []
    for pid, ident in od["links"].get(hid, []):
        label = "GND ID" if pid == "P227" else "VIAF ID"
        if len(od["owners"][(pid, ident)]) > 1:        # one record for several articles: which item is meant?
            notes.append(f"{label} {ident} is given to several HDS articles; not copied")
            continue
        r = od["checked"].get((pid, ident)) or {"status": "unchecked"}
        if r["status"] in ("missing", "abandoned") or r.get("undifferentiated"):
            notes.append(f"{label} {ident} " + ("is an undifferentiated name record" if r.get("undifferentiated")
                                                 else "no longer exists") + "; not copied")
            continue
        if r["status"] == "replaced":
            ident = r["now"]
        elif r["status"] != "ok" and pid == "P214":
            checked = viaf_current(ident)
            if not checked:
                notes.append(f"{label} {ident} could not be checked against VIAF; not copied")
                continue
            ident = checked
        facts.append(fact(pid, string_value(ident), label))
    page = WORK2 / "hds" / f"{hid}.html"
    t = page.read_text(encoding="utf-8") if page.exists() else ""
    bio = od["bio"].get(hid)
    title = clean((re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S) or [None, ""])[1]) if t else \
        (f"{bio['Complement']} {bio['Lemma']}".strip() if bio else od["names"].get(hid, hid))
    paras = [p for p in re.findall(r"<p[^>]*>(.*?)</p>", t, re.S) if len(clean(p)) > 40]
    first = paras[0] if paras else ""
    ch = swiss_place
    if kind == "person" and not t and bio:
        born, died = life_years(bio["Precision"])
        for year, pid, label in ((born, "P569", "year of birth"), (died, "P570", "year of death")):
            if year:
                facts.append(fact(pid, time_value(year), label))
    elif kind == "person":
        for prop, pid, span, label in (("birthDate", "P569", "hls-dnais", "date of birth"), ("deathDate", "P570", "hls-ddec", "date of death")):
            m = re.search(rf'itemprop="{prop}"[^>]*>(\d{{4}})(?:-(\d\d)-(\d\d))?<', t)
            if m:
                y, mo, d = int(m.group(1)), m.group(2) and int(m.group(2)), m.group(3) and int(m.group(3))
                f = fact(pid, time_value(y, mo, d), label)
                f["calendar_unclear"] = bool(d and y < 1813)  # used only where the item already has it from the HDS
                facts.append(f)
            m = re.search(rf'class="{span}"[^>]*>[^<]*</span>\s*([^,<]+?)\s*,', first)
            if m:
                name = re.sub(r"^\d{1,2}\.\d{1,2}\.\d{4}\s+", "", clean(m.group(1)))   # date written outside the span
                q = ch(name)
                key = "place of birth" if span == "hls-dnais" else "place of death"
                if q:
                    facts.append(fact("P19" if span == "hls-dnais" else "P20", item_value(q), key))
                else:
                    notes.append(f"{key} '{name}' not matched to one place item")
        header = clean(re.split(r"\.\s+(?:Sohn|Tochter)\b", first)[0])
        m = re.search(r",\s*von ([^.]+)$", header)
        if m:
            for name in re.split(r",\s*|\s+und\s+", m.group(1)):
                q = ch(name)
                if q:
                    facts.append(fact("P1321", item_value(q), "place of origin"))
                else:
                    notes.append(f"place of origin '{name}' not matched to one place item")
    elif kind == "family":
        m = re.search(r",\s*([^,]+)$", title)
        if m and "Familie" in title:
            q = ch(m.group(1))
            if q:
                facts.append(fact("P1321", item_value(q), "place of origin"))
            else:
                notes.append(f"place of origin '{m.group(1)}' not matched to one place item")
    elif kind == "place":
        m = re.search(r"\.\s*(\d{3,4})\s+([A-ZÄÖÜ][^.;\d]{1,60}?)\s*\.", clean(first))
        if m and int(m.group(1)) <= 1800:
            facts.append(fact("P1249", time_value(int(m.group(1))), "earliest written record",
                              {"P1810": [string_value(m.group(2).strip())]}))
    elif kind == "organisation":
        m = (re.search(r"\bgründete\s+(\d{4})\b", clean(first)) or re.search(r"\b(\d{4})\s+gegründet\b", clean(first))
             or re.search(r"\bgegründet\s+(?:im Jahr\s+)?(\d{4})\b", clean(first)))
        if m:
            facts.append(fact("P571", time_value(int(m.group(1))), "inception"))
    return title, facts, notes


# ---------------------------------------------------------------- SNL (metadata only: snl.no marks it free to reuse)

def snl_date(s, label, notes, julian=False):
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
        if y < 1700 and (d or mo) and not julian:   # '0.0.1125' is just a year, the same in both calendars
            notes.append(f"{label} {s} not used: calendar before 1700 unclear")
            return None
        return time_value(y, mo, d, julian)
    if re.fullmatch(r"\d{4}", s):
        return time_value(int(s))
    if s:
        notes.append(f"{label} '{s}' not in a clear format")
    return None


# ---------------------------------------------------------------- relatives, from SNL's family line
# 'Foreldre: Lærer William Vold (1874–1943) og lærer Helga Askevold (1875–1930).' 'Gift 14.5.1937 med Sigrid
# Wilhelmsen (9.8.1913–28.7.1992), datter av ...' 'Far til <a href="https://snl.no/X">X</a> (1939–).' A relative is
# taken only when found through its link to another SNL or NBL article, or by its exact name and its life years
# with a single candidate; father or mother follows the relative's sex on Wikidata, not the word order.

SNL_LINK = re.compile(r'<a\b[^>]*href="https?://(?:(nbl|nkl)\.)?snl\.no/([^"#?]+)"[^>]*>(.*?)</a>', re.S)
REL_UNITS = (("parents", re.compile(r"(?:Foreldre|(?:Sønn|Datter|Barn) av)\s*:?\s*")),     # capitalised: the
             ("spouse", re.compile(r"(?:Gift|\d\))\s*")),                              # subject's own family
             ("children", re.compile(r"(?:Far|Mor) til\s+", re.I)),                      # (linked only)
             ("siblings", re.compile(r"(?:Bror|Søster) (?:av|til)\s+", re.I)))
NAME_WORD = r"(?:[A-ZÆØÅÄÖÜÉÈ][\w'’-]*\.?|de|von|van|af|la|le|du|der|den|zu|di|da)"
NAME_YEARS = re.compile(rf"((?:{NAME_WORD}\s+)*[A-ZÆØÅÄÖÜÉÈ][\w'’-]+)\s*\(([^)]*)\)")
_by_id, _sex = {}, {}


def life_span(txt):
    """'1864–1946' -> (1864, 1946); '1873–89' -> (1873, 1889); 'født 1944' -> (1944, None); a year marked
    uncertain ('ca.', 'etter', '1943/44', '1470-årene') counts as unknown."""
    txt = txt.split(";")[0]
    if "født" in txt and "–" not in txt and "-" not in txt:
        m = re.search(r"\d{4}", txt)
        return (int(m.group(0)) if m else None), None
    parts = txt.split("–", 1) if "–" in txt else re.split(r"(?<=\d)\s*-\s*(?=\d)", txt, maxsplit=1)
    out = []
    for p in (parts + [""])[:2]:
        m = re.search(r"(\d{2,4})\s*$", p.strip())
        unsure = re.search(r"ca\.|etter|før|årene|/|\?", p)
        out.append(int(m.group(1)) if m and not unsure else None)
    born, died = out
    if born and born < 100:
        born = None
    if died and died < 100 and born:                  # '1873–89': the death year shortened
        died += born // 100 * 100 + (100 if died < born % 100 else 0)
    elif died and died < 100:
        died = None
    return born, died


def by_id(pid):
    if pid not in _by_id:
        found = {}
        for q, ident in qlever(f"SELECT ?i ?id WHERE {{ ?i wdt:{pid} ?id }}"):
            found.setdefault(urllib.parse.unquote(ident), set()).add(q)
        _by_id[pid] = found                         # whole, never half filled for another thread
    return _by_id[pid]


@functools.lru_cache(maxsize=None)
def person_named(name, born, died):
    """The one human item labelled exactly so whose known life years all match (at least one must be known)."""
    if not (born or died) or len(name.split()) < 2:
        return None
    if name not in _humans:
        lits = " ".join(f"{json.dumps(name)}@{lang}" for lang in HUMAN_LANGS)
        _humans[name] = lookup(f"SELECT DISTINCT ?i ?b ?d WHERE {{ VALUES ?l {{ {lits} }} ?i rdfs:label ?l ; "
                               "wdt:P31 wd:Q5 . OPTIONAL { ?i wdt:P569 ?b } OPTIONAL { ?i wdt:P570 ?d } }")
    rows = _humans[name]
    year = lambda v: int(m.group(1)) if (m := re.match(r"(-?\d{1,4})-", v or "")) else None
    years = {}
    for r in rows:
        r = r + [""] * (3 - len(r))
        years.setdefault(r[0], (set(), set()))[0].add(year(r[1]))
        years[r[0]][1].add(year(r[2]))
    fit = [q for q, (bs, ds) in years.items() if (not born or born in bs) and (not died or died in ds)]
    return fit[0] if len(fit) == 1 else None


HUMAN_LANGS = NO_LANGS + ("mul", "da", "sv", "de")
_humans = {}                                        # name -> (item, birth date, death date) rows of humans so labelled


def prefetch_humans(names):
    """Look up the humans labelled with each name, 100 names to a query, as person_named() would one by one."""
    names = sorted({n for n in names if n not in _humans and PLAIN.match(n)})
    for i in range(0, len(names), 100):
        chunk = names[i:i + 100]
        lits = " ".join(f"{json.dumps(n)}@{lang}" for n in chunk for lang in HUMAN_LANGS)
        found = {}
        for r in qlever(f"SELECT DISTINCT ?l ?i ?b ?d WHERE {{ VALUES ?l {{ {lits} }} ?i rdfs:label ?l ; wdt:P31 wd:Q5 . "
                        "OPTIONAL { ?i wdt:P569 ?b } OPTIONAL { ?i wdt:P570 ?d } }"):
            r = r + [""] * (4 - len(r))
            found.setdefault(r[0], []).append(r[1:])
        for n in chunk:
            _humans[n] = found.get(n, [])


def sex(q):
    if q not in _sex:
        rows = lookup(f"SELECT ?s WHERE {{ wd:{q} wdt:P21 ?s }}")
        _sex[q] = rows[0][0] if len(rows) == 1 else None
    return _sex[q]


REL_PIDS = {"father": "P22", "mother": "P25", "spouse": "P26", "child": "P40", "sibling": "P3373"}


def sentences(text):
    """Split at '. ' before a capital letter, outside parentheses, but not after an abbreviation ('Dr.med. Stephan',
    'St. Olav'): '... (1906–97). Gift med ...' is two sentences."""
    out, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if ch == "." and depth <= 0 and re.match(r"\s+[A-ZÆØÅ]", text[i + 1:i + 3]):
            word = re.search(r"(\S+)$", text[start:i])
            if word and (len(word.group(1)) > 3 or word.group(1)[-1] in ")0123456789") and "." not in word.group(1):
                out.append(text[start:i + 1])
                start = i + 1
    return out + [text[start:]]


def split_outside_parens(text, sep):
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == sep and depth <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def relatives(raw):
    """[(relation, item, marriage date or None)] from SNL's family line; relation is father, mother, spouse,
    child or sibling."""
    text = re.sub(r"</p>|<br\s*/?>", "\n", raw or "")
    text = SNL_LINK.sub(lambda m: f"{clean(m.group(3))}⟦{m.group(1) or 'snl'}:{m.group(2)}⟧", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    units = [u.strip() for line in text.split("\n") for s in sentences(line) for u in split_outside_parens(s, ";")
             if u.strip()]
    found, carry = [], None
    for unit in units:
        kind = next((k for k, rx in REL_UNITS if rx.match(unit)), None)
        if kind is None and carry == "parents" and unit:       # 'Datter av:' alone, the names in the next paragraph
            kind = carry
        elif kind is None:
            carry = None
            continue
        body = next((rx.sub("", unit, count=1) for k, rx in REL_UNITS if k == kind and rx.match(unit)), unit)
        if not body.strip(" :.,"):
            carry = kind
            continue
        carry = kind if kind == "spouse" else None
        when = None
        if kind == "spouse":
            body = re.split(r",\s*(?:datter|sønn|enke|enkemann|ekteskapet)\b", body)[0]
            body = re.sub(r"^\s*\d\)\s*", "", body)                  # 'Gift 1) 22.6.1877 med ...'
            m = re.match(r"\s*(?:i\s+)?(\d{1,2}\.\d{1,2}\.\d{4}|\d{4})\b", body)
            if m:
                when = m.group(1)
            parts = re.split(r"(?:^|\s)med\s", body, maxsplit=1)
            body = parts[1] if len(parts) == 2 else ""
        people = []
        for m in re.finditer(r"([^⟦⟧]*?)⟦(snl|nbl|nkl):([^⟧]+)⟧", body):
            pid = {"snl": "P4342", "nbl": "P5080", "nkl": "P5081"}[m.group(2)]
            qs = by_id(pid).get(urllib.parse.unquote(m.group(3)), set())
            if len(qs) == 1:
                people.append(next(iter(qs)))
        if kind in ("parents", "spouse"):
            plain = re.sub(rf"(?:{NAME_WORD}\s+)*[\w'’-]*⟦[^⟧]+⟧\s*(?:\([^)]*\))?", "", body)   # linked ones out
            plain = re.sub(r"\s*«[^»]*»", "", plain)
            for name, span in NAME_YEARS.findall(plain):
                born, died = life_span(span)
                words = name.split()
                q = None
                for start in range(len(words) - 1):       # a capitalised title may lead: 'Billedskjærer Claus Berg'
                    q = person_named(" ".join(words[start:]), born, died)
                    if q:
                        break
                if q:
                    people.append(q)
        if kind == "spouse":
            people = people[:1]                            # one spouse per marriage
        if kind == "parents" and not people:
            carry = "parents"                              # 'Foreldre: Dr.med.' split off: names in the next part
        for q in dict.fromkeys(people):
            if kind == "parents":
                rel = {"Q6581097": "father", "Q6581072": "mother"}.get(sex(q))
                if rel:
                    found.append((rel, q, None))
            else:
                found.append(({"spouse": "spouse", "children": "child", "siblings": "sibling"}[kind], q, when))
    return found


DOUBT = re.compile(r"omtrent|usikk|ukjent|ca\.|ikke kjent|antatt|trolig", re.I)


def lifespan_caveat(comment):
    """What SNL's note on a person's life dates means for them: 'omtrentlig fødselsår' (approximate year of birth)
    or 'usikkert dødsår' puts that date in doubt, 'fødselsår og dødsår er omtrentlige' both, a doubt naming neither
    both; 'ifølge den julianske kalender' gives Julian dates; 'fødselsdato er dåpsdato' a baptism date."""
    c = (comment or "").lower()
    doubt = bool(DOUBT.search(c))
    birth, death = "fød" in c, "død" in c
    return {"birth": doubt and (birth or not death), "death": doubt and (death or not birth),
            "julian": "juliansk" in c, "baptism": "dåpsdato" in c}


SNL_KEEP = ("title", "metadata_license_name", "metadata")     # all the bot reads; the article text is never kept


_unreadable = set()                                 # (work, slug) whose JSON could not be read: not asked again


def snl_entry(slug, work="snl"):
    if (work, slug) in _unreadable:
        raise ValueError(f"{slug}: not readable through the API")
    try:
        return json.loads(cached(f"{work}/{slug.replace('/', '_')}.json",
                                 lambda: json.dumps(read_snl(work, slug), ensure_ascii=False)))
    except (ValueError, AttributeError, subprocess.CalledProcessError):
        _unreadable.add((work, slug))
        raise


def read_snl(work, slug):
    """An entry's metadata. SNL renames articles (A._B._Guthrie is now A.B._Guthrie) and has moved NKL entries to
    snl.no: the old JSON address then answers 404 while the old page redirects, so the entry is read where the page
    leads, and 'canonical' says where that is (the reference cites it)."""
    url = lambda w, s: f"https://{SNL_HOSTS[w]}/{urllib.parse.quote(s)}.json"
    try:
        return {k: v for k, v in get(url(work, slug), as_json=True).items() if k in SNL_KEEP}
    except (ValueError, AttributeError, subprocess.CalledProcessError):
        moved = snl_moved(work, slug)
        if not moved:
            raise
        got = {k: v for k, v in get(url(*moved), as_json=True).items() if k in SNL_KEEP}
        got["canonical"] = {"work": moved[0], "slug": moved[1]}
        return got


def snl_moved(work, slug):
    """(work, slug) the entry's page now redirects to, or None."""
    page = f"https://{SNL_HOSTS[work]}/{urllib.parse.quote(slug)}"
    wait_turn(SNL_HOSTS[work], 1.0)
    import shutil
    if shutil.which("curl"):                        # the PC (its Python's certificate store is out of date)
        location = subprocess.run(["curl", "-s", "-I", "-o", os.devnull, "-w", "%{redirect_url}", "-A", UA, page],
                                  capture_output=True, text=True, encoding="utf-8").stdout.strip()
    else:
        class Stay(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            urllib.request.build_opener(Stay).open(urllib.request.Request(page, headers={"User-Agent": UA},
                                                                         method="HEAD"), timeout=60)
            location = ""
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location", "") if e.code in (301, 302, 307, 308) else ""
        except OSError:
            location = ""
    target = urllib.parse.urlsplit(urllib.parse.urljoin(page, location)) if location else None
    hosts = {host: w for w, host in SNL_HOSTS.items()}
    if not target or target.netloc not in hosts or not target.path.strip("/"):
        return None
    return hosts[target.netloc], urllib.parse.unquote(target.path.strip("/"))


NAME_PREFIX = re.compile(r"^(?:egentlig|eg\.|også kjent (?:som|under (?:kallenavnet|navnet))|kalt|født|f\.|"
                         r"[a-zæøå]+sk(?: navn)?|på [a-zæøå]+sk)\s*:?\s*", re.I)
PARTICLES = {"de", "von", "van", "af", "la", "le", "du", "der", "den", "da", "di", "y", "bin", "ibn", "al", "zu", "og"}


def other_names(raw, kind):
    """(aliases, pseudonyms) from SNL's alternative_form: one name per line or comma part, after a short lead such as
    'egentlig' or 'ungarsk'; 'Pseudonym: X' gives a pseudonym. Only strings that look like names are kept (for
    medical terms, short lower-case terms); sentences are dropped."""
    text = re.sub(r"</p>|<br\s*/?>|</div>", "\n", raw or "")
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    aliases, pseudonyms = [], []
    for line in (" ".join(l.split()) for l in text.split("\n")):
        m = re.match(r"(?:pseudonym|psevdonym)\s*:?\s*(.+)$", line, re.I)
        if m:
            pseudonyms += [p.strip(" .") for p in re.split(r",\s*|\s+og\s+", m.group(1)) if p.strip(" .")]
            continue
        line = NAME_PREFIX.sub("", line)
        for part in re.split(r",\s*(?!Jr\b|Sr\b)", line):
            part = part.strip(" .;")
            words = part.split()
            if not words or len(words) > 6 or len(part) > 80 or re.search(r"[0-9:()]", part):
                continue
            if kind == "term":
                if re.fullmatch(r"[\w' -]+", part) and len(words) <= 5:
                    aliases.append(part)
            elif all(w[0].isupper() or w.lower() in PARTICLES or re.fullmatch(r"[IVX]+\.?|Jr\.?|Sr\.?|&|A/S|AS|ASA", w)
                     for w in words) and words[0][0].isupper():
                aliases.append(part)
    return list(dict.fromkeys(aliases)), list(dict.fromkeys(pseudonyms))


def snl_facts(slug, kind, work="snl"):
    try:
        d = snl_entry(slug, work)
    except (ValueError, AttributeError, subprocess.CalledProcessError):
        return slug, [], ["entry not readable through the API"]
    facts, notes = [], []
    if d.get("metadata_license_name") != "fri":
        return d.get("title", slug), [], ["metadata not marked free to reuse"]
    m = d.get("metadata") or {}
    no = norwegian_place
    aliases, pseudonyms = other_names(m.get("alternative_form"), kind)
    facts += [fact("ALIAS", {"language": "nb", "value": a}, "other name") for a in aliases]
    if kind == "person":
        facts += [fact("P742", string_value(p), "pseudonym") for p in pseudonyms]
        for key, pid, label in (("place_of_burial", "P119", "place of burial"), ("place_of_work", "P937", "work location")):
            if (m.get(key) or "").strip():
                q = no(m[key])
                if q:
                    facts.append(fact(pid, item_value(q), label))
                else:
                    notes.append(f"{label} '{clean(m[key])}' not matched to one place item")
    if kind == "person":
        caveat = lifespan_caveat(m.get("lifespan_comment"))
        for key, pid, label in (("birth_date", "P569", "date of birth"), ("death_date", "P570", "date of death")):
            v = snl_date(m.get(key), label, notes, caveat["julian"])
            if not v:
                continue
            if caveat["birth" if pid == "P569" else "death"]:
                notes.append(f"{label} not used: SNL notes '{clean(m['lifespan_comment'])}'")
            elif pid == "P569" and caveat["baptism"]:
                facts.append(fact("P1636", v, "date of baptism"))
            else:
                facts.append(fact(pid, v, label))
        born_at = None
        for key, pid, label in (("birthplace", "P19", "place of birth"), ("place_of_death", "P20", "place of death")):
            if m.get(key):
                same = pid == "P20" and re.fullmatch(r"samme sted|same stad|sm?st\.", clean(m[key]), re.I)
                q = born_at if same else no(m[key])           # 'samme sted': where the person was born
                born_at = q if pid == "P19" else born_at
                if q:
                    facts.append(fact(pid, item_value(q), label))
                else:
                    notes.append(f"{label} '{clean(m[key])}' not matched to one place item")
        for word in re.split(r",\s*|\s+og\s+", (m.get("occupation") or "").strip().lower()):
            if word in OCCUPATIONS:
                facts.append(fact("P106", item_value(OCCUPATIONS[word][0]), "occupation"))
            elif word:
                notes.append(f"occupation '{word}' not mapped")
        if m.get("gender") in GENDER:
            facts.append(fact("P21", item_value(GENDER[m["gender"]]), "sex or gender"))
        if re.fullmatch(r"[0-9a-f-]{36}", m.get("kulturnav_id") or ""):
            facts.append(fact("P1248", string_value(m["kulturnav_id"]), "KulturNav ID"))
        if re.fullmatch(r"p[a-z]\d+", m.get("hbr_id") or ""):
            facts.append(fact("P4574", string_value(m["hbr_id"]), "historical population register ID"))
        for rel, q, when in relatives(m.get("family")):
            married = snl_date(when, "date of marriage", []) if when else None
            facts.append(fact(REL_PIDS[rel], item_value(q), rel, {"P580": [married]} if married else None))
    elif kind == "place":
        if re.fullmatch(r"[0-5]\d{3}", (m.get("municipal_number") or "").strip()):
            facts.append(fact("P2504", string_value(m["municipal_number"].strip()), "municipality number"))
        form = (m.get("language_form") or "").strip().lower()      # bokmål, nynorsk or nøytral (both)
        demonym = clean(m.get("demonym") or "")
        if re.fullmatch(r"[a-zæøåA-ZÆØÅ-]+", demonym):
            for lang in {"bokmål": ["nb"], "nynorsk": ["nn"]}.get(form, ["nb", "nn"]):
                facts.append(fact("P1549", text_value(demonym, lang), "demonym"))
        area = re.sub(r"\s", "", clean(m.get("area") or "")).replace(",", ".")
        if re.fullmatch(r"\d+(?:\.\d+)?", area):
            facts.append(fact("P2046", quantity_value(area, KM2), "area"))
        if clean(m.get("administrative_center") or ""):
            q = no(m["administrative_center"])
            if q:
                facts.append(fact("P36", item_value(q), "administrative centre"))
            else:
                notes.append(f"administrative centre '{clean(m['administrative_center'])}' not matched to one place item")
        top = re.match(r"([^(,]+?)\s*\(", clean(m.get("highest_mountain") or ""))   # 'Storskrymten (1985 moh.) på ...'
        if top:
            q = snl_linked(m["highest_mountain"]) or place(top.group(1), MOUNTAINS, NO_LANGS, "Q20")
            if q:
                facts.append(fact("P610", item_value(q), "highest point"))
            else:
                notes.append(f"highest point '{top.group(1)}' not matched to one mountain item")
    elif kind == "organisation":
        v = snl_date(m.get("incorporated"), "inception", notes)
        if v:
            facts.append(fact("P571", v, "inception"))
        v = snl_date(m.get("closed"), "dissolved", notes)
        if v:
            facts.append(fact("P576", v, "dissolved"))
        form = clean(m.get("organization_type") or "").lower()
        if form in LEGAL_FORMS:
            facts.append(fact("P1454", item_value(LEGAL_FORMS[form]), "legal form"))
        name = clean(m.get("organization_name") or "")
        if name and len(name) <= 120:
            facts.append(fact("P1448", text_value(name, "nb"), "official name"))
        if re.fullmatch(r"\d{9}", (m.get("organization_number") or "").strip()):
            facts.append(fact("P2333", string_value(m["organization_number"].strip()), "organisation number"))
        if (m.get("organization_headquarter") or "").strip():
            q = no(m["organization_headquarter"])
            if q:
                facts.append(fact("P159", item_value(q), "headquarters"))
            else:
                notes.append(f"headquarters '{clean(m['organization_headquarter'])}' not matched to one place item")
    return d.get("title", slug), facts, notes


# ---------------------------------------------------------------- one edit per item

def reference(work, ident, today):
    q, idp, _ = WORKS[work]
    ref = ref_json(q, today)
    ref["snaks"][idp] = [{"snaktype": "value", "property": idp, "datavalue": string_value(ident)}]
    ref["snaks-order"] = ["P248", idp, "P813"]
    return ref


def place_labels(live, chosen):
    """Labels of the places on the items and in the entries: a place of the same name counts as the same place
    (Drammen the town and Drammen the urban area are two items; the source goes to the one the item uses)."""
    qs = {c["mainsnak"]["datavalue"]["value"]["id"] for e in live.values() for p in PLACE_PROPS
          for c in e.get("claims", {}).get(p, []) if c["mainsnak"]["snaktype"] == "value"}
    qs |= {f["value"]["value"]["id"] for c in chosen for f in c["facts"] if f["pid"] in PLACE_PROPS}
    found = items(sorted(qs)) if qs else {}
    return {q: {v["value"] for v in e.get("labels", {}).values()} for q, e in found.items()}


PREFER_OVER_WEAK = False      # until the request says so: a differing value with no source, or only a Wikipedia
                              # import, gets the entry's value beside it at preferred rank ("best referenced value")


def entry_is_person(entry):
    """Whether the source describes a person: an HDS biography, or SNL metadata with life dates."""
    if entry["work"] == "hds":
        return entry["id"] in hds_open()["bio"]
    return any(f["pid"] in ("P569", "P570", "P21", "P4574") for f in entry["facts"])


def build(ent, entry, today, labels=None):
    """(claims for wbeditentity, list of changes, notes) for one entry and its live item."""
    work_q, idp, _ = WORKS[entry["work"]]
    cite = entry.get("cite")                           # an entry SNL has renamed or moved: cited where it is now
    ref = reference(cite["work"], cite["slug"], today) if cite else reference(entry["work"], entry["id"], today)
    claims = ent.get("claims", {})
    out_claims, done, notes = [], [], list(entry.get("notes", []))
    is_human = any(c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") == "Q5" for c in claims.get("P31", []))
    if entry_is_person(entry) != is_human:                 # e.g. a company carrying the HDS ID of its founder
        return [], [], notes + ["the entry describes " + ("a person" if not is_human else "no person")
                                + " but the item " + ("does not" if not is_human else "does") + ": the identifier is "
                                "probably on the wrong item; nothing added"]
    plain = lambda v: urllib.parse.unquote(str(v))      # the query mirror returns some IDs URL-encoded
    if not any(plain(c["mainsnak"].get("datavalue", {}).get("value")) == plain(entry["id"]) for c in claims.get(idp, [])):
        out_claims.append({"type": "statement", "rank": "normal",
                           "mainsnak": {"snaktype": "value", "property": idp, "datavalue": string_value(entry["id"])}})
        done.append(f"{entry['work'].upper()} ID")
    names = {(lang, v["value"].casefold()) for lang, v in ent.get("labels", {}).items()} | \
            {(lang, a["value"].casefold()) for lang, al in ent.get("aliases", {}).items() for a in al}
    new_names = {}
    for f in (f for f in entry["facts"] if f["pid"] == "ALIAS"):     # other names: aliases, in the same edit
        lang, name = f["value"]["language"], f["value"]["value"]
        if (lang, name.casefold()) not in names and ("mul", name.casefold()) not in names:
            new_names.setdefault(lang, []).append({"language": lang, "value": name, "add": ""})
            names.add((lang, name.casefold()))
    if new_names:
        out_claims.append({"_aliases": new_names})
        done.append("other names")
    seen = set()
    for f in (f for f in entry["facts"] if f["pid"] != "ALIAS"):
        key = (f["pid"], json.dumps(f["value"], sort_keys=True))
        if key in seen or (f["value"]["type"] == "wikibase-entityid" and f["value"]["value"]["id"] == ent.get("id")):
            continue                                       # (an item is never its own relative or place)
        seen.add(key)
        existing = [c for c in claims.get(f["pid"], []) if c["mainsnak"]["snaktype"] == "value" and c["rank"] != "deprecated"]
        equal = [c for c in existing if same(c["mainsnak"]["datavalue"], f["value"])]
        if f["value"]["type"] == "time" and not equal and any(        # a fuller date that agrees: nothing to do
                c["mainsnak"]["datavalue"]["value"]["time"][:5] == f["value"]["value"]["time"][:5]
                and c["mainsnak"]["datavalue"]["value"]["precision"] > f["value"]["value"]["precision"] for c in existing):
            continue
        if not equal and labels and f["pid"] in PLACE_PROPS:            # a place of the same name
            ours = labels.get(f["value"]["value"]["id"], set())
            equal = [c for c in existing if labels.get(c["mainsnak"]["datavalue"]["value"].get("id"), set()) & ours]
        if f.get("calendar_unclear") and not (equal and cites(equal[0], work_q)):
            notes.append(f"{f['label']} {f['value']['value']['time'][1:11]} not used: calendar before 1813 unclear")
            continue
        if equal:
            if f["pid"] not in IDS | {"P21"} and not cites(equal[0], work_q):
                c = copy.deepcopy(equal[0])
                c.setdefault("references", []).append(ref)
                out_claims.append(c)
                done.append(f"source for {f['label']}")
        elif existing and f["pid"] not in MULTI and PREFER_OVER_WEAK and f["pid"] not in IDS and all(
                not c.get("references") or only_import_refs(c) for c in existing):
            c = statement(f["pid"], f["value"], ref, rank="preferred")   # with reason: best referenced value
            out_claims.append(c)
            done.append(f"{f['label']} (preferred: the item's other value has no source)")
        elif existing and f["pid"] not in MULTI:  # several occupations or places of origin are normal
            notes.append(f"{f['label']}: item has a different value, left alone")
        else:
            c = statement(f["pid"], f["value"], ref)
            if f["qualifiers"]:
                c["qualifiers"] = {p: [{"snaktype": "value", "property": p, "datavalue": v} for v in vs] for p, vs in f["qualifiers"].items()}
                c["qualifiers-order"] = list(f["qualifiers"])
            out_claims.append(c)
            done.append(f["label"])
    return out_claims, list(dict.fromkeys(done)), notes


# ---------------------------------------------------------------- choosing the test entries

KINDS = [("hds", "person", ("Q5",), 10), ("hds", "family", ("Q8436",), 4), ("hds", "place", ("Q70208",), 5),
         ("hds", "organisation", ("Q4830453", "Q43229"), 6),
         ("snl", "person", ("Q5",), 10), ("snl", "place", ("Q755707",), 6), ("snl", "organisation", ("Q4830453", "Q43229", "Q6881511"), 8)]


def hds_read_pages(kind, classes):
    """Entries whose page the bot has already read, as candidates of this kind."""
    ids = sorted(p.stem for p in (WORK2 / "hds").glob("*.html"))
    if not ids:
        return []
    rows = qlever("SELECT ?i ?id ?c WHERE { VALUES ?id { " + " ".join(json.dumps(i) for i in ids) + " } ?i wdt:P902 ?id ; wdt:P31 ?c }")
    seen, cands = set(), []
    for q, hid, c in rows:
        if c in classes and q not in seen:
            seen.add(q)
            cands.append({"work": "hds", "kind": kind, "id": hid, "qid": q, "matched_by": "identifier"})
    return cands


def hds_unlinked_people(n):
    """HDS biographies no item carries yet, matched by exact name and both life years, with one candidate."""
    od = hds_open()
    linked = {r[0] for r in qlever("SELECT ?id WHERE { ?i wdt:P902 ?id }")}
    cands = [(hid, r) for hid, r in od["bio"].items() if hid not in linked and all(life_years(r["Precision"]))]
    random.shuffle(cands)
    found = []
    for hid, r in cands[:80]:
        if len(found) >= n:
            break
        born, died = life_years(r["Precision"])
        name = f"{r['Complement']} {r['Lemma']}".strip()
        lits = " ".join(f"{json.dumps(name)}@{lang}" for lang in ("de", "fr", "it", "en"))
        rows = qlever(f"SELECT DISTINCT ?i WHERE {{ VALUES ?l {{ {lits} }} ?i rdfs:label ?l ; wdt:P31 wd:Q5 ; wdt:P569 ?b ; "
                      f"wdt:P570 ?d . FILTER(YEAR(?b) = {born} && YEAR(?d) = {died}) MINUS {{ ?i wdt:P902 ?x }} }}")
        if len(rows) == 1:
            found.append({"work": "hds", "kind": "person", "id": hid, "qid": rows[0][0], "matched_by": "name and life years"})
    return found


def snl_unlinked_people(n):
    """SNL person articles with no item carrying their ID, matched by exact name and full date of birth."""
    found = []
    for word in ("maler", "forfatter", "politiker", "skuespiller", "komponist", "arkitekt"):
        hits = get(f"https://snl.no/api/v1/search?query={word}&limit=50", as_json=True)
        slugs = [h["permalink"] for h in hits if h.get("permalink") and h.get("encyclopedia_id") == 1]
        if not slugs:
            continue
        linked = {r[0] for r in qlever("SELECT ?id WHERE { VALUES ?id { " + " ".join(json.dumps(s) for s in slugs) + " } ?i wdt:P4342 ?id }")}
        for slug in slugs:
            if slug in linked or len(found) >= n:
                continue
            title, facts, notes = snl_facts(slug, "person")
            birth = next((f["value"]["value"] for f in facts if f["pid"] == "P569" and f["value"]["value"]["precision"] == 11), None)
            if not birth:
                continue
            rows = qlever(f'SELECT DISTINCT ?i WHERE {{ ?i wdt:P31 wd:Q5 ; rdfs:label {json.dumps(title)}@nb ; wdt:P569 ?b . '
                          f'FILTER(STR(?b) = "{birth["time"][1:]}") }}')
            if len(rows) == 1:
                found.append({"work": "snl", "kind": "person", "id": slug, "qid": rows[0][0], "matched_by": "name and date of birth"})
        if len(found) >= n:
            break
    return found


def sample():
    random.seed(20261010)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    chosen = []
    for work, kind, classes, n in KINDS:
        idp = WORKS[work][1]
        extra = " ?i wdt:P27 wd:Q20 ." if work == "snl" and kind == "person" else ""
        rows = qlever(f"SELECT ?i ?id WHERE {{ VALUES ?c {{ {' '.join('wd:' + c for c in classes)} }} ?i wdt:P31 ?c ; wdt:{idp} ?id .{extra} }} "
                      f"ORDER BY RAND() LIMIT {200 if work == 'hds' else 60}")
        cands = [{"work": work, "kind": kind, "id": urllib.parse.unquote(r[1]), "qid": r[0], "matched_by": "identifier"} for r in rows]
        if work == "hds":
            cands = hds_read_pages(kind, classes) + cands
            if kind == "person":
                cands = hds_unlinked_people(3) + cands
        if work == "snl" and kind == "person":
            try:
                cands = snl_unlinked_people(3) + cands
            except (KeyError, TypeError, ValueError) as e:      # the search API answered in an unexpected form
                out(f"SNL search for entries without an item failed: {e!r}")
        kept = 0
        live = items([c["qid"] for c in cands])
        for c in cands:
            if kept >= n:
                break
            title, facts, notes = (hds_facts if work == "hds" else snl_facts)(c["id"], kind)
            c.update(title=title, facts=facts, notes=notes)
            claims, done, _ = build(live.get(c["qid"], {}), c, today)
            if claims:
                chosen.append(c)
                kept += 1
                out(f"{work} {kind:12} {c['id']:>14} {c['qid']:>11} {title[:34]:34} {'; '.join(done)}")
        out(f"-- {work} {kind}: {kept} of {n}")
    (WORK2 / "sample.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    out(f"{len(chosen)} entries saved to {WORK2 / 'sample.json'}")


def check_occupations():
    labels = items([q for q, _ in OCCUPATIONS.values()])
    for word, (q, label) in list(OCCUPATIONS.items()):
        got = labels.get(q, {}).get("labels", {}).get("en", {}).get("value")
        if got != label:
            out(f"occupation '{word}': {q} is '{got}', not '{label}'; left out")
            del OCCUPATIONS[word]


# ---------------------------------------------------------------- saving and the report

def save(site, qid, claims, done, work, baserevid):
    from pywikibot.data import api
    from pywikibot.exceptions import APIError
    summary = f"{', '.join(done)} from {WORKS[work][2]} ({REQUEST2})"
    data = {"claims": [c for c in claims if "_aliases" not in c]}
    for c in claims:
        if "_aliases" in c:                                   # other names go into the same edit
            data["aliases"] = c["_aliases"]
    if not data["claims"]:
        del data["claims"]
    try:
        r = api.Request(site=site, parameters={"action": "wbeditentity", "id": qid, "data": json.dumps(data),
                                               "bot": 1, "baserevid": baserevid, "token": site.tokens["csrf"],
                                               "summary": summary}).submit()
        return r["entity"]["lastrevid"], None
    except APIError as e:
        return None, f"refused by Wikidata: {e.code}"


def current_notes(chosen, today):
    """What the bot still leaves alone for these entries, checked against Wikidata now."""
    live = items([c["qid"] for c in chosen])
    labels = place_labels(live, chosen)
    return [(c, n) for c in chosen for n in build(live.get(c["qid"], {}), c, today, labels)[2]]


def report(rows, notes):
    rounds, last = [], None
    for r in rows:                                # a new round starts where the revision IDs jump (a later run)
        rounds.append(1 if last is None else rounds[-1] + (r["revid"] - last > 2000))
        last = r["revid"]
    lines = [f"This page lists the test edits for [[Wikidata:Requests for permissions/Bot/OKA bot 2]], made on "
             f"{datetime.date.today():%d %B %Y}. The bot wrote it. Each edit adds or sources facts from one entry of "
             "the [[Q642074|Historical Dictionary of Switzerland]] (HDS) or [[Q746368|Store norske leksikon]] (SNL)."]
    if max(rounds) > 1:
        lines.append(f"Round 1 made {rounds.count(1)} edits. After the operator reviewed them, the matching rules were "
                     "improved and the same entries were checked again; round 2 made the remaining "
                     f"{len(rows) - rounds.count(1)} edits.")
    lines += ["", '{| class="wikitable sortable"', "! # !! Round !! Work !! Kind !! Entry !! Item !! Changes !! Diff"]
    for i, (r, rnd) in enumerate(zip(rows, rounds), 1):
        url = f"https://hls-dhs-dss.ch/de/articles/{r['id']}/" if r["work"] == "hds" else f"https://snl.no/{r['id']}"
        changes = f"{'; '.join(r['done'])}{' (matched by ' + r['matched_by'] + ')' if r['matched_by'] != 'identifier' else ''}"
        if r.get("undone"):                      # a wrong test edit, reverted by the bot: shown, not hidden
            changes = f"<s>{changes}</s> '''undone''' ([[Special:Diff/{r['undo_revid']}|diff]]): {r['undone']}"
        lines += ["|-", f"| {i} || {rnd} || {r['work'].upper()} || {r['kind']} || [{url} {r['title']}] || {{{{Q|{r['qid'][1:]}}}}} "
                        f"|| {changes} || [[Special:Diff/{r['revid']}|diff]]"]
    lines.append("|}")
    manual = json.loads((WORK2 / "manual.json").read_text(encoding="utf-8")) if (WORK2 / "manual.json").exists() else []
    if manual:
        lines += ["", "== Conflicts resolved by hand ==",
                  "Disagreements the bot found, checked against further sources and resolved by the operator's account:"]
        lines += [f"* {{{{Q|{m['qid'][1:]}}}}}: {m['change']} ([[Special:Diff/{m['revid']}|diff]])" for m in manual]
    lines += ["", "== Left alone ==", "Values the bot does not add for these entries, and why (checked when this page was written):"]
    lines += [f"* {c['work'].upper()} {c['title']} ({{{{Q|{c['qid'][1:]}}}}}): {clean(n)}" for c, n in notes] or ["* none"]
    return "\n".join(lines) + "\n"


def write_report(site=None):
    """Rewrite the report page from the run log and the current state of the items."""
    from common import bot_site
    site = site or bot_site(5)
    import pywikibot
    rows = json.loads((WORK2 / "testrun.json").read_text(encoding="utf-8"))
    chosen = json.loads((WORK2 / "sample.json").read_text(encoding="utf-8"))
    today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    page = pywikibot.Page(site, REPORT_PAGE)
    page.text = report(rows, current_notes(chosen, today))
    page.save(summary=f"Test run report for [[Wikidata:Requests for permissions/Bot/OKA bot 2]] ({len(rows)} edits)", bot=True)
    out(f"{len(rows)} test edits listed; report at {REPORT_PAGE}")


def run():
    from common import bot_site
    chosen = json.loads((WORK2 / "sample.json").read_text(encoding="utf-8"))
    site = bot_site(5)                        # writes the login settings; pywikibot may only be imported after this
    import pywikibot
    today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    rows, skipped = [], []
    live = items([c["qid"] for c in chosen])
    labels = place_labels(live, chosen)
    for c in chosen:
        ent = live.get(c["qid"], {})
        claims, done, notes = build(ent, c, today, labels)
        c["notes_out"] = notes
        if not claims:
            skipped.append(c)
            continue
        revid, err = save(site, c["qid"], claims, done, c["work"], ent.get("lastrevid"))
        if err:
            c["notes_out"].append(err)
            skipped.append(c)
        else:
            rows.append({**c, "done": done, "revid": revid})
        out(f"{c['work']} {c['kind']:12} {c['qid']:>11} {'saved ' + str(revid) if revid else err or 'no change'}: {'; '.join(done)}")
        time.sleep(5)
    log = WORK2 / "testrun.json"                  # earlier test edits stay in the report
    rows = (json.loads(log.read_text(encoding="utf-8")) if log.exists() else []) + rows
    log.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(site)


def refacts():
    """Recompute the facts of the chosen entries (after a rule change), keeping the same entries."""
    chosen = json.loads((WORK2 / "sample.json").read_text(encoding="utf-8"))
    for c in chosen:
        c["title"], c["facts"], c["notes"] = (hds_facts if c["work"] == "hds" else snl_facts)(c["id"], c["kind"])
    (WORK2 / "sample.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    out(f"facts recomputed for {len(chosen)} entries")


def preview():
    chosen = json.loads((WORK2 / "sample.json").read_text(encoding="utf-8"))
    today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    live = items([c["qid"] for c in chosen])
    labels = place_labels(live, chosen)
    for c in chosen:
        claims, done, notes = build(live.get(c["qid"], {}), c, today, labels)
        out(f"{c['work']} {c['kind']:12} {c['qid']:>11} {c['title'][:34]:34} {'; '.join(done) or 'no change'}"
            + (f"   [{' | '.join(notes)}]" if notes else ""))


def prefetch():
    """Read ahead, no edits: the SNL metadata of every entry the bot can use (people, organisations and Norwegian
    municipalities linked from Wikidata) into the cache the run reads, at one request a second; the small kinds
    first. Resumable: entries already cached are not read again."""
    seen, failed = set(), 0
    for _, kind, classes, _ in sorted((k for k in KINDS if k[0] == "snl"), key=lambda k: k[1] == "person"):
        rows = qlever(f"SELECT DISTINCT ?id WHERE {{ VALUES ?c {{ {' '.join('wd:' + c for c in classes)} }} "
                      "?i wdt:P31 ?c ; wdt:P4342 ?id }")
        slugs = [s for s in dict.fromkeys(urllib.parse.unquote(r[0]) for r in rows) if s not in seen]
        seen.update(slugs)
        todo = [s for s in slugs if not (WORK2 / "snl" / f"{s.replace('/', '_')}.json").exists()]
        out(f"snl {kind}: {len(slugs)} entries, {len(todo)} to read")
        for n, s in enumerate(todo, 1):
            try:
                snl_entry(s)
            except (ValueError, AttributeError, OSError, subprocess.CalledProcessError):
                failed += 1
            if n % 1000 == 0:
                out(f"snl {kind}: {n} of {len(todo)} read, {failed} not readable so far")
    out(f"prefetch done; {failed} entries not readable (they are tried again on the next prefetch)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    check_occupations()
    {"sample": sample, "refacts": refacts, "preview": preview, "run": run, "report": write_report,
     "prefetch": prefetch}[sys.argv[1]]()
