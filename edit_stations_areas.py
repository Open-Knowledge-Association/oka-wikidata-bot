"""Edits of the batches "stations" and "areas": one live item plus its swisstopo record into a single edit.

Same principles as edit.py: nothing is removed, existing labels, descriptions, classes and statements are left as
they are (a statement only ever gains a reference), and every statement added or confirmed cites the dataset.

stations (swissNAMES3D stops):
- swisstopo as a source for a position within 50 m of the stop (an old position saved without a precision gets the
  one its digits imply, as in edit.py; the position itself is unchanged);
- the official name (P1448) in the name's language, when the item has none in that language;
- the municipality (P131) from swissBOUNDARIES3D, when the item has none;
- no labels, heights or classes.

areas (swissBOUNDARIES3D municipality polygons), with the rules of BATCHES["areas"]["rules"] (see common.py):
- an equal area (same whole hectares) without point in time, or with the release date, gets the source;
- otherwise the area is added in km² with point in time (P585) = the date the boundaries are valid on, unless the
  rules say to leave that case alone; an equal value someone deprecated, or a different value already dated the
  release date, is left alone.
"""
import copy

from common import (BATCHES, HECTARE, KM2, M2, OFFICIAL_TYPES, STATION_RADIUS, SWISSBOUNDARIES3D, SWISSNAMES3D,
                    area_claim, cites, dist_m, item_claim, lv95_to_wgs84, mono_claim)
from edit import build_edit, build_p131, implied_precision

PLAIN_QUALIFIERS = {"P585", "P459", "P7452"}   # point in time, determination method, reason for preferred rank


def build_station(raw, row, refs):
    """Return (data for wbeditentity or None, list of changes or a reason it was skipped)."""
    rec, ref = row["rec"], refs[SWISSNAMES3D]
    if raw.get("redirects") or "missing" in raw:
        return None, ["skipped: redirect or deleted"]
    item_names = {v["value"] for v in raw.get("labels", {}).values()} | \
                 {a["value"] for al in raw.get("aliases", {}).values() for a in al}
    if rec["name"] not in item_names:
        return None, ["skipped: name no longer on item"]
    claims = raw.get("claims", {})
    coords = [c for c in claims.get("P625", []) if c["mainsnak"]["snaktype"] == "value" and c["rank"] != "deprecated"]
    if not coords:
        return None, ["skipped: no coordinates"]
    lat, lon = lv95_to_wgs84(rec["e"], rec["n"])
    cv = coords[0]["mainsnak"]["datavalue"]["value"]
    offset = dist_m(cv["latitude"], cv["longitude"], lat, lon)
    if offset > STATION_RADIUS:
        return None, ["skipped: coordinates moved"]

    out_claims, done = [], []
    if offset <= 50 and not cites(coords[0], SWISSNAMES3D):
        c = copy.deepcopy(coords[0])
        c.setdefault("references", []).append(ref)
        out_claims.append(c)
        done.append("source for position")
        v = c["mainsnak"]["datavalue"]["value"]
        if v.get("precision") is None:      # old imports lack it, and Wikidata won't take the claim back without one
            v["precision"] = implied_precision(v["latitude"], v["longitude"])
            done.append("precision of position (from its digits)")
    if rec["status"] == "offiziell" and rec["ntype"] in OFFICIAL_TYPES and rec["lang"]:
        same_lang = [c["mainsnak"]["datavalue"]["value"] for c in claims.get("P1448", [])
                     if c["mainsnak"]["snaktype"] == "value"
                     and c["mainsnak"]["datavalue"]["value"]["language"].split("-")[0] == rec["lang"]]
        if not same_lang:                   # an official name in this language (e.g. a former one) is left alone
            out_claims.append(mono_claim("P1448", rec["name"], rec["lang"], ref))
            done.append("official name")
    if row.get("munis") and not claims.get("P131"):
        out_claims += [item_claim("P131", m, refs[SWISSBOUNDARIES3D]) for m in row["munis"]]
        done.append("municipality")
    return ({"claims": out_claims} if out_claims else None), done


def hectares(c):
    """Area of a statement in hectares (km², ha or m²), or None for another unit or no value."""
    if c["mainsnak"]["snaktype"] != "value":
        return None
    v = c["mainsnak"]["datavalue"]["value"]
    factor = {KM2: 100, HECTARE: 1, M2: 1e-4}.get(v.get("unit"))
    return float(v["amount"]) * factor if factor else None


def _dates(c):
    return [q["datavalue"]["value"]["time"] for q in c.get("qualifiers", {}).get("P585", []) if q["snaktype"] == "value"]


def _plain(c, date):
    """Nothing limits the statement (no start or end, no part, no other point in time than the release date)."""
    return {p for p, v in c.get("qualifiers", {}).items() if v} <= PLAIN_QUALIFIERS and _dates(c) in ([], [date])


