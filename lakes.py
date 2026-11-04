"""Batch "lakes" (task 1): each lake's outline from swissTLM3D, laid over swissBOUNDARIES3D. Only the planning needs
the geometry libraries (shapely, pyshp); the edits are made from the plan file (edit_stations_areas.build_lake).

swissTLM3D draws lakes as shorelines (TLM_STEHENDES_GEWAESSER, PolylineZ, kinds "See" and "Seeinsel"), each named by
its water-name UUID (GEW_NAME_U). A lake's surface is the polygon its shore lines close, less its islands. Its outline
continues across the border (Lake Geneva 580 km² in all), so the Swiss part is the share inside Switzerland's
territory. The municipalities are those whose territory (Gemeindegebiet) holds part of the lake; the water of some big
lakes is cantonal territory (Kantonsgebiet: Lake Thun, the Zurich part of Lake Zurich) and lies in no municipality.

    python lakes.py extract        fetch the shoreline layer out of the swissTLM3D zip (range requests, ~70 MB)
    python lakes.py survey         lakes, areas, Swiss shares and municipality shares, written to work/lakes_survey.json
"""
import collections
import json
import sqlite3
import sys
from pathlib import Path

from common import WORK, out

TLM_RELEASE = "swisstlm3d_2026-02"
TLM_URL = ("https://data.geo.admin.ch/ch.swisstopo.swisstlm3d/swisstlm3d_2026-02/"
           "swisstlm3d_2026-02_2056_5728.shp.zip")
TLM_DIR = WORK / TLM_RELEASE
SHORES = TLM_DIR / "swissTLM3D_TLM_STEHENDES_GEWAESSER"
BOUNDARIES = next((WORK / "swissboundaries3d_2026-01").glob("*.gpkg"), None)
SURVEY = WORK / "lakes_survey.json"
MIN_SHARE_M2, MIN_SHARE_PART, PART_OF_POND = 1000, 0.001, 0.1
# A municipality counts when it holds at least 0.1% of the lake and either 1,000 m² or a tenth of it (a pond of 600 m²
# in one municipality): smaller overlaps are where two drawings of one shore differ.


MAX_MUNIS = 5      # a lake in more municipalities gets its cantons instead (jz, 4 November 2026)


def counts(a, area):
    return a >= MIN_SHARE_PART * area and (a >= MIN_SHARE_M2 or a >= PART_OF_POND * area)


def gpkg_geometry(blob):
    """A GeoPackage geometry: the 'GP' header (8 bytes and an envelope whose size the flags give), then WKB."""
    from shapely import wkb
    flags = blob[3]
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    return wkb.loads(bytes(blob[8 + envelope:]))


def lake_polygons():
    """{water-name UUID: (names, polygon)} for every named lake: its shore lines closed into polygons, islands cut out."""
    import shapefile
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    lines, names = collections.defaultdict(lambda: collections.defaultdict(list)), collections.defaultdict(set)
    for sr in shapefile.Reader(str(SHORES), encoding="utf-8").iterShapeRecords():
        rec = sr.record
        if rec["GEW_NAME_U"]:
            lines[rec["GEW_NAME_U"]][rec["OBJEKTART"]].append(LineString([p[:2] for p in sr.shape.points]))
            if rec["NAME"]:
                names[rec["GEW_NAME_U"]].add(rec["NAME"])
    lakes = {}
    for u, kinds in lines.items():
        water = unary_union(list(polygonize(unary_union(kinds.get("See", [])))))
        if kinds.get("Seeinsel"):
            water = water.difference(unary_union(list(polygonize(unary_union(kinds["Seeinsel"])))))
        if not water.is_empty:
            lakes[u] = (sorted(names[u]), water)
    return lakes


def territories():
    """Switzerland's territory, and its municipalities and cantonal areas (the water no municipality holds)."""
    con = sqlite3.connect(str(BOUNDARIES))
    from shapely.ops import unary_union
    ch = unary_union([gpkg_geometry(g) for g, in con.execute("select geom from tlm_landesgebiet where icc = 'CH'")])
    munis, cantonal = [], []
    for g, bfs, name, art, icc, canton in con.execute(
            "select geom, bfs_nummer, name, objektart, icc, kantonsnummer from tlm_hoheitsgebiet"):
        if icc != "CH":
            continue
        rec = {"bfs": int(bfs) if bfs else None, "name": name, "canton": canton, "geom": gpkg_geometry(g)}
        (munis if art == "Gemeindegebiet" else cantonal).append(rec)
    return ch, munis, cantonal


