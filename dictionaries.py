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
import copy
import csv
import datetime
import html
import json
import random
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from common import ROOT, UA, cites, curl_json, entity, only_import_refs, ref_json, statement

WORK2 = ROOT / "work" / "task2"
REQUEST2 = "[[Wikidata:Requests for permissions/Bot/OKA bot 2|test run]]"
REPORT_PAGE = "User:OKA bot/Test run 2"
WORKS = {"hds": ("Q642074", "P902", "the Historical Dictionary of Switzerland"),
         "snl": ("Q746368", "P4342", "Store norske leksikon")}
IDS = {"P902", "P4342", "P227", "P214", "P1248", "P4574", "P2504", "P2333"}     # identifiers: compared as text
GREGORIAN = "http://www.wikidata.org/entity/Q1985727"
SWISS_PLACES = (("Q70208", "Q685309"),)         # municipality / former municipality of Switzerland
NORWEGIAN_PLACES = (("Q515", "Q1549591"), ("Q755707",),   # city, big city; municipality of Norway; then former
                    ("Q18663579", "Q15092344", "Q486972"))  # municipality, urban area in Norway, settlement
WORLD_CITIES = (("Q515", "Q1549591", "Q5119", "Q200250", "Q1637706"),)   # well-known cities anywhere
NO_LANGS, CH_LANGS = ("nb", "nn", "en"), ("de", "fr", "it", "rm")
MULTI = {"P106", "P1321"}                        # several values are normal: a new one is added beside the others
PLACE_PROPS = {"P19", "P20", "P159", "P1321"}
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


def get(url, as_json=False):
    host = urllib.parse.urlsplit(url).netloc
    time.sleep(max(0.0, 1.0 - (time.time() - _last.get(host, 0))))
    _last[host] = time.time()
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
    pfx = ("PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
           "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> ")
    for attempt in range(1, 6):                     # an error page is not an empty result: wait and retry
        r = subprocess.run(["curl", "-s", "-G", "https://qlever.dev/api/wikidata", "--max-time", "300", "-H",
                            "Accept: text/tab-separated-values", "--data-urlencode",
                            "query=PREFIX wikibase: <http://wikiba.se/ontology#> PREFIX skos: <http://www.w3.org/2004/02/skos/core#> "
                            + pfx + query],
                           capture_output=True, text=True, encoding="utf-8")
        if r.stdout.startswith("?"):
            break
        time.sleep(10 * attempt)
    else:
        raise RuntimeError(f"query service failed: {r.stdout[:200]}")
    rows = [line.split("\t") for line in r.stdout.strip().splitlines()[1:]]
    return [[v.strip("<>").rsplit("/", 1)[-1].strip('"') if v.startswith("<") else v.split('"')[1] if '"' in v else v
             for v in row] for row in rows]


def items(ids):
    found = {}
    for i in range(0, len(ids), 50):
        for attempt in range(1, 8):                  # Wikidata answers bursts of reads with an error page: wait, retry
            try:
                found.update(curl_json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
                                       "&props=labels|claims&ids=" + "|".join(ids[i:i + 50]))["entities"])
                break
            except (ValueError, KeyError, subprocess.CalledProcessError):
                time.sleep(20 * attempt)
        else:
            raise RuntimeError("Wikidata did not answer wbgetentities")
        time.sleep(1)
    return found


# ---------------------------------------------------------------- values

def time_value(year, month=None, day=None):
    if day:
        return {"type": "time", "value": {"time": f"+{year:04d}-{month:02d}-{day:02d}T00:00:00Z", "timezone": 0,
                                          "before": 0, "after": 0, "precision": 11, "calendarmodel": GREGORIAN}}
    return {"type": "time", "value": {"time": f"+{year:04d}-00-00T00:00:00Z", "timezone": 0, "before": 0, "after": 0,
                                      "precision": 9, "calendarmodel": GREGORIAN}}


def item_value(q):
    return {"type": "wikibase-entityid", "value": entity(q)}


def string_value(s):
    return {"type": "string", "value": s}


def same(a, b):
    va, vb = a.get("value"), b.get("value")
    if a.get("type") != b.get("type"):
        return False
    if a["type"] == "wikibase-entityid":
        return va.get("id") == vb.get("id")
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
    return place(name, SWISS_PLACES, CH_LANGS, "Q39") or place(name, WORLD_CITIES, CH_LANGS, None, min_links=20)


