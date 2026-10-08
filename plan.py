"""Build the work plan locally: pair Wikidata items with swissNAMES3D features and list the features to create.

Everything here runs on downloaded data (swissNAMES3D, swissBOUNDARIES3D, a QLever snapshot of Wikidata).
The runner re-checks every item live before it edits, so the plan only needs the pairs, not the changes.
"""
import json
import math
from collections import Counter, defaultdict

from common import (CLASSES, NAME_LANGS, NEW_CLASS, OFFICIAL_TYPES, RADIUS, RESERVOIR, WORK, Boundaries, literal, out,
                    point, qid, sparql_tsv, swissnames3d)

PLAN = WORK / "plan.jsonl"


def build():
    recs, release = swissnames3d()
    bounds = Boundaries()
    values = " ".join("wd:" + q for q in CLASSES)
    scope = f"VALUES ?class {{ {values} }} ?item wdt:P31 ?class ; wdt:P17 wd:Q39 ."

    groups, reservoirs = defaultdict(set), set()
    for item, cls in sparql_tsv(f"SELECT ?item ?class WHERE {{ {scope} }}", "scope.tsv"):
        groups[qid(item)].add(CLASSES[qid(cls)])
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
    for item, n in sparql_tsv(f"SELECT ?item ?name WHERE {{ {scope} ?item skos:altLabel ?name . }}", "aliases.tsv"):
        names[qid(item)].add(literal(n)[0])
    has_p131 = {qid(r[0]) for r in sparql_tsv(f"SELECT DISTINCT ?item WHERE {{ {scope} ?item wdt:P131 ?x . }}", "p131.tsv")}
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
        uniq = {}                                     # a pass and its road pass, or name variants, are one feature
        for d, r in sorted(cands, key=lambda x: x[0]):
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
        row = {"kind": "edit", "qid": q, "dist": round(d, 1), "reservoir": q in reservoirs, "rec": r}
        if r["group"] in ("summit", "pass") and q not in has_p131:
            row["munis"] = municipalities(r["e"], r["n"])[0]
        rows.append(row)

    # 2. features to create: official names of the listed types, with no item of the same name within 2 km
    #    and no item of the same kind nearby (the runner repeats the name check with a live search)
    name_pts = defaultdict(list)
    for q, ns in names.items():
        for nm in ns:
            name_pts[nm.casefold()] += coords[q][:1]
    kind_pts = defaultdict(list)
    for q, grps in groups.items():
        for g in grps:
            kind_pts[g] += coords[q][:1]
    near_kind = {"summit": 250, "pass": 250, "lake": 1000, "glacier": 1000}
    matched = {r["uuid"] for _, r in pairs.values()} | set(used)
    seen = set()
    for r in recs:
        if (r["uuid"] in matched or r["uuid"] in seen or r["type"] not in NEW_CLASS or not r["lang"]
                or r["status"] != "offiziell" or r["ntype"] not in OFFICIAL_TYPES):
            continue
        if any(math.hypot(r["e"] - e, r["n"] - n) <= 2000 for e, n in name_pts.get(r["name"].casefold(), [])):
            continue
        if any(math.hypot(r["e"] - e, r["n"] - n) <= near_kind[r["group"]] for e, n in kind_pts[r["group"]]):
            continue
        seen.add(r["uuid"])
        munis, cantons = municipalities(r["e"], r["n"])
        rows.append({"kind": "create", "cls": NEW_CLASS[r["type"]], "rec": r,
                     "canton": cantons[0] if len(cantons) == 1 else None,
                     "munis": munis if r["group"] in ("summit", "pass") else []})

    with open(PLAN, "w", encoding="utf-8") as f:
        f.write(json.dumps({"release": release, "items_in_scope": len(groups)}) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    creates = Counter(r["cls"] for r in rows if r["kind"] == "create")
    out(f"{release}: {len(groups)} items in scope, {len(pairs)} paired, skipped {dict(stats)}")
    out(f"plan: {len(pairs)} items to check, {sum(creates.values())} items to create {dict(creates)}, "
        f"{sum(1 for r in rows if r.get('munis'))} with a municipality -> {PLAN}")


def load():
    with open(PLAN, encoding="utf-8") as f:
        header = json.loads(f.readline())
        return header, [json.loads(line) for line in f]
