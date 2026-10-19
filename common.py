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
KM2, HECTARE, M2 = ("http://www.wikidata.org/entity/" + q for q in ("Q712226", "Q35852", "Q25343"))
BEST_REFERENCED = "Q98386534"                                  # "best referenced value" (reason for preferred rank)
MOST_RECENT = "Q71533355"                                      # "most recent value" (reason for preferred rank)
IMPORT_ONLY = {"P143", "P813", "P4656"}                         # imported from a Wikimedia project / retrieved / import URL

# Wikidata class -> feature group, and swissNAMES3D object type -> feature group
CLASSES = {"Q8502": "summit", "Q207326": "summit", "Q54050": "summit", "Q133056": "pass",
           "Q23397": "lake", "Q131681": "lake", "Q35666": "glacier"}
RESERVOIR = "Q131681"
TYPES = {"Hauptgipfel": "summit", "Gipfel": "summit", "Alpiner Gipfel": "summit", "Huegel": "summit",
         "Haupthuegel": "summit", "Felskopf": "summit", "Pass": "pass", "Strassenpass": "pass",
         "See": "lake", "Gletscher": "glacier"}
RADIUS = {"summit": 250, "pass": 250, "lake": 2000, "glacier": 2000,         # matching distance in metres
          "valley": 2000, "ridge": 2000, "waterfall": 250, "cave": 250}
NEW_CLASS = {"See": "Q23397", "Gletscher": "Q35666", "Pass": "Q133056", "Strassenpass": "Q133056",
             "Hauptgipfel": "Q8502", "Alpiner Gipfel": "Q8502", "Haupthuegel": "Q54050"}

# Second batch, "natural": valleys, ridges, waterfalls and caves (CSV points of valleys and ridges are their polygon's
# centroid, hence the larger radius), plus new items for the minor summit types, which the first batch only edits. Its
# scope also takes in the classes above, so that the creation checks see every summit, lake, glacier and pass item.
NATURAL_CLASSES = {"Q39816": "valley", "Q649266": "valley", "Q1006357": "valley", "Q594419": "valley", "Q2490191": "valley",
                   "Q740445": "ridge", "Q1334383": "ridge", "Q1400565": "ridge", "Q34038": "waterfall",
                   "Q35509": "cave", "Q2232001": "cave", "Q7558985": "cave", "Q1317637": "cave", "Q2455463": "cave",
                   "Q1149405": "cave", "Q1131329": "cave", "Q30754717": "cave",
                   "Q3618634": "summit", "Q111177881": "summit"}    # subpeak, main peak: for the creation checks only
ALL_CLASSES = {**CLASSES, **NATURAL_CLASSES}
NATURAL_TYPES = {"Tal": "valley", "Grat": "ridge", "Wasserfall": "waterfall", "Grotte, Hoehle": "cave"}
NATURAL_NEW_CLASS = {"Tal": "Q39816", "Grat": "Q740445", "Wasserfall": "Q34038",
                     "Gipfel": "Q207326", "Huegel": "Q54050", "Felskopf": "Q207326"}
# "Grotte, Hoehle" is not created: swisstopo's type also covers mine entrances, Lourdes grottoes and natural arches.
# Before this batch creates an item, an existing item of the same kind and name this close is a duplicate too (on top
# of the 2 km for any item): valley and ridge points are polygon centroids, several km from where the item may sit (a
# Tal is under 20 km long); waterfall items often sit on their stream; some summit items (e.g. GeoNames imports) are
# a few km off. Items tied to another swisstopo feature (paired in the plan, or with a swissNAMES3D position when the
# runner checks live) only count within 2 km.
NATURAL_SAME_KIND_NAME = {"valley": 10000, "ridge": 10000, "waterfall": 10000, "summit": 5000, "cave": 5000}

