"""Build the work plan locally: pair Wikidata items with swissNAMES3D features and list the features to create.

Everything here runs on downloaded data (swissNAMES3D, swissBOUNDARIES3D, a QLever snapshot of Wikidata).
The runner re-checks every item live before it edits, so the plan only needs the pairs, not the changes.
Each batch (common.BATCHES) has its own plan file: work/plan.jsonl for the first, work/plan_<batch>.jsonl after.
"""
import csv
import json
import math
from collections import Counter, defaultdict

from common import (BATCHES, ID_PREVIOUS_RELEASE, ID_SOURCE_BATCHES, NAME_LANGS, OFFICIAL_TYPES, RADIUS, RESERVOIR, ROOT,
                    WORK, Boundaries, fetch_dataset, literal, out, point, qid, sparql_tsv, swissnames3d)

MUNI_GROUPS = ("summit", "pass", "waterfall", "cave",   # point features: municipality (P131) from their name point
               "settlement", "neighbourhood")
NEAR_KIND = {"summit": 250, "pass": 250, "lake": 1000, "glacier": 1000,      # no item of the same kind this close
             "valley": 1000, "ridge": 1000, "waterfall": 250, "cave": 250, "settlement": 250, "neighbourhood": 250}


def plan_path(batch=""):
    return WORK / BATCHES[batch]["plan"]


def official(rec):
    return rec["status"] == "offiziell" and rec["ntype"] in OFFICIAL_TYPES


def build_p131():
    """Swiss items with a position and no municipality, of point-like classes: the municipality swissBOUNDARIES3D
    places every one of their positions in (none if positions disagree or lie outside Switzerland)."""
    from common import P131_NOT, P131_ROOTS
    bounds = Boundaries()
    muni_item = defaultdict(set)
    for item, code in sparql_tsv("SELECT ?item ?code WHERE { ?item wdt:P31 wd:Q70208 ; wdt:P771 ?code . "
                                 "MINUS { ?item wdt:P576 ?end } }", "municipalities.tsv"):
        muni_item[int(literal(code)[0])].add(qid(item))
    muni_item = {k: next(iter(v)) for k, v in muni_item.items() if len(v) == 1}
    roots, bad = " ".join("wd:" + q for q in P131_ROOTS), " ".join("wd:" + q for q in P131_NOT)
    query = (f"SELECT DISTINCT ?item ?coord WHERE {{ ?item wdt:P17 wd:Q39 ; wdt:P625 ?coord ; wdt:P31 ?c . "
             f"MINUS {{ ?item wdt:P131 ?x }} VALUES ?root {{ {roots} }} ?c wdt:P279* ?root . "
             f"FILTER NOT EXISTS {{ VALUES ?bad {{ {bad} }} ?item wdt:P31/wdt:P279* ?bad }} }}")
    coords = defaultdict(list)
    for item, c in sparql_tsv(query, "p131_candidates.tsv"):
        if "(" in c:
            coords[qid(item)].append(point(c))
    rows, skipped = [], Counter()
    for q, pts in sorted(coords.items()):
        found = {tuple(muni_item.get(bfs) for bfs, _ in bounds.at(e, n)) for e, n in pts}
        if len(found) != 1 or not next(iter(found)) or not all(next(iter(found))):
            skipped["outside, on a border or positions disagree"] += 1
            continue
        e, n = pts[0]
        rows.append({"kind": "edit", "batch": "p131", "qid": q, "munis": list(next(iter(found))),
                     "rec": {"uuid": "", "name": q, "e": e, "n": n, "group": "point"}})
    path = plan_path("p131")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"release": "swissboundaries3d", "items_in_scope": len(coords)}) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    out(f"plan: {len(rows)} items get a municipality, skipped {dict(skipped)} -> {path}")


