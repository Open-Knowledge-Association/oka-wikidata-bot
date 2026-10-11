"""Build the work plan locally: pair Wikidata items with swissNAMES3D features and list the features to create.

Everything here runs on downloaded data (swissNAMES3D, swissBOUNDARIES3D, a QLever snapshot of Wikidata).
The runner re-checks every item live before it edits, so the plan only needs the pairs, not the changes.
Each batch (common.BATCHES) has its own plan file: work/plan.jsonl for the first, work/plan_<batch>.jsonl after.
"""
import json
import math
from collections import Counter, defaultdict

from common import (BATCHES, NAME_LANGS, OFFICIAL_TYPES, RADIUS, RESERVOIR, WORK, Boundaries, literal, out, point, qid,
                    sparql_tsv, swissnames3d)

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


def build(batch=""):
    if batch == "p131":
        return build_p131()
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