# Batches: each has its own plan file. Rows of a later batch carry "batch" and are logged as "edit:<batch>" and
# "create:<batch>", so an item done in one batch is still checked by the next; the first batch ("") keeps plan.jsonl
# and the plain kinds "edit" and "create".
# "ch_only": create only features whose point lies in a Swiss municipality (swissNAMES3D also covers Liechtenstein,
# and border features can have their point just across the border).
BATCHES = {
    "": {"plan": "plan.jsonl", "cache": "", "classes": CLASSES, "types": TYPES, "edit": set(TYPES), "new": NEW_CLASS,
         "same_kind_name": {}, "ch_only": False,
         "covers": ["mountains", "summits", "hills", "passes", "lakes", "reservoirs", "glaciers"]},
    "natural": {"plan": "plan_natural.jsonl", "cache": "natural_", "classes": ALL_CLASSES,
                "types": {**TYPES, **NATURAL_TYPES}, "edit": set(NATURAL_TYPES), "new": NATURAL_NEW_CLASS,
                "same_kind_name": NATURAL_SAME_KIND_NAME, "ch_only": True,
                "covers": ["valleys", "ridges", "waterfalls", "caves"],
                "note": "Valleys, ridges, waterfalls and caves get no heights, and no new items are made for caves."},
}
# Third batch, "settlements": localities (Ort) and urban neighbourhoods (Quartier). Items of these classes are paired;
# current municipalities are not (a municipality is not "located in" itself), former ones are. New items only for
# localities of 100 to 9,999 inhabitants (bigger ones have items) and for neighbourhoods; same-named villages far
# apart are normal (Oberdorf), so only 3 km count for a duplicate of the same kind.
SETTLEMENT_CLASSES = {"Q486972": "settlement", "Q532": "settlement", "Q5084": "settlement", "Q11183787": "settlement",
                      "Q3957": "settlement", "Q515": "settlement", "Q685309": "settlement",
                      "Q123705": "neighbourhood", "Q2983893": "neighbourhood"}
BATCHES["settlements"] = {
    "plan": "plan_settlements.jsonl", "cache": "settlements_", "classes": SETTLEMENT_CLASSES,
    "types": {"Ort": "settlement", "Quartier": "neighbourhood"}, "edit": {"Ort", "Quartier"},
    "new": {"Ort": "Q532", "Quartier": "Q123705"}, "create_pop": {"100 bis 999", "1'000 bis 1'999", "2'000 bis 9'999"},
    "same_kind_name": {"settlement": 3000, "neighbourhood": 3000}, "ch_only": True,
    "covers": ["localities", "neighbourhoods"],
    "note": "Localities and neighbourhoods get no heights; new items only for localities of 100 to 9,999 inhabitants."}
RADIUS.update({"settlement": 1500, "neighbourhood": 1000})
# Fourth batch, "p131": Swiss items with a position but no municipality get the one swissBOUNDARIES3D places their
# position in. Point features only: classes under built structure, archaeological site, settlement, fountain, water
# well, mountain, pass or hill; not waters, glaciers, protected areas, regions, roads, railway lines, bridges or
# tunnels (they span municipalities). Nothing else changes on these items.
P131_ROOTS = ("Q811979", "Q839954", "Q486972", "Q483453", "Q43483", "Q8502", "Q133056", "Q54050")
P131_NOT = ("Q15324", "Q35666", "Q473972", "Q355304", "Q56061", "Q82794", "Q1620908", "Q34442", "Q79007",
            "Q728937", "Q12280", "Q44377", "Q34379", "Q3325736")   # ...musical instruments, artist-run initiatives
BATCHES["p131"] = {"plan": "plan_p131.jsonl", "cache": "p131_", "covers": ["buildings and other located items"],
                   "note": "Buildings and other located items get only their municipality, from swissBOUNDARIES3D."}
# Fifth batch, "stations": railway stations and other public transport stops, paired with swissNAMES3D's stops
# ("Haltestelle Bahn" also covers tram and metro stops; "Uebrige Bahnen" are the stops of cable cars and funiculars).
# Edits only, and only three things: swisstopo as a source for a position within 50 m, the official name, and the
# municipality. No labels: station labels follow their own conventions ("Bahnhof X", "X railway station", "gare de X").
# A stop is often several swissNAMES3D points (one per platform or direction): points of the same type and name within
# STATION_LINK metres are one stop, and an item and a stop are paired only when each is the other's only candidate.
# Car loading stations ("Verladestation": car trains and car ferries, 14 stops) are left out.
STATION_TYPES = {"Haltestelle Bahn": "rail", "Haltestelle Bus": "bus", "Haltestelle Schiff": "boat", "Uebrige Bahnen": "cable"}
_RAIL = ("Q55488 Q55678 Q27996460 Q2175765 Q928830 Q22808403 Q22808404 Q11670533 Q63979268 Q1793804 Q18543139 Q55485 "
         "Q55491 Q114782406 Q1402443 Q1567913 Q55677 Q11606300 Q55490 Q2451577 Q1335652 Q55484 Q1147171 Q125306799 "
         "Q13433889 Q4346602 Q1406904 Q125695092").split()