def build_stations():
    """Railway stations and other stops paired with swissNAMES3D's stops. A stop is the points of one type and name
    within STATION_LINK m of each other (one per platform or direction); an item and a stop are paired when one of
    the item's labels (any language) or aliases is the stop's name, the types are compatible, the stop lies within
    250 m and each is the other's only candidate. Closed and former stations are left out. No new items."""
    from common import STATION_CLASSES, STATION_CLOSED, STATION_LINK, STATION_NOT, STATION_RADIUS, STATION_TYPES
    cache = BATCHES["stations"]["cache"]
    recs, release = swissnames3d(STATION_TYPES)
    bounds = Boundaries()
    values = " ".join("wd:" + q for q in [*STATION_CLASSES, *STATION_NOT])
    scope = f"VALUES ?class {{ {values} }} ?item wdt:P31 ?class ; wdt:P17 wd:Q39 ."
    groups, closed = defaultdict(set), set()
    for item, cls in sparql_tsv(f"SELECT ?item ?class WHERE {{ {scope} }}", cache + "scope.tsv"):
        if qid(cls) in STATION_NOT:
            closed.add(qid(item))
        else:
            groups[qid(item)].update(STATION_CLASSES[qid(cls)])
    states = " ".join("wd:" + q for q in STATION_CLOSED)
    closed |= {qid(r[0]) for r in sparql_tsv(
        f"SELECT DISTINCT ?item WHERE {{ {scope} {{ ?item wdt:P576 ?x }} UNION {{ ?item wdt:P3999 ?x }} UNION "
        f"{{ ?item wdt:P582 ?x }} UNION {{ VALUES ?x {{ {states} }} ?item wdt:P5817 ?x }} }}", cache + "closed.tsv")}
    names = defaultdict(set)
    for item, n in (sparql_tsv(f"SELECT ?item ?name WHERE {{ {scope} ?item rdfs:label ?name . }}", cache + "labels.tsv")
                    + sparql_tsv(f"SELECT ?item ?name WHERE {{ {scope} ?item skos:altLabel ?name . }}", cache + "aliases.tsv")):
        names[qid(item)].add(literal(n)[0])
    coords = defaultdict(list)
    for item, c in sparql_tsv(f"SELECT ?item ?coord WHERE {{ {scope} ?item wdt:P625 ?coord . }}", cache + "coords.tsv"):
        if "(" in c:
            coords[qid(item)].append(point(c))
    has_p131 = {qid(r[0]) for r in sparql_tsv(f"SELECT DISTINCT ?item WHERE {{ {scope} ?item wdt:P131 ?x . }}",
                                              cache + "p131.tsv")}
    muni_item = defaultdict(set)
    for item, code in sparql_tsv("SELECT ?item ?code WHERE { ?item wdt:P31 wd:Q70208 ; wdt:P771 ?code . "
                                 "MINUS { ?item wdt:P576 ?end } }", "municipalities.tsv"):
        muni_item[int(literal(code)[0])].add(qid(item))
    muni_item = {k: next(iter(v)) for k, v in muni_item.items() if len(v) == 1}

    stop_of, size = {}, Counter()                 # point uuid -> stop (the uuid of one of its points)
    same = defaultdict(list)
    for r in recs:
        same[r["type"], r["name"]].append(r)
    for rs in same.values():                      # points linked within STATION_LINK m, transitively, are one stop
        stop = list(range(len(rs)))
        for i in range(len(rs)):
            for j in range(i):
                if math.hypot(rs[i]["e"] - rs[j]["e"], rs[i]["n"] - rs[j]["n"]) <= STATION_LINK:
                    old, new = stop[i], stop[j]
                    stop = [new if s == old else s for s in stop]
        for i, r in enumerate(rs):
            stop_of[r["uuid"]] = rs[stop[i]]["uuid"]
            size[rs[stop[i]]["uuid"]] += 1
    by_name = defaultdict(list)
    for r in recs:
        by_name[r["name"]].append(r)

    stats, found, items_of = Counter(), {}, defaultdict(set)
    for q, grps in groups.items():
        if q in closed:
            stats["closed or former station"] += 1
            continue
        if not coords[q]:
            stats["no coordinates"] += 1
            continue
        we, wn = coords[q][0]
        stops = {}                                    # stop -> (distance, nearest point of the stop)
        for r in (r for nm in names[q] for r in by_name.get(nm, []) if r["group"] in grps):
            d = math.hypot(r["e"] - we, r["n"] - wn)
            if d <= STATION_RADIUS and d < stops.get(stop_of[r["uuid"]], (math.inf,))[0]:
                stops[stop_of[r["uuid"]]] = (d, r)
        found[q] = stops
        for s in stops:
            items_of[s].add(q)
    rows = []
    for q, stops in sorted(found.items(), key=lambda kv: int(kv[0][1:])):
        if len(stops) != 1:
            stats["no match" if not stops else "several stops"] += 1
            continue
        s, (d, r) = next(iter(stops.items()))
        if len(items_of[s]) > 1:                      # possible duplicates or parts of one station: left alone
            stats["one stop, several items"] += 1
            continue
        row = {"kind": "edit", "batch": "stations", "qid": q, "dist": round(d, 1), "points": size[s], "rec": r}
        if q not in has_p131:                         # the stop's municipality, if every position of the item agrees
            at = {tuple(muni_item.get(bfs) for bfs, _ in bounds.at(e, n)) for e, n in [(r["e"], r["n"])] + coords[q]}
            m = next(iter(at)) if len(at) == 1 else ()
            if len(m) == 1 and m[0]:
                row["munis"] = list(m)
            else:
                stats["no municipality: outside, on a border or positions disagree"] += 1
        rows.append(row)
    path = plan_path("stations")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"release": release, "items_in_scope": len(groups)}) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    out(f"{release}: {sum(1 for r in recs if stop_of[r['uuid']] == r['uuid'])} stops "
        f"{dict(Counter(r['type'] for r in recs if stop_of[r['uuid']] == r['uuid']))} in {len(recs)} points; "
        f"{len(groups)} items in scope")
    out(f"plan: {len(rows)} items paired {dict(Counter(r['rec']['type'] for r in rows))}, "
        f"{sum(1 for r in rows if r.get('munis'))} with a municipality, skipped {dict(stats)} -> {path}")