def norwegian_place(raw):
    """SNL place values: a link to an SNL article; 'Aker (Oslo)' or 'Skafså i Tokke': the first place, which lies
    in the second (or is its old name, as Kristiania is Oslo's); else a city, municipality or settlement in
    Norway; else a well-known city anywhere."""
    q = snl_linked(raw)
    if q:
        return q
    name = clean(raw).split(",")[0].strip()
    m = re.fullmatch(r"(.+?)\s*\((.+?)\)", name) or re.fullmatch(r"(.+?)\s+i\s+(.+)", name)
    if m:
        part, whole = m.group(1).strip(), m.group(2).strip()
        q = place(part, NORWEGIAN_PLACES, NO_LANGS, "Q20")
        if q:
            return q
        within = place(whole, NORWEGIAN_PLACES, NO_LANGS, "Q20")
        if within and named(within, part):
            return within
        return place(part, (sum(NORWEGIAN_PLACES, ()),), NO_LANGS, "Q20", within=within) if within else None
    return place(name, NORWEGIAN_PLACES, NO_LANGS, "Q20") or place(name, WORLD_CITIES, NO_LANGS, None, min_links=20)


def named(q, name):
    """Whether this item has the name as a label or alias (e.g. Oslo is also called Kristiania)."""
    lits = " ".join(f'"{name}"@{lang}' for lang in NO_LANGS + CH_LANGS + ("mul",))
    return bool(qlever(f"SELECT ?l WHERE {{ VALUES ?l {{ {lits} }} wd:{q} rdfs:label|skos:altLabel ?l }}"))


def snl_linked(raw):
    """The item an SNL metadata value links to (its first snl.no link), found through the SNL ID on Wikidata."""
    m = re.search(r'href="https?://snl\.no/([^"#?]+)"', raw or "")
    if not m:
        return None
    slug = urllib.parse.unquote(m.group(1))
    rows = qlever(f"SELECT DISTINCT ?i WHERE {{ VALUES ?v {{ {json.dumps(slug)} {json.dumps(urllib.parse.quote(slug))} }} ?i wdt:P4342 ?v }}")
    return rows[0][0] if len(rows) == 1 else None


def place(name, tiers, langs, country, within=None, min_links=5):
    """The one item whose label (or default label) is exactly this name, trying the class tiers in order (e.g. city
    before municipality, the usual target of a place of birth); None if a tier has several or no tier has one.
    With `within` (a canton or municipality), aliases count as well, but only for items located in it. Without a
    country, only well-known places qualify (min_links Wikipedia articles and three times the next candidate)."""
    name = clean(name).split(",")[0].strip()          # 'Lyngdal, Agder' -> 'Lyngdal'
    if not name or re.search(r"[()\d]", name):
        return None
    key = (name, tiers, within, country)
    if key not in _places:
        _places[key] = None
        lits = " ".join(f'"{name}"@{lang}' for lang in langs + ("mul",))
        names = "{ ?i rdfs:label ?l } UNION { ?i skos:altLabel ?l }" if within else "?i rdfs:label ?l ."
        inside = f"?i wdt:P131* wd:{within} ." if within else ""
        for classes in tiers:
            cls = " ".join(f"wd:{c}" for c in classes)
            in_country = f"; wdt:P17 wd:{country}" if country else ""
            rows = qlever(f"SELECT DISTINCT ?i ?links WHERE {{ VALUES ?l {{ {lits} }} VALUES ?c {{ {cls} }} {names} "
                          f"?i wdt:P31 ?c {in_country} ; wikibase:sitelinks ?links . {inside} }} ORDER BY DESC(?links)")
            links = [int(r[1].split("^")[0].strip('"')) if not r[1].isdigit() else int(r[1]) for r in rows]
            if (len(rows) == 1 and (country or links[0] >= min_links)) or \
                    (len(rows) > 1 and links[0] >= min_links and links[0] >= 3 * links[1]):
                _places[key] = rows[0][0]                # one candidate, or one far better known (a city, not its urban area)
                break
            if rows:
                break                                    # several comparable candidates of the same kind: too unclear
    return _places[key]


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
        links = {}
        for fn, pid in (("BEACON-GND-HLS.txt", "P227"), ("BEACON-VIAF-HLS.txt", "P214")):
            for line in open(d / fn, encoding="utf-8"):
                parts = line.rstrip("\n").split("|")
                m = len(parts) >= 3 and not line.startswith("#") and re.search(r"articles/(\d{6})/", parts[2])
                if m:
                    links.setdefault(m.group(1), []).append((pid, parts[0].strip()))
        _open = {"bio": bio, "names": names, "links": links}
    return _open


_viaf = {}


def viaf_current(vid):
    """VIAF merges clusters; an abandoned one names the cluster that replaced it."""
    if vid not in _viaf:
        _viaf[vid] = vid
        try:
            time.sleep(1)
            d = curl_json(f"https://viaf.org/viaf/{vid}", "-H", "Accept: application/json")
            target = d.get("ns0:abandoned_viaf_record", {}).get("ns0:redirect", {}).get("ns0:directto")
            if target:
                _viaf[vid] = str(target)
        except (ValueError, KeyError, AttributeError, RuntimeError, subprocess.CalledProcessError):
            pass
    return _viaf[vid]