#  railway station, railway stop, Haltepunkt, tram stop, metro station, underground station, station located on surface,
#  elevated station, people mover station, S-Bahn station, central station, dead-end station, underground railway
#  station, break-of-gauge station, border station, harbour station, Keilbahnhof, last station, through station,
#  junction station, airport station, multi-level station, interchange station, station accessible only by rail,
#  staggered platforms, passenger station, inter-city station, car-train station
STATION_CLASSES = {**{q: ("rail",) for q in _RAIL},
                   "Q99980564": ("rail", "cable"), "Q2391004": ("rail", "cable"),       # top station, bottom station
                   "Q107133821": ("rail", "cable"), "Q15915771": ("rail", "cable"),     # middle station, funicular station
                   "Q44696264": ("cable",),                                            # aerial tramway station
                   "Q813817": ("rail", "bus"), "Q953806": ("bus",), "Q494829": ("bus",),  # request stop, bus stop, bus station
                   **{q: ("boat",) for q in ("Q863454", "Q16938673", "Q3543043", "Q828909", "Q2240172", "Q4303352", "Q1478783")}}
#                  pier, landing, quay, wharf, jetty, passenger ship terminal, ferry port
STATION_NOT = ("Q4663385", "Q65464941", "Q168565")          # former railway station, former railway stop, ghost station
STATION_CLOSED = ("Q11639308", "Q56651571", "Q104664889", "Q56556915", "Q55570340")   # state of use (P5817): decommissioned,
#                                                          out of service, permanently closed, demolished, closed to the public
STATION_LINK = 300
# Pairing distance: of the items whose exact name is a stop of a compatible type within 5 km, 99% lie within 200 m of
# it. Deliberately not in RADIUS: edit.build_edit (labels!) must never handle these rows, and fails on them.
STATION_RADIUS = 250
BATCHES["stations"] = {"plan": "plan_stations.jsonl", "cache": "stations_",
                       "covers": ["railway stations and other public transport stops"],
                       "note": "Railway stations and other public transport stops get only a source for their position, "
                               "their official name and their municipality: no labels, heights or new items."}
# Sixth batch, "areas": the area of each current municipality, from its swissBOUNDARIES3D polygon (gem_flaeche, whole
# hectares, as of the release date), paired by BFS number (P771), unique both ways. Rules (see edit_stations_areas.py):
#   lake: a municipality with a share of a large lake (see_flaeche > 0): "skip" (left out), "land" (area without the
#         lake share, which is how the Federal Statistical Office's values on Wikidata count it) or "total";
#   equal_dated: an equal value is there only with another point in time: "new" (add the dated value), "source" (cite
#         swissBOUNDARIES3D on it anyway) or "skip";
#   different: no equal value: "add" (dated value alongside the others) or "skip";
#   rank: of a new value: "normal" or "preferred" (with reason "most recent value").
# An equal value without point in time (or with the release date) always gets the source. Nothing is ever removed.
AREA_RULES = {"lake": "skip", "equal_dated": "skip", "different": "add", "rank": "preferred"}
BATCHES["areas"] = {"plan": "plan_areas.jsonl", "cache": "areas_", "covers": ["municipalities"], "rules": AREA_RULES,
                    "note": "Municipalities get their area from swissBOUNDARIES3D, in square kilometres with the date the "
                            "boundaries are valid on as point in time, or swisstopo as a source for an equal area without "
                            "a date." + (" Municipalities with a share of a large lake are left out."
                                         if AREA_RULES["lake"] == "skip" else "")}
# Seventh batch, "ids": the swissNAMES3D ID (P14976, created 17 October 2026) on the items the earlier batches edited or
# created, from the UUID they were paired on. Only IDs that were already in the 2025 release, since lakes, rivers and
# streets get new IDs in every release; a locality or neighbourhood, which the 2026 release gives as a point and as a
# polygon, gets the polygon's long-standing ID; a stop only when it is one point (stations are often one point per
# platform). Nothing else changes on these items.
SWISSNAMES3D_ID = "P14976"
ID_PREVIOUS_RELEASE = "swissnames3d_2025"
ID_SOURCE_BATCHES = ("", "natural", "settlements", "stations")          # batches whose log pairs items with a UUID
ID_CONFLICTS = ("Q5", "Q4167410", "Q13406463")      # human, disambiguation page, list: the property's conflicts-with
BATCHES["ids"] = {"plan": "plan_ids.jsonl", "cache": "ids_", "covers": ["swissNAMES3D IDs"],
                  "note": "Items the earlier batches edited or created get their swissNAMES3D ID, when it was already "
                          "in the previous release; lakes are left out, since their IDs change with every release."}