def build_areas():
    """Current municipalities paired with their swissBOUNDARIES3D polygon by BFS number (P771), unique both ways; the
    area to state (whole hectares) follows BATCHES["areas"]["rules"]["lake"]."""
    import sqlite3
    from common import fetch_dataset, release_date
    rules = BATCHES["areas"]["rules"]
    folder = fetch_dataset("ch.swisstopo.swissboundaries3d", ".gpkg.zip")
    release, date = folder.name, release_date("ch.swisstopo.swissboundaries3d", folder.name)
    con = sqlite3.connect(str(next(folder.rglob("*.gpkg"))))
    polys = defaultdict(list)
    for uuid, bfs, name, ha, lake, art, icc in con.execute(
            "select uuid, bfs_nummer, name, gem_flaeche, see_flaeche, objektart, icc from tlm_hoheitsgebiet"):
        if art == "Gemeindegebiet" and icc == "CH" and bfs:      # not Liechtenstein, enclaves, lakes or condominiums
            polys[int(bfs)].append({"uuid": uuid, "name": name, "group": "municipality", "bfs": int(bfs),
                                    "ha": int(round(ha)), "lake_ha": int(round(lake or 0))})
    items_of, codes_of = defaultdict(set), defaultdict(set)
    for item, code in sparql_tsv("SELECT ?item ?code WHERE { ?item wdt:P31 wd:Q70208 ; wdt:P771 ?code . "
                                 "MINUS { ?item wdt:P576 ?end } }", "municipalities.tsv"):
        items_of[int(literal(code)[0])].add(qid(item))
        codes_of[qid(item)].add(int(literal(code)[0]))
    stats, rows = Counter(), []
    for bfs, recs in sorted(polys.items()):
        qs = items_of.get(bfs, set())
        if len(recs) != 1 or len(qs) != 1:
            stats["no item" if not qs else "several items" if len(qs) > 1 else "several polygons"] += 1
            continue
        q, r = next(iter(qs)), recs[0]
        if len(codes_of[q] & set(polys)) != 1:
            stats["item has several current BFS numbers"] += 1
            continue
        if r["lake_ha"] and rules["lake"] == "skip":
            stats["share of a lake (rule: skip)"] += 1
            continue
        r["area_ha"] = r["ha"] - r["lake_ha"] if rules["lake"] == "land" else r["ha"]
        rows.append({"kind": "edit", "batch": "areas", "qid": q, "date": date, "rec": r})
    stale = sorted((q for q, codes in codes_of.items() if not codes & set(polys)), key=lambda q: int(q[1:]))
    if stale:                                         # e.g. merged municipalities whose item lacks P576
        out(f"{len(stale)} municipality items without P576 have no current BFS number: {' '.join(stale)}")
    path = plan_path("areas")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"release": release, "date": date, "rules": rules, "items_in_scope": len(codes_of)}) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    out(f"{release} (valid on {date[1:11]}): {len(polys)} municipalities, {len(codes_of)} current municipality items")
    out(f"plan: {len(rows)} items paired, skipped {dict(stats)}, rules {rules} -> {path}")


