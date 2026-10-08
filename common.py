"""Shared constants and helpers for OKA bot's swisstopo task on Wikidata."""
import csv
import io
import json
import math
import os
import sqlite3
import struct
import subprocess
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
UA = "OKA-bot/1.0 (https://www.wikidata.org/wiki/User:OKA_bot; operator User:7804j)"
REQUEST = "[[Wikidata:Requests for permissions/Bot/OKA bot|swisstopo task]]"
STAC = "https://data.geo.admin.ch/api/stac/v0.9/collections"
QLEVER = "https://qlever.dev/api/wikidata"

SWISSNAMES3D, SWISSBOUNDARIES3D = "Q141593662", "Q141599379"   # dataset items cited in references
METRE = "http://www.wikidata.org/entity/Q11573"
BEST_REFERENCED = "Q98386534"                                  # "best referenced value" (reason for preferred rank)
IMPORT_ONLY = {"P143", "P813", "P4656"}                         # imported from a Wikimedia project / retrieved / import URL

# Wikidata class -> feature group, and swissNAMES3D object type -> feature group
CLASSES = {"Q8502": "summit", "Q207326": "summit", "Q54050": "summit", "Q133056": "pass",
           "Q23397": "lake", "Q131681": "lake", "Q35666": "glacier"}
RESERVOIR = "Q131681"
TYPES = {"Hauptgipfel": "summit", "Gipfel": "summit", "Alpiner Gipfel": "summit", "Huegel": "summit",
         "Haupthuegel": "summit", "Felskopf": "summit", "Pass": "pass", "Strassenpass": "pass",
         "See": "lake", "Gletscher": "glacier"}
RADIUS = {"summit": 250, "pass": 250, "lake": 2000, "glacier": 2000}        # matching distance in metres
NEW_CLASS = {"See": "Q23397", "Gletscher": "Q35666", "Pass": "Q133056", "Strassenpass": "Q133056",
             "Hauptgipfel": "Q8502", "Alpiner Gipfel": "Q8502", "Haupthuegel": "Q54050"}
LANG = {"Hochdeutsch inkl. Lokalsprachen": "de", "Franzoesisch inkl. Lokalsprachen": "fr",
        "Italienisch inkl. Lokalsprachen": "it", "Rumantsch Grischun inkl. Lokalsprachen": "rm"}
OFFICIAL_TYPES = ("einfacher Name", "Endonym")
NAME_LANGS = ("de", "de-ch", "gsw", "fr", "frp", "it", "lmo", "rm", "en", "ceb", "sv", "nl")

# Descriptions for new items: "[type] in [canton], [country]" (Help:Description); generic wording on a canton border
CANTON_CODES = ["ZH", "BE", "LU", "UR", "SZ", "OW", "NW", "GL", "ZG", "FR", "SO", "BS", "BL", "SH", "AR", "AI",
                "SG", "GR", "AG", "TG", "TI", "VD", "VS", "NE", "GE", "JU"]             # BFS canton numbers 1-26