# Eighth batch, "localities": new items for the localities under 100 inhabitants that have none (jz, 16 October 2026:
# task 1's approval says "create missing items" with no floor; wait for the swissNAMES3D ID so every new item carries
# it). In stages: 20 to 99 inhabitants first ("hamlet"); under 20 after a quiet week. Creations only. A locality is
# both a point and a polygon in the 2026 release: the polygon, whose ID dates back years, is used. Settlement items
# of any of these classes, and any Swiss item with the same name, count as possible duplicates.
LOCALITY_CLASSES = {**SETTLEMENT_CLASSES, "Q253019": "settlement", "Q3257686": "settlement", "Q131596": "settlement",
                    "Q54935504": "settlement"}       # ...Ortsteil, locality, farm, city of Switzerland
BATCHES["localities"] = {
    "plan": "plan_localities.jsonl", "cache": "localities_", "classes": LOCALITY_CLASSES,
    "types": {"Ort": "settlement"}, "edit": set(), "new": {"Ort": "Q5084"}, "create_pop": {"20 bis 49", "50 bis 99"},
    "geoms": {"PLY"}, "add_id": True, "same_kind_name": {"settlement": 3000}, "ch_only": True,
    "covers": ["localities of 20 to 99 inhabitants"],
    "note": "Localities of 20 to 99 inhabitants that have no item get one, as a hamlet, with their swissNAMES3D ID."}
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
              "Q54050": ("hill", "Hügel", "colline", "collina"), "Q207326": ("summit", "Gipfel", "sommet", "cima"),
              "Q39816": ("valley", "Tal", "vallée", "valle"), "Q740445": ("ridge", "Grat", "crête", "cresta"),
              "Q34038": ("waterfall", "Wasserfall", "cascade", "cascata"), "Q35509": ("cave", "Höhle", "grotte", "grotta"),
              "Q532": ("village", "Dorf", "village", "villaggio"),
              "Q5084": ("hamlet", "Weiler", "hameau", "borgata"),           # the class's own labels
              "Q123705": ("neighbourhood", "Quartier", "quartier", "quartiere")}
GENERIC = {"Q23397": ("lake in Switzerland", "See in der Schweiz", "lac suisse", "lago svizzero"),
           "Q35666": ("glacier in Switzerland", "Gletscher in der Schweiz", "glacier suisse", "ghiacciaio svizzero"),
           "Q133056": ("mountain pass in Switzerland", "Gebirgspass in der Schweiz", "col suisse", "valico svizzero"),
           "Q8502": ("mountain in Switzerland", "Berg in der Schweiz", "montagne suisse", "montagna svizzera"),
           "Q54050": ("hill in Switzerland", "Hügel in der Schweiz", "colline suisse", "collina svizzera"),
           "Q207326": ("summit in Switzerland", "Gipfel in der Schweiz", "sommet suisse", "cima svizzera"),
           "Q39816": ("valley in Switzerland", "Tal in der Schweiz", "vallée suisse", "valle svizzera"),
           "Q740445": ("ridge in Switzerland", "Grat in der Schweiz", "crête suisse", "cresta svizzera"),
           "Q34038": ("waterfall in Switzerland", "Wasserfall in der Schweiz", "cascade suisse", "cascata svizzera"),
           "Q35509": ("cave in Switzerland", "Höhle in der Schweiz", "grotte suisse", "grotta svizzera"),
           "Q532": ("village in Switzerland", "Dorf in der Schweiz", "village suisse", "villaggio svizzero"),
           "Q5084": ("hamlet in Switzerland", "Weiler in der Schweiz", "hameau suisse", "borgata svizzera"),
           "Q123705": ("neighbourhood in Switzerland", "Quartier in der Schweiz", "quartier suisse", "quartiere svizzero")}
KIND_OF = {**ALL_CLASSES, **LOCALITY_CLASSES}     # class -> feature kind, for the runner's live duplicate check


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
    import shutil
    if shutil.which("curl"):
        return json.loads(subprocess.run(["curl", "-s", "-S", "-L", "--max-time", "600", "-A", UA, *extra, url],
                                         capture_output=True, check=True).stdout)
    headers, args = {"User-Agent": UA}, list(extra)  # without curl (e.g. on Toolforge): GETs, with -H headers
    while args:
        if args[0] != "-H" or len(args) < 2:
            raise RuntimeError("curl is needed for this request")
        name, _, value = args[1].partition(":")
        headers[name.strip()] = value.strip()
        del args[:2]
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=600) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:           # like curl: hand back the error page, which then fails to parse
        return json.loads(e.read())