def release_records(folder):
    """{UUID: record} of every object of a swissNAMES3D release (points, lines and polygons), with all its names."""
    recs = {}
    for fn in ("swissNAMES3D_PKT.csv", "swissNAMES3D_PLY.csv", "swissNAMES3D_LIN.csv"):
        path = next(folder.rglob(fn), None)
        if not path:
            continue
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                rec = recs.setdefault(r["UUID"], {"uuid": r["UUID"], "type": r["OBJEKTART"], "geom": fn[13:16],
                                                  "name_uuid": r["NAME_UUID"], "name": r["NAME"], "e": float(r["E"]),
                                                  "n": float(r["N"]), "names": []})
                if r["NAME"] not in rec["names"]:
                    rec["names"].append(r["NAME"])
    return recs


def build_ids():
    """The swissNAMES3D ID for the items the earlier batches edited or created (BATCHES["ids"] in common.py), from the
    UUID each was paired on in edits.csv:
    - an item paired with more than one UUID, or a UUID paired with more than one item, is left out;
    - a locality or neighbourhood paired on its point gets its polygon's ID (same name UUID; else the same name and
      type within 1.5 km): the 2026 release added a point beside each polygon, and the polygon IDs date back years;
    - a stop that is several points (one per platform) is left out: no single ID stands for it;
    - only IDs already in the previous release count (lakes get new ones in every release)."""
    folder = fetch_dataset("ch.swisstopo.swissnames3d", ".csv.zip")
    release, recs = folder.name, release_records(folder)
    previous = set(release_records(WORK / ID_PREVIOUS_RELEASE))
    polygons = defaultdict(list)                      # (type, name UUID) and (type, name) -> polygons
    for r in recs.values():
        if r["geom"] == "PLY" and r["type"] in ("Ort", "Quartier"):
            polygons[(r["type"], r["name_uuid"])].append(r)
            polygons[(r["type"], r["name"])].append(r)
    multi_stop = {row["qid"] for row in load("stations")[1] if row["kind"] == "edit" and row.get("points", 1) > 1} \
        if plan_path("stations").exists() else set()
    with open(ROOT / "edits.csv", encoding="utf-8", newline="") as f:
        logged = [r for r in csv.DictReader(f) if r["revid"] and r["uuid"] and r["qid"]
                  and r["kind"].partition(":")[0] in ("edit", "create") and r["kind"].partition(":")[2] in ID_SOURCE_BATCHES]
    uuids_of = defaultdict(set)
    for r in logged:
        uuids_of[r["qid"]].add(r["uuid"])
    stats, chosen = Counter(), {}
    for q, uuids in uuids_of.items():
        if len(uuids) > 1:
            stats["item paired with several UUIDs"] += 1
            continue
        rec = recs.get(next(iter(uuids)))
        if not rec:
            stats["UUID not in this release"] += 1
            continue
        if q in multi_stop:
            stats["stop of several points"] += 1
            continue
        if rec["type"] in ("Ort", "Quartier") and rec["geom"] == "PKT":
            same = polygons.get((rec["type"], rec["name_uuid"])) or [
                p for p in polygons.get((rec["type"], rec["name"]), []) if math.dist((p["e"], p["n"]), (rec["e"], rec["n"])) <= 1500]
            if len(same) != 1:
                stats["point without one polygon"] += 1
                continue
            rec = same[0]
        if rec["uuid"] not in previous:
            stats[f"ID new in {release[-4:]} ({rec['type']})"] += 1
            continue
        chosen[q] = rec
    items_of = defaultdict(list)
    for q, rec in chosen.items():
        items_of[rec["uuid"]].append(q)
    rows = []
    for q, rec in sorted(chosen.items(), key=lambda kv: int(kv[0][1:])):
        if len(items_of[rec["uuid"]]) > 1:
            stats["UUID on several items"] += 1
            continue
        rows.append({"kind": "edit", "batch": "ids", "qid": q,
                     "rec": {k: rec[k] for k in ("uuid", "name", "type", "e", "n", "names")} | {"group": "id"}})
    path = plan_path("ids")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"release": release, "previous": ID_PREVIOUS_RELEASE, "items_in_scope": len(uuids_of),
                            "skipped": dict(stats)}, ensure_ascii=False) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    out(f"{release}: {len(uuids_of)} items paired in the log; plan: {len(rows)} IDs "
        f"{dict(Counter(r['rec']['type'] for r in rows).most_common())}; skipped {dict(stats)} -> {path}")


