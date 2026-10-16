"""OKA bot, task 3 (Wikidata:Requests for permissions/Bot/OKA bot 3): the works, and the helpers their readers share.

Each reader, readers3/<work>.py, gives
  candidates(n)  -> [(entry ID, item)]: up to n entries whose ID an item carries, in a reproducible random order;
  read(ID)       -> {"title": ..., "kind": "person" | "place" | "organisation", "facts": [...], "notes": [...]},
the facts in task 2's form (dictionaries.fact), so that task 2's build() and save() make the edits: a fact is added
when the item has no value for it, an equal value gets the entry as its source, a different one is left alone and
listed. Readers read slowly, under the bot's name, and keep only facts, never text.
"""
import json
import random
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

import dictionaries as d

WORK3 = d.ROOT / "work" / "task3"
REQUEST3 = "[[Wikidata:Requests for permissions/Bot/OKA bot 3|test run]]"

# work -> (item cited as 'stated in' (P248, the property's own P9073), identifier property, name in edit summaries)
WORKS3 = {
    "ndb": ("Q1202222", "P7902", "the Deutsche Biographie"),
    "lex": ("Q136450325", "P8313", "Lex (Den Store Danske)"),
    "dkl": ("Q259197", "P7939", "Dansk kvindebiografisk leksikon"),
    "kb": ("Q4349956", "P2180", "the National Biography of Finland"),
    "skbl": ("Q50395049", "P4963", "Svenskt kvinnobiografiskt lexikon"),
    "sbl": ("Q379406", "P3217", "Svenskt biografiskt lexikon"),
    "oebl": ("Q25666", "P6194", "the Österreichisches Biographisches Lexikon"),
    "wgw": ("Q18029622", "P7842", "the Wien Geschichte Wiki"),
    "hessbio": ("Q14564246", "P13226", "the Hessische Biografie"),
    "saebi": ("Q1228663", "P1710", "the Sächsische Biografie"),
    "sbi": ("Q15975890", "P1254", "Slovenska biografija"),
    "uppslag": ("Q3503516", "P4854", "Uppslagsverket Finland"),
    "bpn": ("Q1868372", "P651", "the Biografisch Portaal van Nederland"),
    "ehlfl": ("Q59035927", "P13079", "the Historisches Lexikon des Fürstentums Liechtenstein"),
    "dodis": ("Q1234408", "P701", "Dodis"),
}
d.WORKS.update(WORKS3)                              # build(), reference() and save() look works up there

# The year from which a day-exact date of that country is read as Gregorian. Earlier day-exact dates are used only to
# source an equal value the item already has (task 2's rule): Protestant Germany, Denmark and the Dutch eastern
# provinces changed in 1700, Sweden and Finland in 1753, the Habsburg lands in 1584, Swiss Protestant cantons by 1812.
GREGORIAN_FROM = {"Q183": 1700, "Q35": 1700, "Q55": 1701, "Q34": 1753, "Q33": 1753, "Q40": 1584, "Q215": 1584,
                  "Q347": 1584, "Q39": 1813}
LANGS = {"Q183": ("de", "en"), "Q40": ("de", "en"), "Q347": ("de", "en"), "Q39": ("de", "fr", "it", "en"),
         "Q35": ("da", "en"), "Q34": ("sv", "en"), "Q33": ("fi", "sv", "en"), "Q215": ("sl", "de", "it", "en"),
         "Q55": ("nl", "en")}


def date_fact(pid, label, y, m=None, day=None, country=None):
    """A date the entry gives (a year, a month or a day) as a fact; a day before the Gregorian year of the country it
    belongs to is marked calendar_unclear (see GREGORIAN_FROM): it then only sources an equal value."""
    f = d.fact(pid, d.time_value(int(y), int(m) if m else None, int(day) if day else None), label)
    if day and int(y) < GREGORIAN_FROM.get(country, 1583):
        f["calendar_unclear"] = True
        f["calendar_note"] = f"calendar unclear before {GREGORIAN_FROM.get(country, 1583)} there"
    return f