CANTONS = {  # en, de, fr preposition, fr, it
    "ZH": ("Zurich", "Zürich", "de ", "Zurich", "Zurigo"), "BE": ("Bern", "Bern", "de ", "Berne", "Berna"),
    "LU": ("Lucerne", "Luzern", "de ", "Lucerne", "Lucerna"), "UR": ("Uri", "Uri", "d'", "Uri", "Uri"),
    "SZ": ("Schwyz", "Schwyz", "de ", "Schwytz", "Svitto"), "OW": ("Obwalden", "Obwalden", "d'", "Obwald", "Obvaldo"),
    "NW": ("Nidwalden", "Nidwalden", "de ", "Nidwald", "Nidvaldo"), "GL": ("Glarus", "Glarus", "de ", "Glaris", "Glarona"),
    "ZG": ("Zug", "Zug", "de ", "Zoug", "Zugo"), "FR": ("Fribourg", "Freiburg", "de ", "Fribourg", "Friburgo"),
    "SO": ("Solothurn", "Solothurn", "de ", "Soleure", "Soletta"), "BS": ("Basel-Stadt", "Basel-Stadt", "de ", "Bâle-Ville", "Basilea Città"),
    "BL": ("Basel-Landschaft", "Basel-Landschaft", "de ", "Bâle-Campagne", "Basilea Campagna"),
    "SH": ("Schaffhausen", "Schaffhausen", "de ", "Schaffhouse", "Sciaffusa"),
    "AR": ("Appenzell Ausserrhoden", "Appenzell Ausserrhoden", "d'", "Appenzell Rhodes-Extérieures", "Appenzello Esterno"),
    "AI": ("Appenzell Innerrhoden", "Appenzell Innerrhoden", "d'", "Appenzell Rhodes-Intérieures", "Appenzello Interno"),
    "SG": ("St. Gallen", "St. Gallen", "de ", "Saint-Gall", "San Gallo"), "GR": ("Graubünden", "Graubünden", "des ", "Grisons", "Grigioni"),
    "AG": ("Aargau", "Aargau", "d'", "Argovie", "Argovia"), "TG": ("Thurgau", "Thurgau", "de ", "Thurgovie", "Turgovia"),
    "TI": ("Ticino", "Tessin", "du ", "Tessin", "Ticino"), "VD": ("Vaud", "Waadt", "de ", "Vaud", "Vaud"),
    "VS": ("Valais", "Wallis", "du ", "Valais", "Vallese"), "NE": ("Neuchâtel", "Neuenburg", "de ", "Neuchâtel", "Neuchâtel"),
    "GE": ("Geneva", "Genf", "de ", "Genève", "Ginevra"), "JU": ("Jura", "Jura", "du ", "Jura", "Giura"),
}
TYPE_WORDS = {"Q23397": ("lake", "See", "lac", "lago"), "Q35666": ("glacier", "Gletscher", "glacier", "ghiacciaio"),
              "Q133056": ("mountain pass", "Gebirgspass", "col", "valico"), "Q8502": ("mountain", "Berg", "montagne", "montagna"),
              "Q54050": ("hill", "Hügel", "colline", "collina")}
GENERIC = {"Q23397": ("lake in Switzerland", "See in der Schweiz", "lac suisse", "lago svizzero"),
           "Q35666": ("glacier in Switzerland", "Gletscher in der Schweiz", "glacier suisse", "ghiacciaio svizzero"),
           "Q133056": ("mountain pass in Switzerland", "Gebirgspass in der Schweiz", "col suisse", "valico svizzero"),
           "Q8502": ("mountain in Switzerland", "Berg in der Schweiz", "montagne suisse", "montagna svizzera"),
           "Q54050": ("hill in Switzerland", "Hügel in der Schweiz", "colline suisse", "collina svizzera")}


def out(*parts):
    print(*parts, flush=True)


# ---------------------------------------------------------------- geography

def wgs84_to_lv95(lat, lon):
    """swisstopo's approximate formulas (about 1 m accuracy)."""
    p, l = (lat * 3600 - 169028.66) / 10000, (lon * 3600 - 26782.5) / 10000
    return (2600072.37 + 211455.93 * l - 10938.51 * l * p - 0.36 * l * p ** 2 - 44.54 * l ** 3,
            1200147.07 + 308807.95 * p + 3745.25 * l ** 2 + 76.63 * p ** 2 - 194.56 * l ** 2 * p + 119.79 * p ** 3)


def lv95_to_wgs84(e, n):
    y, x = (e - 2600000) / 1e6, (n - 1200000) / 1e6
    lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x ** 2 - 0.0436 * y ** 3
    lat = 16.9023892 + 3.238272 * x - 0.270978 * y ** 2 - 0.002528 * x ** 2 - 0.0447 * y ** 2 * x - 0.0140 * x ** 3
    return round(lat * 100 / 36, 5), round(lon * 100 / 36, 5)