def build(batch=""):
    if batch == "ids":
        return build_ids()
    if batch == "p131":
        return build_p131()
    if batch == "stations":
        return build_stations()
    if batch == "areas":
        return build_areas()
    cfg = BATCHES[batch]
    classes, cache, tag = cfg["classes"], cfg["cache"], ({"batch": batch} if batch else {})
    recs, release = swissnames3d(cfg["types"])
    bounds = Boundaries()
    values = " ".join("wd:" + q for q in classes)
    scope = f"VALUES ?class {{ {values} }} ?item wdt:P31 ?class ; wdt:P17 wd:Q39 ."

    groups, reservoirs = defaultdict(set), set()
    for item, cls in sparql_tsv(f"SELECT ?item ?class WHERE {{ {scope} }}", cache + "scope.tsv"):
        groups[qid(item)].add(classes[qid(cls)])
        if qid(cls) == RESERVOIR:
            reservoirs.add(qid(item))
    coords = defaultdict(list)
    for item, c in sparql_tsv("SELECT ?item ?coord WHERE { ?item wdt:P17 wd:Q39 ; wdt:P625 ?coord . }", "coords.tsv"):
        if "(" in c:                                  # skip "unknown value"
            coords[qid(item)].append(point(c))
    names = defaultdict(set)
    for lg in NAME_LANGS:                             # one query per language; a single UNION query times out
        for item, n in sparql_tsv("SELECT ?item ?name WHERE { ?item wdt:P17 wd:Q39 ; wdt:P625 ?c ; rdfs:label ?name . "
                                  f'FILTER(LANG(?name) = "{lg}") }}', f"labels_{lg}.tsv"):
            names[qid(item)].add(literal(n)[0])
    for item, n in sparql_tsv(f"SELECT ?item ?name WHERE {{ {scope} ?item skos:altLabel ?name . }}", cache + "aliases.tsv"):
        names[qid(item)].add(literal(n)[0])
    has_p131 = {qid(r[0]) for r in sparql_tsv(f"SELECT DISTINCT ?item WHERE {{ {scope} ?item wdt:P131 ?x . }}",
                                              cache + "p131.tsv")}
    muni_item = defaultdict(set)
    for item, code in sparql_tsv("SELECT ?item ?code WHERE { ?item wdt:P31 wd:Q70208 ; wdt:P771 ?code . "
                                 "MINUS { ?item wdt:P576 ?end } }", "municipalities.tsv"):
        muni_item[int(literal(code)[0])].add(qid(item))
    muni_item = {k: next(iter(v)) for k, v in muni_item.items() if len(v) == 1}

    def municipalities(e, n):
        hits = bounds.at(e, n)
        items = [muni_item.get(bfs) for bfs, _ in hits]
        return (items if hits and all(items) else []), sorted({c for _, c in hits})

    by_name = defaultdict(list)
    for rec in recs:
        by_name[rec["name"]].append(rec)

    # 1. pair existing items with swissNAMES3D features: exact name, compatible type, within the distance limit, unique
    stats, pairs, used = Counter(), {}, defaultdict(list)
    for q, grps in groups.items():
        if not coords[q]:
            stats["no coordinates"] += 1
            continue
        we, wn = coords[q][0]
        cands = [(math.hypot(r["e"] - we, r["n"] - wn), r) for nm in names[q] for r in by_name.get(nm, [])
                 if r["group"] in grps]
        cands = [(d, r) for d, r in cands if d <= RADIUS[r["group"]]]
        uniq = {}                                     # a pass and its road pass, or name variants, are one feature;
        for d, r in sorted(cands, key=lambda x: (x[0], not official(x[1]), x[1]["name"])):   # ties: official name first
            if not any(math.hypot(r["e"] - o["e"], r["n"] - o["n"]) <= 250 for _, o in uniq.values()):
                uniq[r["uuid"]] = (d, r)
        if len(uniq) != 1:
            stats["no match" if not uniq else "several candidates"] += 1
            continue
        d, r = next(iter(uniq.values()))
        pairs[q] = (d, r)
        used[r["uuid"]].append(q)
    for uuid, qs in used.items():
        if len(qs) > 1:                               # two items for one feature: possible duplicates, left alone
            stats["two items, one feature"] += len(qs)
            for q in qs:
                pairs.pop(q)

    rows = []
    for q, (d, r) in sorted(pairs.items(), key=lambda kv: int(kv[0][1:])):
        if r["type"] not in cfg["edit"]:              # paired with a feature that another batch edits
            continue
        row = {"kind": "edit", **tag, "qid": q, "dist": round(d, 1), "reservoir": q in reservoirs, "rec": r}
        if r["group"] in MUNI_GROUPS and q not in has_p131:
            row["munis"] = municipalities(r["e"], r["n"])[0]
        rows.append(row)

    # 2. features to create: official names of the listed types, with no item of the same name within 2 km (in a
    #    later batch, no unpaired item of the same kind and name within its "same_kind_name" distance either) and no
    #    item of the same kind nearby (the runner repeats the name checks with a live search)
    name_pts = defaultdict(list)
    for q, ns in names.items():
        for nm in ns:
            name_pts[nm.casefold()] += coords[q][:1]
    kind_pts, kind_name_pts = defaultdict(list), defaultdict(list)
    for q, grps in groups.items():
        for g in grps:
            kind_pts[g] += coords[q][:1]
            for nm in names[q] if g in cfg["same_kind_name"] and q not in pairs else ():
                kind_name_pts[g, nm.casefold()] += coords[q][:1]
    if batch and plan_path().exists():                # a later batch: the first batch's new items count as existing,
        for row in load()[1]:                         # whether or not they are saved yet
            if row["kind"] == "create":
                r = row["rec"]
                for nm in {r["name"]} | {n["name"] for n in r["names"]}:
                    name_pts[nm.casefold()].append((r["e"], r["n"]))
                kind_pts[r["group"]].append((r["e"], r["n"]))
    matched = {r["uuid"] for _, r in pairs.values()} | set(used)
    seen = set()
    for r in recs:
        if (r["uuid"] in matched or r["uuid"] in seen or r["type"] not in cfg["new"] or not r["lang"]
                or not official(r) or (r["type"] == "Ort" and r.get("pop") not in cfg.get("create_pop", ()))):
            continue
        if any(math.hypot(r["e"] - e, r["n"] - n) <= 2000 for e, n in name_pts.get(r["name"].casefold(), [])):
            continue
        far = cfg["same_kind_name"].get(r["group"])
        if far and any(math.hypot(r["e"] - e, r["n"] - n) <= far
                       for e, n in kind_name_pts.get((r["group"], r["name"].casefold()), [])):
            continue
        if any(math.hypot(r["e"] - e, r["n"] - n) <= NEAR_KIND[r["group"]] for e, n in kind_pts[r["group"]]):
            continue
        munis, cantons = municipalities(r["e"], r["n"])
        if cfg["ch_only"] and not cantons:            # Liechtenstein, or just across the border
            continue
        seen.add(r["uuid"])
        rows.append({"kind": "create", **tag, "cls": cfg["new"][r["type"]], "rec": r,
                     "canton": cantons[0] if len(cantons) == 1 else None,
                     "munis": munis if r["group"] in MUNI_GROUPS else []})
        if batch:                                     # the runner's live search would find this new item by name,
            for nm in {r["name"]} | {n["name"] for n in r["names"]}:      # so a same-named feature nearby is left out
                name_pts[nm.casefold()].append((r["e"], r["n"]))

    if batch:                                         # new point features name their municipality in the description
        single = sorted({r["munis"][0] for r in rows if r["kind"] == "create" and len(r.get("munis", [])) == 1})
        names = defaultdict(dict)
        for m, lab in sparql_tsv('SELECT ?m ?l WHERE { ?m wdt:P31 wd:Q70208 ; rdfs:label ?l '      # every Swiss municipality
                                 'FILTER(LANG(?l) IN ("en", "de", "fr", "it", "mul")) }', "muni_labels.tsv") if single else []:
            text, lang = literal(lab)
            names[qid(m)][lang] = text
        for r in rows:
            if r["kind"] == "create" and len(r.get("munis", [])) == 1 and names.get(r["munis"][0]):
                r["muni_names"] = names[r["munis"][0]]

    path = plan_path(batch)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"release": release, "items_in_scope": len(groups)}) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    creates = Counter(r["cls"] for r in rows if r["kind"] == "create")
    edits = sum(1 for r in rows if r["kind"] == "edit")
    out(f"{release}: {len(groups)} items in scope, {len(pairs)} paired, skipped {dict(stats)}")
    out(f"plan: {edits} items to check, {sum(creates.values())} items to create {dict(creates)}, "
        f"{sum(1 for r in rows if r.get('munis'))} with a municipality -> {path}")


def load(batch=""):
    with open(plan_path(batch), encoding="utf-8") as f:
        header = json.loads(f.readline())
        return header, [json.loads(line) for line in f]