def build_area(raw, row, refs, rules=None):
    """Return (data for wbeditentity or None, list of changes or a reason it was skipped)."""
    rules = rules or BATCHES["areas"]["rules"]
    rec, ref, date = row["rec"], refs[SWISSBOUNDARIES3D], row["date"]
    ha = rec["area_ha"]
    if raw.get("redirects") or "missing" in raw:
        return None, ["skipped: redirect or deleted"]
    codes = [c["mainsnak"]["datavalue"]["value"] for c in raw.get("claims", {}).get("P771", [])
             if c["mainsnak"]["snaktype"] == "value" and c["rank"] != "deprecated"]
    if rec["bfs"] not in {int(v) for v in codes if v.strip().isdigit()}:     # some are saved as "0029"
        return None, ["skipped: BFS number no longer on item"]
    areas = raw.get("claims", {}).get("P2046", [])
    equal = [c for c in areas if hectares(c) is not None and abs(hectares(c) - ha) < 0.5]
    if any(c["rank"] == "deprecated" for c in equal):
        return None, ["skipped: someone deprecated this value"]
    live = [c for c in areas if c["rank"] != "deprecated"]
    if any(date in _dates(c) for c in live if c not in equal):
        return None, ["skipped: another value for the same date"]
    plain = [c for c in equal if _plain(c, date)]   # an equal value without a date (or with this one): source it
    if plain:
        if any(cites(c, SWISSBOUNDARIES3D) for c in plain):
            return None, []
        c = copy.deepcopy(plain[0])
        c.setdefault("references", []).append(ref)
        return {"claims": [c]}, ["source for area"]
    if equal:                               # the same value, but only for another time
        mode = rules["equal_dated"]
        if mode == "skip":
            return None, []
        if mode == "source":
            if any(cites(c, SWISSBOUNDARIES3D) for c in equal):
                return None, []
            c = copy.deepcopy(equal[0])
            c.setdefault("references", []).append(ref)
            return {"claims": [c]}, ["source for area"]
        return {"claims": [area_claim(ha, date, ref, rank_for(live, rules))]}, ["area"]
    if live and rules["different"] == "skip":
        return None, ["skipped: different area on item"]
    return {"claims": [area_claim(ha, date, ref, rank_for(live, rules))]}, ["area"]


def rank_for(live, rules):
    """Preferred (as the most recent value) when the item has older values and none is preferred yet; else normal,
    so that an item never ends up with two preferred areas (Chur: a preferred 2021 value stays as it is)."""
    if rules["rank"] == "preferred" and live and not any(c["rank"] == "preferred" for c in live):
        return "preferred"
    return "normal"


def area_seen(entity, row):
    """Read-back check: a P2046 statement with the plan's value that cites swissBOUNDARIES3D."""
    return any(cites(c, SWISSBOUNDARIES3D) and hectares(c) is not None and abs(hectares(c) - row["rec"]["area_ha"]) < 0.5
               for c in entity.get("claims", {}).get("P2046", []))


BUILDERS = {"p131": build_p131, "stations": build_station, "areas": build_area}   # batches with their own edit rules


def builder(row):
    """The function that turns an edit row of a plan, plus its live item, into one edit (for pipeline.py: run_batch
    and preview call builder(row)(raw, row, refs) where they called build_edit or build_p131)."""
    return BUILDERS.get(row.get("batch", ""), build_edit)


def describe(raw, row, data):
    """One line on what an edit would add, for checking samples by hand."""
    rec, bits = row["rec"], []
    for c in (data or {}).get("claims", []):
        pid, v = c["mainsnak"]["property"], c["mainsnak"]["datavalue"]["value"]
        if c.get("id"):                                   # an existing statement that gains the source
            bits.append(f"source on {pid}" + (f" {v['amount']}" if pid == "P2046" else ""))
        elif pid == "P1448":
            bits.append(f"P1448 {v['text']!r}@{v['language']}")
        elif pid == "P131":
            bits.append(f"P131 {v['id']}")
        elif pid == "P2046":
            bits.append(f"P2046 {v['amount']} km2 at {c['qualifiers']['P585'][0]['datavalue']['value']['time'][1:11]}"
                        + (" (preferred)" if c["rank"] == "preferred" else ""))
    if row.get("batch") == "areas":
        have = [(round(hectares(c) / 100, 2) if hectares(c) is not None else "?", c["rank"][:3],
                 ",".join(t[1:5] for t in _dates(c)) or "undated") for c in raw.get("claims", {}).get("P2046", [])]
        bits.append(f"| swisstopo {rec['area_ha'] / 100:.2f} km2 (BFS {rec['bfs']}); on item: {have or 'none'}")
    else:
        bits.append(f"| {rec['type']} {rec['name']!r}, {row['dist']} m, {row['points']} point(s)")
    return "; ".join(bits)


def main():
    """Read-only preview of the pending rows of a batch, from live data (no login, nothing is written)."""
    import argparse
    import random
    import sys
    from collections import Counter
    import pipeline
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["preview"])
    ap.add_argument("--plan", choices=["stations", "areas"], required=True)
    ap.add_argument("--n", type=int, default=10, help="the first N pending rows")
    ap.add_argument("--sample", type=int, default=0, help="instead, K pending rows at random, shown one by one")
    a = ap.parse_args()
    rows = pipeline.pending("edit", a.plan)
    rows = random.Random(1).sample(rows, min(a.sample, len(rows))) if a.sample else rows[:a.n]
    refs, live = pipeline.refs_for_today(), {}
    for i in range(0, len(rows), 50):
        live.update(pipeline.fetch([r["qid"] for r in rows[i:i + 50]]))
    totals, changed = Counter(), 0
    for r in rows:
        raw = live.get(r["qid"], {"missing": ""})
        data, done = builder(r)(raw, r, refs)
        changed += bool(data)
        totals.update(["(no change)"] if not done else [d.split(" (")[0] for d in done])
        if a.sample or len(rows) <= 30:
            print(f"{r['qid']:>11} {r['rec']['name'][:30]:30} {'; '.join(done) or 'no change'}  [{describe(raw, r, data)}]")
    print(f"{len(rows)} items, {changed} with an edit: {dict(totals.most_common())}")


if __name__ == "__main__":
    main()