def latest_asset(collection, suffix):
    items = curl_json(f"{STAC}/{collection}/items?limit=100")["features"]
    item = max(items, key=lambda f: f["properties"].get("datetime") or f["id"])
    href = next(a["href"] for k, a in item["assets"].items() if k.endswith(suffix))
    return item["id"], href


def release_date(collection, release):
    """The date a release is valid on, from swisstopo's STAC API, as a Wikidata time ("+2026-01-01T00:00:00Z")."""
    return "+" + curl_json(f"{STAC}/{collection}/items/{release}")["properties"]["datetime"][:10] + "T00:00:00Z"


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

def swissnames3d(types=TYPES):
    """Records of the feature types a batch covers ({object type: group}), plus every official/common name of each object."""
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
                if r["OBJEKTART"] in types:
                    recs.append({"uuid": r["UUID"], "name": r["NAME"], "type": r["OBJEKTART"], "group": types[r["OBJEKTART"]],
                                 "geom": fn[13:16],
                                 "e": float(r["E"]), "n": float(r["N"]), "z": int(float(r["Z"])), "status": r["STATUS"],
                                 "lang": lang, "ntype": r["NAMEN_TYP"], "pop": r.get("EINWOHNERKATEGORIE") or ""})
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


def day_snak(pid, date):
    return {"snaktype": "value", "property": pid, "datavalue": {"type": "time", "value": {
        "time": date, "timezone": 0, "before": 0, "after": 0, "precision": 11,
        "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}}}


def area_claim(ha, date, ref, rank="normal"):
    """Area in square kilometres (whole hectares, so two decimals) at a point in time; a preferred one says why."""
    c = statement("P2046", {"type": "quantity", "value": {"amount": f"+{ha // 100}.{ha % 100:02d}", "unit": KM2}}, ref)
    c["qualifiers"], c["qualifiers-order"] = {"P585": [day_snak("P585", date)]}, ["P585"]
    if rank == "preferred":
        c["rank"] = "preferred"
        c["qualifiers"]["P7452"] = [{"snaktype": "value", "property": "P7452",
                                     "datavalue": {"type": "wikibase-entityid", "value": entity(MOST_RECENT)}}]
        c["qualifiers-order"].append("P7452")
    return c


def cites(claim, dataset):
    return any(s.get("datavalue", {}).get("value", {}).get("id") == dataset
               for r in claim.get("references", []) for s in r["snaks"].get("P248", []))


def only_import_refs(claim):
    return all(set(r["snaks"]) <= IMPORT_ONLY for r in claim.get("references", []))


# ---------------------------------------------------------------- bot session (Special:BotPasswords pair from the user environment)

def bot_site(throttle):
    import shutil
    import tempfile
    if sys.platform == "win32":                   # the operator's PC: user environment in the registry
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            login = winreg.QueryValueEx(key, "COMMONS_BOT_USER")[0]
            password = winreg.QueryValueEx(key, "COMMONS_BOT_PASSWORD")[0]
    else:                                         # Toolforge: set with `toolforge envvars create`
        login, password = os.environ.get("COMMONS_BOT_USER", ""), os.environ.get("COMMONS_BOT_PASSWORD", "")
    if "@" not in login or len(password) != 32:
        sys.exit("COMMONS_BOT_USER/COMMONS_BOT_PASSWORD are not a Special:BotPasswords pair; refusing to log in.")
    user, bp_name = login.split("@", 1)
    workdir = tempfile.mkdtemp(prefix="pwb-")
    with open(os.path.join(workdir, "user-config.py"), "w", encoding="utf-8") as f:
        f.write(f"family = 'wikidata'\nmylang = 'wikidata'\nusernames['wikidata']['wikidata'] = {user!r}\n"
                f"password_file = 'user-password.py'\nput_throttle = {throttle}\n"
                "if 'write_maxlag' in globals():\n    write_maxlag = 5\nelse:\n    maxlag = 5\n"  # renamed in pywikibot 11.8
                "max_retries = 200\nretry_wait = 10\nretry_max = 120\n"
                f"user_agent_description = {UA!r}\n")
    fd = os.open(os.path.join(workdir, "user-password.py"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(fd, "w", encoding="utf-8") as f:                    # readable by the bot's account only
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