def life_years(precision):
    """'1825 - 1908' -> (1825, 1908); uncertain years (marked //) are not used."""
    m = re.fullmatch(r"(\d{4}) -(?: (\d{4}))?", (precision or "").strip())
    return (int(m.group(1)), m.group(2) and int(m.group(2))) if m else (None, None)


def hds_facts(hid, kind):
    od = hds_open()
    facts, notes = [], []
    for pid, ident in od["links"].get(hid, []):
        if pid == "P214":
            ident = viaf_current(ident)
        facts.append(fact(pid, string_value(ident), "GND ID" if pid == "P227" else "VIAF ID"))
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
                    notes.append(f"{key} '{name}' not matched to one Swiss municipality")
        header = clean(re.split(r"\.\s+(?:Sohn|Tochter)\b", first)[0])
        m = re.search(r",\s*von ([^.]+)$", header)
        if m:
            for name in re.split(r",\s*|\s+und\s+", m.group(1)):
                q = ch(name)
                if q:
                    facts.append(fact("P1321", item_value(q), "place of origin"))
                else:
                    notes.append(f"place of origin '{name}' not matched to one Swiss municipality")
    elif kind == "family":
        m = re.search(r",\s*([^,]+)$", title)
        if m and "Familie" in title:
            q = ch(m.group(1))
            if q:
                facts.append(fact("P1321", item_value(q), "place of origin"))
            else:
                notes.append(f"place of origin '{m.group(1)}' not matched to one Swiss municipality")
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

def snl_date(s, label, notes):
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
        if y < 1700:
            notes.append(f"{label} {s} not used: calendar before 1700 unclear")
            return None
        return time_value(y, mo, d)
    if re.fullmatch(r"\d{4}", s):
        return time_value(int(s))
    if s:
        notes.append(f"{label} '{s}' not in a clear format")
    return None


def snl_facts(slug, kind):
    try:
        d = json.loads(cached(f"snl/{slug.replace('/', '_')}.json",
                              lambda: json.dumps(get(f"https://snl.no/{urllib.parse.quote(slug)}.json", as_json=True))))
    except (ValueError, subprocess.CalledProcessError):
        return slug, [], ["entry not readable through the API"]
    facts, notes = [], []
    if d.get("metadata_license_name") != "fri":
        return d.get("title", slug), [], ["metadata not marked free to reuse"]
    m = d.get("metadata") or {}
    no = norwegian_place
    if kind == "person":
        for key, pid, label in (("birth_date", "P569", "date of birth"), ("death_date", "P570", "date of death")):
            v = snl_date(m.get(key), label, notes)
            if v:
                facts.append(fact(pid, v, label))
        for key, pid, label in (("birthplace", "P19", "place of birth"), ("place_of_death", "P20", "place of death")):
            if m.get(key):
                q = no(m[key])
                if q:
                    facts.append(fact(pid, item_value(q), label))
                else:
                    notes.append(f"{label} '{m[key]}' not matched to one Norwegian municipality")
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
    elif kind == "place":
        if re.fullmatch(r"[0-5]\d{3}", (m.get("municipal_number") or "").strip()):
            facts.append(fact("P2504", string_value(m["municipal_number"].strip()), "municipality number"))
    elif kind == "organisation":
        v = snl_date(m.get("incorporated"), "inception", notes)
        if v:
            facts.append(fact("P571", v, "inception"))
        if re.fullmatch(r"\d{9}", (m.get("organization_number") or "").strip()):
            facts.append(fact("P2333", string_value(m["organization_number"].strip()), "organisation number"))
        if (m.get("organization_headquarter") or "").strip():
            q = no(m["organization_headquarter"])
            if q:
                facts.append(fact("P159", item_value(q), "headquarters"))
            else:
                notes.append(f"headquarters '{m['organization_headquarter'].strip()}' not matched to one Norwegian municipality")
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
    ref = reference(entry["work"], entry["id"], today)
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
        done.append(f"{'HDS' if entry['work'] == 'hds' else 'SNL'} ID")
    seen = set()
    for f in entry["facts"]:
        key = (f["pid"], json.dumps(f["value"], sort_keys=True))
        if key in seen:
            continue
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
    try:
        r = api.Request(site=site, parameters={"action": "wbeditentity", "id": qid, "data": json.dumps({"claims": claims}),
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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    check_occupations()
    {"sample": sample, "refacts": refacts, "preview": preview, "run": run, "report": write_report}[sys.argv[1]]()