def dist_m(lat1, lon1, lat2, lon2):
    k = math.pi / 180
    a = math.sin((lat2 - lat1) * k / 2) ** 2 + math.cos(lat1 * k) * math.cos(lat2 * k) * math.sin((lon2 - lon1) * k / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- downloads (latest release from swisstopo's STAC API)

def curl_json(url, *extra):
    return json.loads(subprocess.run(["curl", "-s", "-S", "-L", "--max-time", "600", "-A", UA, *extra, url],
                                     capture_output=True, check=True).stdout)


def latest_asset(collection, suffix):
    items = curl_json(f"{STAC}/{collection}/items?limit=100")["features"]
    item = max(items, key=lambda f: f["properties"].get("datetime") or f["id"])
    href = next(a["href"] for k, a in item["assets"].items() if k.endswith(suffix))
    return item["id"], href


def fetch_dataset(collection, suffix):
    """Download and unpack the latest release once; returns its folder."""
    release, href = latest_asset(collection, suffix)
    folder = WORK / release
    if not folder.exists():
        folder.mkdir(parents=True)
        zpath = folder / href.rsplit("/", 1)[1]
        subprocess.run(["curl", "-s", "-S", "-L", "-A", UA, "-o", str(zpath), href], check=True)
        zipfile.ZipFile(zpath).extractall(folder)
        zpath.unlink()
    return folder


# ---------------------------------------------------------------- swissBOUNDARIES3D: municipality and canton of a point

def _wkb_rings(buf, off):
    """Parse a (Multi)Polygon WKB with optional Z/M; returns (list of polygons as lists of rings, new offset)."""
    bo = "<" if buf[off] == 1 else ">"
    gtype = struct.unpack(bo + "I", buf[off + 1:off + 5])[0]
    off += 5
    has_z = gtype in (1003, 1006) or bool(gtype & 0x80000000) or gtype in (3003, 3006)
    has_m = gtype in (2003, 2006, 3003, 3006) or bool(gtype & 0x40000000)
    base = gtype & 0xFFFF
    base = base % 1000 if base > 1000 else base
    dims = 2 + has_z + has_m
    if base == 6:
        n = struct.unpack(bo + "I", buf[off:off + 4])[0]
        off += 4
        polys = []
        for _ in range(n):
            sub, off = _wkb_rings(buf, off)
            polys += sub
        return polys, off
    nrings = struct.unpack(bo + "I", buf[off:off + 4])[0]
    off += 4
    rings = []
    for _ in range(nrings):
        npts = struct.unpack(bo + "I", buf[off:off + 4])[0]
        off += 4
        vals = struct.unpack(bo + "d" * (npts * dims), buf[off:off + 8 * npts * dims])
        off += 8 * npts * dims
        rings.append([(vals[i], vals[i + 1]) for i in range(0, len(vals), dims)])
    return [rings], off


def _gpkg_polygons(blob):
    flags = blob[3]
    env = (flags >> 1) & 7
    off = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    return _wkb_rings(blob, off)[0]


def _inside(rings, x, y):
    def ring_has(ring):
        c, j = False, len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                c = not c
            j = i
        return c
    return ring_has(rings[0]) and not any(ring_has(h) for h in rings[1:])


class Boundaries:
    """Current municipalities (BFS number, canton) from the swissBOUNDARIES3D GeoPackage, for local point lookups."""

    def __init__(self):
        folder = fetch_dataset("ch.swisstopo.swissboundaries3d", ".gpkg.zip")
        gpkg = next(folder.rglob("*.gpkg"))
        con = sqlite3.connect(str(gpkg))
        self.munis = []
        for blob, bfs, kanton, art, icc in con.execute(
                "select geom, bfs_nummer, kantonsnummer, objektart, icc from tlm_hoheitsgebiet"):
            if art != "Gemeindegebiet" or not bfs or not kanton or icc != "CH":   # skips Liechtenstein and enclaves
                continue
            polys = _gpkg_polygons(blob)
            xs = [p[0] for poly in polys for p in poly[0]]
            ys = [p[1] for poly in polys for p in poly[0]]
            self.munis.append((min(xs), min(ys), max(xs), max(ys), polys, int(bfs), int(kanton)))

    def at(self, e, n):
        """[(bfs, canton code)] of the municipalities containing the LV95 point (more than one on a boundary)."""
        hits = []
        for x0, y0, x1, y1, polys, bfs, kanton in self.munis:
            if x0 <= e <= x1 and y0 <= n <= y1 and any(_inside(p, e, n) for p in polys):
                hits.append((bfs, CANTON_CODES[kanton - 1]))
        return hits


# ---------------------------------------------------------------- swissNAMES3D

def swissnames3d():
    """Records of the feature types the task covers, plus every official/common name of each object."""
    folder = fetch_dataset("ch.swisstopo.swissnames3d", ".csv.zip")
    recs, names_of = [], defaultdict(list)
    for fn in ("swissNAMES3D_PKT.csv", "swissNAMES3D_PLY.csv"):
        with open(next(folder.rglob(fn)), encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                lang = LANG.get(r["SPRACHCODE"])
                if lang and r["STATUS"] in ("offiziell", "ueblich") and r["NAMEN_TYP"] in ("einfacher Name", "Endonym", "Exonym"):
                    entry = {"lang": lang, "name": r["NAME"], "status": r["STATUS"], "ntype": r["NAMEN_TYP"]}
                    if entry not in names_of[r["UUID"]]:
                        names_of[r["UUID"]].append(entry)
                if r["OBJEKTART"] in TYPES:
                    recs.append({"uuid": r["UUID"], "name": r["NAME"], "type": r["OBJEKTART"], "group": TYPES[r["OBJEKTART"]],
                                 "e": float(r["E"]), "n": float(r["N"]), "z": int(float(r["Z"])), "status": r["STATUS"],
                                 "lang": lang, "ntype": r["NAMEN_TYP"]})
    for rec in recs:
        rec["names"] = names_of.get(rec["uuid"], [])
    return recs, folder.name


# ---------------------------------------------------------------- Wikidata snapshot (QLever), cached per day

PFX = """PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/> PREFIX psv: <http://www.wikidata.org/prop/statement/value/>
PREFIX pr: <http://www.wikidata.org/prop/reference/> PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX wikibase: <http://wikiba.se/ontology#> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#> """


def sparql_tsv(query, cache):
    path = WORK / "wikidata" / time.strftime("%Y-%m-%d") / cache
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = subprocess.run(["curl", "-s", "-S", "-L", "--max-time", "600", "-A", UA, "-H", "Accept: text/tab-separated-values",
                              "-G", "--data-urlencode", "query=" + PFX + query, QLEVER], capture_output=True, check=True).stdout
        if raw.lstrip().startswith(b"{"):
            sys.exit(f"{cache}: query failed: {raw[:300]!r}")
        path.write_bytes(raw)
    return list(csv.reader(io.StringIO(path.read_text(encoding="utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE))[1:]


def qid(v):
    return v.strip("<>").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def literal(v):
    """'"text"@de' -> ('text', 'de'); '"1.5"^^<...>' -> ('1.5', None)."""
    if v.startswith('"'):
        end = v.rfind('"')
        text = v[1:end]
        if "\\" in text:
            text = text.encode("latin-1", "backslashreplace").decode("unicode_escape")
        rest = v[end + 1:]
        return text, rest[1:] if rest.startswith("@") else None
    return v, None


def point(v):
    nums = v.split("(", 1)[1].split(")", 1)[0].split()
    return wgs84_to_lv95(float(nums[1]), float(nums[0]))


# ---------------------------------------------------------------- statements

def entity(q):
    return {"entity-type": "item", "numeric-id": int(q[1:]), "id": q}


def ref_json(dataset, today):
    return {"snaks": {"P248": [{"snaktype": "value", "property": "P248", "datavalue": {"type": "wikibase-entityid", "value": entity(dataset)}}],
                      "P813": [{"snaktype": "value", "property": "P813", "datavalue": {"type": "time", "value": {
                          "time": today, "timezone": 0, "before": 0, "after": 0, "precision": 11,
                          "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}}}]},
            "snaks-order": ["P248", "P813"]}


def statement(pid, datavalue, ref, rank="normal"):
    c = {"type": "statement", "rank": rank, "references": [ref],
         "mainsnak": {"snaktype": "value", "property": pid, "datavalue": datavalue}}
    if rank == "preferred":
        c["qualifiers"] = {"P7452": [{"snaktype": "value", "property": "P7452",
                                      "datavalue": {"type": "wikibase-entityid", "value": entity(BEST_REFERENCED)}}]}
        c["qualifiers-order"] = ["P7452"]
    return c


def item_claim(pid, q, ref):
    return statement(pid, {"type": "wikibase-entityid", "value": entity(q)}, ref)


def height_claim(z, ref, rank="normal"):
    return statement("P2044", {"type": "quantity", "value": {"amount": f"+{z}", "unit": METRE}}, ref, rank)


def coord_claim(lat, lon, ref, rank="normal"):
    return statement("P625", {"type": "globecoordinate", "value": {"latitude": lat, "longitude": lon, "altitude": None,
                                                                   "precision": 1e-05, "globe": "http://www.wikidata.org/entity/Q2"}}, ref, rank)


def mono_claim(pid, text, lang, ref):
    return statement(pid, {"type": "monolingualtext", "value": {"text": text, "language": lang}}, ref)


def cites(claim, dataset):
    return any(s.get("datavalue", {}).get("value", {}).get("id") == dataset
               for r in claim.get("references", []) for s in r["snaks"].get("P248", []))


def only_import_refs(claim):
    return all(set(r["snaks"]) <= IMPORT_ONLY for r in claim.get("references", []))


# ---------------------------------------------------------------- bot session (Special:BotPasswords pair from the user environment)

def bot_site(throttle):
    import shutil
    import tempfile
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        login = winreg.QueryValueEx(key, "COMMONS_BOT_USER")[0]
        password = winreg.QueryValueEx(key, "COMMONS_BOT_PASSWORD")[0]
    if "@" not in login or len(password) != 32:
        sys.exit("COMMONS_BOT_USER/COMMONS_BOT_PASSWORD are not a Special:BotPasswords pair; refusing to log in.")
    user, bp_name = login.split("@", 1)
    workdir = tempfile.mkdtemp(prefix="pwb-")
    with open(os.path.join(workdir, "user-config.py"), "w", encoding="utf-8") as f:
        f.write(f"family = 'wikidata'\nmylang = 'wikidata'\nusernames['wikidata']['wikidata'] = {user!r}\n"
                f"password_file = 'user-password.py'\nput_throttle = {throttle}\nmaxlag = 5\n"
                "max_retries = 200\nretry_wait = 10\nretry_max = 120\n"
                f"user_agent_description = {UA!r}\n")
    with open(os.path.join(workdir, "user-password.py"), "w", encoding="utf-8") as f:
        f.write(f"({user!r}, BotPassword({bp_name!r}, {password!r}))\n")
    os.environ["PYWIKIBOT_DIR"] = workdir
    import atexit
    atexit.register(shutil.rmtree, workdir, True)
    import pywikibot
    site = pywikibot.Site("wikidata", "wikidata")
    site.login()
    if site.username() != user:
        sys.exit(f"logged in as {site.username()!r}, expected {user!r}")
    return site