def survey():
    """Each named lake: area, Swiss share, the municipalities and cantonal areas holding part of it."""
    from shapely.strtree import STRtree
    lakes = lake_polygons()
    ch, munis, cantonal = territories()
    tree = STRtree([m["geom"] for m in munis])
    ctree = STRtree([c["geom"] for c in cantonal])
    rows = []
    for u, (names, water) in lakes.items():
        area = water.area
        swiss = water.intersection(ch).area
        if swiss < 1:
            continue                                   # a lake beyond the border, drawn for the map's edge
        shares = []
        for i in tree.query(water):
            m = munis[i]
            a = water.intersection(m["geom"]).area
            if counts(a, area):
                shares.append({"bfs": m["bfs"], "name": m["name"], "m2": round(a)})
        cshares = []
        for i in ctree.query(water):
            c = cantonal[i]
            a = water.intersection(c["geom"]).area
            if counts(a, area):
                cshares.append({"name": c["name"], "canton": c["canton"], "m2": round(a)})
        rows.append({"gew_uuid": u, "names": names, "m2": round(area), "swiss_m2": round(swiss),
                     "munis": sorted(shares, key=lambda s: -s["m2"]), "cantonal": cshares})
    SURVEY.write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")
    border = [r for r in rows if r["swiss_m2"] < 0.999 * r["m2"]]
    out(f"{len(rows)} named lakes with water in Switzerland, {len(border)} of them across the border; "
        f"{sum(1 for r in rows if not r['munis'])} in no municipality; "
        f"{collections.Counter(min(len(r['munis']), 6) for r in rows)} municipalities per lake (6 = 6 or more)")
    return rows


def items_of_lakes():
    """{water-name UUID: item} for the lakes an earlier batch paired with an item: swissNAMES3D's lake records name the
    water by NAME_UUID (or NAMENGRUPPE_UUID for a name in several languages), and edits.csv logs the item each record's
    UUID was paired with. A lake whose records lead to several items is left out."""
    import csv
    csv.field_size_limit(10 ** 9)
    paired = collections.defaultdict(set)
    for r in csv.DictReader(open(WORK.parent / "edits.csv", encoding="utf-8")):
        if r["qid"] and r["uuid"]:
            paired[r["uuid"]].add(r["qid"])
    names = WORK / "swissnames3d_2026" / "swissNAMES3D_PLY.csv"
    found = collections.defaultdict(set)
    for r in csv.DictReader(open(names, encoding="utf-8-sig"), delimiter=";"):
        if r["OBJEKTART"] == "See":
            for key in (r["NAME_UUID"], r["NAMENGRUPPE_UUID"]):
                if key:
                    found[key] |= paired.get(r["UUID"], set())
    return {u: next(iter(qs)) for u, qs in found.items() if len(qs) == 1}, \
        {u for u, qs in found.items() if len(qs) > 1}


def build_lakes():
    """plan_lakes.jsonl: one edit row per paired lake (read only; the edits follow edit_stations_areas.build_lake)."""
    from common import BATCHES, CANTON_ITEMS, literal, qid, release_date, sparql_tsv
    rows = json.loads(SURVEY.read_text(encoding="utf-8")) if SURVEY.exists() else survey()
    item_of, several = items_of_lakes()
    muni_items = collections.defaultdict(set)
    for item, code in sparql_tsv("SELECT ?item ?code WHERE { ?item wdt:P31 wd:Q70208 ; wdt:P771 ?code . "
                                 "MINUS { ?item wdt:P576 ?end } }", "municipalities.tsv"):
        muni_items[int(literal(code)[0])].add(qid(item))
    date = release_date("ch.swisstopo.swisstlm3d", TLM_RELEASE)
    canton_of = {int(bfs): int(k) for bfs, k in sqlite3.connect(str(BOUNDARIES)).execute(
        "select bfs_nummer, kantonsnummer from tlm_hoheitsgebiet where objektart = 'Gemeindegebiet' and icc = 'CH'")}
    plan, stats = [], collections.Counter()
    for r in rows:
        u = r["gew_uuid"]
        if u in several:
            stats["several items"] += 1
            continue
        if u not in item_of:
            stats["no item paired"] += 1
            continue
        munis = []
        for m in r["munis"]:
            qs = muni_items.get(m["bfs"], set())
            if len(qs) == 1:
                munis.append(next(iter(qs)))
            else:
                stats["municipality without one item"] += 1
        cantons = {CANTON_ITEMS[int(c["canton"]) - 1] for c in r["cantonal"] if c.get("canton")}
        if len(r["munis"]) > MAX_MUNIS:                  # a big lake: its cantons rather than a long list (jz)
            cantons |= {CANTON_ITEMS[canton_of[m["bfs"]] - 1] for m in r["munis"] if m["bfs"] in canton_of}
            munis = []
            stats["cantons instead of municipalities"] += 1
        cantons = sorted(cantons)
        names = [n for full in r["names"] for n in full.split(" | ")]
        plan.append({"kind": "edit", "batch": "lakes", "qid": item_of[u], "date": date,
                     "rec": {"uuid": u, "name": names[0], "names": names, "m2": r["m2"], "swiss_m2": r["swiss_m2"],
                             "munis": munis, "cantons": cantons}})
    path = WORK / BATCHES["lakes"]["plan"]
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"release": TLM_RELEASE, "date": date, "lakes": len(rows)}) + "\n")
        for row in plan:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    out(f"{TLM_RELEASE} (valid on {date[1:11]}): {len(rows)} lakes; plan: {len(plan)} items paired, "
        f"skipped {dict(stats)} -> {path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "extract":
        import remote_zip
        remote_zip.extract(TLM_URL, "swissTLM3D_TLM_STEHENDES_GEWAESSER", TLM_DIR)
    elif sys.argv[1] == "survey":
        survey()