def town(name, langs, region, given=True):
    """The one town or municipality of this name in the region, by task 2's rule abroad (the one far better known than
    the others of that name, else the one city among comparable ones; by label first, else by alias). `region` is a
    country's QID, a tuple of QIDs (Denmark and the Kingdom of Denmark), or what region_named() gives (England: towns
    within it). When the entry named no country (given=False, the region being the work's home country), a
    well-known city anywhere follows (Paris, Wien); when it named one, only towns there count. None when unclear."""
    name = (name or "").strip()
    if not name:
        return None
    if isinstance(region, (str, tuple)) and (not region or region[0] not in ("country", "within")):
        region = ("country", region)
    within, countries = (region[1], region[2]) if region[0] == "within" else (None, region[1])
    ask = lambda tiers: (d.place(name, tiers, langs, countries, within=within) if within
                         else d.place_or_alias(name, tiers, langs, countries))
    q = ask(d.FOREIGN_PLACES[1:])
    if not q and d._any_candidate.get((d.place_name(name), d.FOREIGN_PLACES[1:], within, countries, False), True):
        q = ask(d.FOREIGN_PLACES[:1])                 # several comparable towns of the name: the one city, if any
    if q and d.named_like_region(name, q):
        q = None
    return q or (None if given else d.world_city(name, langs))


_named_regions = {}


def region_named(name, langs):
    """The one present-day country or first-level region with this name in these languages: ('country', the countries
    its towns name in P17) or ('within', the region, its countries) for England, Scotland or a US state; else None."""
    key = (name, langs)
    if key not in _named_regions:
        kinds = " ".join(f"wd:{k}" for k in d.COUNTRY_KINDS + d.REGION_KINDS)
        lits = " ".join(f"{json.dumps(name, ensure_ascii=False)}@{lang}" for lang in langs + ("mul",))
        found = {}
        for r in d.lookup(f"SELECT DISTINCT ?i ?k ?country ?end WHERE {{ VALUES ?l {{ {lits} }} VALUES ?k {{ {kinds} }} "
                          "?i rdfs:label ?l ; wdt:P31 ?k . OPTIONAL { ?i wdt:P17 ?country } OPTIONAL { ?i wdt:P576 ?end } }"):
            item, kind, country, end = r + [""] * (4 - len(r))
            if not end:                               # a present-day country or region
                found.setdefault(item, [set(), {item}])
                found[item][0].add(kind)
                found[item][1].update({country} - {""})
        if len(found) != 1:
            _named_regions[key] = None
        else:
            item, (kinds_, countries) = next(iter(found.items()))
            countries = tuple(sorted(countries))
            _named_regions[key] = ("within", item, countries) if kinds_ & set(d.REGION_KINDS) else ("country", countries)
    return _named_regions[key]


def item_with(pid, value):
    """The one item carrying this identifier (a place's GND ID, a relative's entry ID), or None."""
    rows = d.lookup(f"SELECT DISTINCT ?i WHERE {{ ?i wdt:{pid} {json.dumps(value)} }}")
    return rows[0][0] if len(rows) == 1 else None


def linked(pid, n, seed=20261016, humans=True, extra=""):
    """Up to n (entry ID, item) pairs: entries whose ID exactly one item carries, in a reproducible random order."""
    cls = "?i wdt:P31 wd:Q5 ." if humans else ""
    rows = d.qlever(f"SELECT ?id (SAMPLE(?i) AS ?item) (COUNT(DISTINCT ?i) AS ?n) WHERE {{ ?i wdt:{pid} ?id . {cls} {extra} }} "
                    "GROUP BY ?id")
    one = sorted((urllib.parse.unquote(r[0]), r[1]) for r in rows if len(r) == 3 and r[2].split("^")[0].strip('"') == "1")
    many = {}
    for r in d.qlever(f"SELECT ?i (COUNT(?id) AS ?n) WHERE {{ ?i wdt:{pid} ?id . {cls} }} GROUP BY ?i HAVING (COUNT(?id) > 1)"):
        many[r[0]] = True
    one = [(i, q) for i, q in one if q not in many]       # an item with several IDs of the work: left out, as in task 2
    random.Random(seed).shuffle(one)
    return one[:n]


def cached(work, name, fetch):
    """The text fetch() returns, kept in work/task3/<work>/ so a source is read once."""
    path = WORK3 / work / re.sub(r"[^\w.-]", "_", name)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fetch(), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def polite_get(url, gap=3.0, as_json=False):
    """A page or API answer, at most one request every `gap` seconds to that site, under the bot's user agent."""
    d.wait_turn(urllib.parse.urlsplit(url).netloc, gap)
    if as_json:
        return d.curl_json(url)
    if shutil.which("curl"):                       # the PC (its Python's certificate store is out of date)
        return subprocess.run(["curl", "-s", "-L", "--max-time", "120", "-A", d.UA, url], capture_output=True,
                              text=True, encoding="utf-8").stdout
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": d.UA}), timeout=120) as r:
        return r.read().decode("utf-8")
