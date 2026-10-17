"""ORCID iDs (P496) that QuickStatements batch #40464 ("add missing ORCID via GND", User:Printstream, July 2026) put
on items of people who cannot be the ORCID holder: a read-only scan. Nothing is edited, and the ORCID API is not asked.

The DNB added ORCID iDs to GND person records by machine matching (Culturegraph, origin code cg001: names and title
data against the ORCID dump), mostly never checked. Batch #40464 copied about 82,000 of them onto the Wikidata items
with that GND ID (P227), often onto a GND-based item that duplicates an older author item, and sometimes onto someone
else: the actress Elke Arendt (Q1331446) got the iD of the food scientist Elke K. Arendt (Q88075585).

  python orcid_wrong.py batch       the items the batch edited, read from Printstream's contributions
  python orcid_wrong.py fetch       both items of every ORCID iD on exactly two items
  python orcid_wrong.py shapes [N]  the reference shapes of their P496 statements, and N edit histories (default 30)
  python orcid_wrong.py scan        the checks -> work/merges/orcid_wrong.json and orcid_wrong.wiki

The batch's statements: a P496 whose only reference is the item's GND ID (P227), on an item the batch edited (or
into which such an item was merged since). Checked: both items of every iD on exactly two items, and the batch's iDs
elsewhere that a QLever query finds with an early date or such an occupation. Listed when (a) died before October
2012, (b) born before 1900, (c) every occupation is of a non-research kind (merge_sample.clearly_not_research), or
(d) the two items are marked different (P1889; P460 "said to be the same" pairs are only recorded). Each listed item
gets its GND record from lobid.org; items where no rule that fired is firm go under "Check first" (see doubts()).

Each step caches what it fetched under work/merges/cache/ (scan runs the steps before it itself), so a rerun asks
again only for what is missing; delete a cache file to fetch it anew. Wikidata, QLever and lobid.org are each asked
about once a second.
"""
import collections
import datetime
import json
import random
import re
import sqlite3
import sys
import time
import zlib

import dictionaries as d
import merge_sample as ms
from common import IMPORT_ONLY, ROOT, curl_json

OUT = ROOT / "work" / "merges"
CACHE = OUT / "cache"
BATCH = "40464"
BATCH_USER = "Printstream"
BATCH_WINDOW = ("2026-07-08T00:00:00Z", "2026-07-09T14:00:00Z")   # found by sampling the user's edits every 12 hours
BATCH_LINK = f"[[:toollabs:qs-dev/batch/{BATCH}|batch #{BATCH}]]"
GND = "Q36578"
ORCID_OPENED = (2012, 10, 1)     # ORCID opened its registry in October 2012: nobody who had died by then registered
BORN_BEFORE = (1900, 1, 1)
PFX = ("PREFIX p: <http://www.wikidata.org/prop/> PREFIX ps: <http://www.wikidata.org/prop/statement/> "
       "PREFIX pr: <http://www.wikidata.org/prop/reference/> PREFIX prov: <http://www.w3.org/ns/prov#> ")
GND_REF = "{ ?r pr:P227 ?g } UNION { ?r pr:P248 wd:Q36578 }"
RULES = {"a": "died before October 2012", "b": "born before 1900",
         "c": "occupation that does not come with an ORCID iD", "d": "marked as different from the other item"}


def out(*a):
    print(*a, flush=True)


def today():
    return datetime.date.today().isoformat()


def api(**params):
    d.wait_turn("www.wikidata.org", 1.0)
    return ms.api(**params)


def cached_json(name, make):
    path = CACHE / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = make()
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ---------------------------------------------------------------- what to look at

def pairs():
    """[(iD, (older item, newer item))] of every ORCID iD on exactly two items (QLever), and where they come from."""
    def make():
        found, origin = ms.all_pairs()
        return {"origin": origin, "date": today(), "pairs": found}
    data = cached_json("pairs.json", make)
    return [(o, tuple(ab)) for o, ab in data["pairs"]], data["origin"], data["date"]


def batch_edits():
    """{item: [[revision id, timestamp], ...]} of every edit of the batch, from Printstream's contributions in the
    batch's window: its edit summaries read 'QuickStatements 3.0 batch #40464: add missing ORCID via GND'."""
    def make():
        edits, other, cont, n = {}, collections.Counter(), {}, 0
        while True:
            r = api(action="query", list="usercontribs", ucuser=BATCH_USER, uclimit=500, ucdir="newer",
                    ucstart=BATCH_WINDOW[0], ucend=BATCH_WINDOW[1], ucprop="ids|title|timestamp|comment", **cont)
            for c in r["query"]["usercontribs"]:
                comment = c.get("comment", "")
                if f"batch/{BATCH}|" in comment:
                    edits.setdefault(c["title"], []).append([c["revid"], c["timestamp"]])
                else:
                    m = re.search(r"batch/(\d+)\|", comment)
                    other["batch #" + m.group(1) if m else "not a batch"] += 1
            n += 1
            if n % 20 == 0:
                out(f"  contributions: {n} requests, {sum(map(len, edits.values())):,} batch edits so far")
            if "continue" not in r:
                break
            cont = r["continue"]
        stamps = sorted(t for v in edits.values() for _, t in v)
        return {"window": BATCH_WINDOW, "edits": edits, "other": dict(other),
                "first": stamps[0] if stamps else None, "last": stamps[-1] if stamps else None}
    return cached_json(f"batch{BATCH}.json", make)


def gnd_statements():
    """Every P496 statement whose reference cites the GND (a GND ID in it, or stated in GND), with the item's
    best-rank dates of birth and death and occupations, from QLever: a snapshot, used only to choose which items
    outside the pairs to fetch and check live."""
    def make():
        where = "?i p:P496 ?s . ?s prov:wasDerivedFrom ?r . " + GND_REF
        got = {"ids": d.qlever(PFX + "SELECT DISTINCT ?i ?o WHERE { ?i p:P496 ?s . ?s ps:P496 ?o ; "
                                     "prov:wasDerivedFrom ?r . " + GND_REF + " }")}
        for key, pid in (("born", "P569"), ("died", "P570"), ("occupations", "P106")):
            got[key] = d.qlever(PFX + f"SELECT DISTINCT ?i ?v WHERE {{ {where} ?i wdt:{pid} ?v }}")
        got["shared"] = d.qlever("SELECT ?o (COUNT(DISTINCT ?i) AS ?n) WHERE { ?i wdt:P496 ?o } GROUP BY ?o "
                                 "HAVING (COUNT(DISTINCT ?i) > 2)")
        got["date"] = today()
        return got
    return cached_json("qlever_gnd.json", make)


# ---------------------------------------------------------------- items, cached

_db = None


def db():
    global _db
    if _db is None:
        CACHE.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(str(CACHE / "items.sqlite"))
        _db.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, fetched TEXT, data BLOB)")
    return _db


def fetch(qs):
    """Fetch the items not yet in the cache: 50 a request, a second apart (d.items)."""
    have = {r[0] for r in db().execute("SELECT id FROM items")}
    todo = sorted(set(qs) - have, key=lambda q: int(q[1:]))
    if not todo:
        return
    out(f"fetching {len(todo):,} items ({len(have):,} cached)")
    t0 = time.time()
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        got = d.items(chunk)
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        db().executemany("INSERT OR REPLACE INTO items VALUES (?, ?, ?)",
                         [(q, now, zlib.compress(json.dumps(got.get(q) or {"id": q, "missing": ""},
                                                            ensure_ascii=False).encode("utf-8"))) for q in chunk])
        db().commit()
        k = i // 50 + 1
        if k % 25 == 0 or i + 50 >= len(todo):
            left = (time.time() - t0) / (i + len(chunk)) * (len(todo) - i - len(chunk))
            out(f"  {i + len(chunk):,} of {len(todo):,} items, about {left / 60:.0f} min left")


def get(q):
    row = db().execute("SELECT data FROM items WHERE id = ?", (q,)).fetchone()
    return json.loads(zlib.decompress(row[0]).decode("utf-8")) if row else None


def gone(e):
    return not e or "missing" in e or bool(e.get("redirects"))


# ---------------------------------------------------------------- statements and references

def statements(e, orcid):
    return [c for c in e.get("claims", {}).get("P496", []) if (c["mainsnak"].get("datavalue") or {}).get("value") == orcid]


def ref_shape(ref):
    """A reference as the properties it uses, with the item it is stated in: 'P227', 'P248=Q36578+P227+P813'."""
    parts = []
    for pid in sorted(ref.get("snaks", {}), key=lambda p: int(p[1:])):
        if pid == "P248":
            parts.append("P248=" + "/".join(sorted(str(((s.get("datavalue") or {}).get("value") or {}).get("id"))
                                                   for s in ref["snaks"][pid])))
        else:
            parts.append(pid)
    return "+".join(parts)


def shape(st):
    return " | ".join(sorted(ref_shape(r) for r in st.get("references", []))) or "(no reference)"


def gnd_cited(st):
    """The GND IDs a statement's references give (P227 in a reference), and whether one is 'stated in: GND'."""
    ids, stated = [], False
    for ref in st.get("references", []):
        snaks = ref.get("snaks", {})
        ids += [s["datavalue"]["value"] for s in snaks.get("P227", []) if s.get("datavalue")]
        stated |= any(((s.get("datavalue") or {}).get("value") or {}).get("id") == GND for s in snaks.get("P248", []))
    return ids, stated


_merged = None


def merged_into(q):
    """The items merged into q since the batch ran (their statements came along), from q's edit summaries."""
    global _merged
    path = CACHE / "merged_into.json"
    if _merged is None:
        _merged = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if q not in _merged:
        r = api(action="query", prop="revisions", titles=q, rvlimit=500, rvprop="comment", rvend=BATCH_WINDOW[0])
        revs = r["query"]["pages"][0].get("revisions", [])
        _merged[q] = sorted({m for v in revs for m in re.findall(r"wbmergeitems-from:0\|\|(Q\d+)", v.get("comment", ""))})
        path.write_text(json.dumps(_merged), encoding="utf-8")
    return _merged[q]


def from_batch(q, st, edits):
    """Whether this P496 statement is one the batch added: its reference cites the GND (the batch's reference is the
    item's GND ID, P227, alone), and the batch edited the item or an item it edited was merged into it since."""
    if not any(gnd_cited(st)):
        return False
    return q in edits or any(m in edits for m in merged_into(q))


# ---------------------------------------------------------------- the checks

def best(e, pid):
    """Statements of the best rank, as the truthy wdt: values have them: preferred if any, else normal."""
    cs = [c for c in e.get("claims", {}).get(pid, []) if c["rank"] != "deprecated"]
    return [c for c in cs if c["rank"] == "preferred"] or cs


def dates(e, pid):
    return [(c["mainsnak"]["datavalue"]["value"]["time"], c["mainsnak"]["datavalue"]["value"]["precision"])
            for c in best(e, pid) if c["mainsnak"]["snaktype"] == "value"]


def latest(t, prec):
    """The last day a date can mean: 1957 (a year) is up to 1957-12-31, the 1950s up to 1959-12-31; a century or a
    millennium is taken generously (+1900 may be meant as the 19th or the 20th century: up to 2000-12-31)."""
    m = re.match(r"([+-]\d+)-(\d\d)-(\d\d)", t)
    if not m:
        return None
    y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if prec >= 11 and mo and dd:
        return y, mo, dd
    if prec == 10 and mo:
        return y, mo, 31
    if prec >= 9:
        return y, 12, 31
    if prec == 8:
        return y - y % 10 + 9, 12, 31
    if prec == 7:
        return (y // 100 + 1) * 100, 12, 31
    if prec == 6:
        return (y // 1000 + 1) * 1000, 12, 31
    return None


def before(ds, limit):
    """Whether the item has such dates and every one of them lies wholly before the limit."""
    ends = [latest(t, p) for t, p in ds]
    return bool(ends) and all(x is not None and x < limit for x in ends)


def when(t, prec):
    y = int(re.match(r"[+-]\d+", t).group())
    yy = f"{-y} BCE" if y < 0 else str(y)
    return yy if prec >= 9 else f"{yy}s" if prec == 8 else f"c. {yy}"


def rules(q, e, other=None, eo=None):
    """The rules the item breaks for carrying an ORCID iD: {rule: evidence}."""
    fired = {}
    died = dates(e, "P570")
    if before(died, ORCID_OPENED):
        fired["a"] = [when(*x) for x in died]
    born = dates(e, "P569")
    if before(born, BORN_BEFORE):
        fired["b"] = [when(*x) for x in born]
    occ = ms.values(e, "P106", "id")
    if ms.clearly_not_research(occ):
        fired["c"] = sorted(set(occ))
    if other and eo and linked(q, e, other, eo, "P1889"):
        fired["d"] = ["P1889"]
    return fired


def linked(q, e, other, eo, pid):
    """Whether either item points to the other with this property (P1889 different from, P460 said to be the same)."""
    return other in ms.values(e, pid, "id") or q in ms.values(eo, pid, "id")


def sourced(e, pid):
    """How the item's best-rank values of a date are referenced: 'referenced', 'only from a Wikipedia' (imported
    from a Wikimedia project, P143/P4656, at most with a retrieval date) or 'unreferenced'."""
    refs = [r.get("snaks", {}) for c in best(e, pid) for r in c.get("references", [])]
    if any(set(s) - IMPORT_ONLY for s in refs):
        return "referenced"
    return "only from a Wikipedia" if any({"P143", "P4656"} & set(s) for s in refs) else "unreferenced"


def years(e, pid):
    return sorted({when(*x) for x in dates(e, pid)})


def about(q, e):
    """What the list shows of an item."""
    return {"item": q, "label": ms.label(e), "gnd": ms.values(e, "P227"), "born": years(e, "P569"),
            "died": years(e, "P570"), "occupations": sorted(set(ms.values(e, "P106", "id"))),
            "instance_of": sorted(set(ms.values(e, "P31", "id")))}


# ---------------------------------------------------------------- the shapes and histories (how the signal was chosen)

def history(q, orcid, limit=500):
    """The item's edits whose summary names this iD, P496, or the batch."""
    r = api(action="query", prop="revisions", titles=q, rvlimit=limit, rvprop="ids|timestamp|user|comment")
    page = r["query"]["pages"][0]
    revs = page.get("revisions", [])
    hits = [(v["timestamp"], v.get("user", ""), v.get("comment", "")) for v in revs
            if orcid in v.get("comment", "") or "P496" in v.get("comment", "") or f"batch/{BATCH}|" in v.get("comment", "")]
    return hits, len(revs), revs[-1]["timestamp"] if revs else None


def shapes(n_hist=30):
    edits = batch_edits()["edits"]
    prs = pairs()[0]
    fetch([q for _, ab in prs for q in ab])
    table, examples = collections.Counter(), collections.defaultdict(list)
    for orcid, ab in prs:
        for q in ab:
            e = get(q)
            if gone(e):
                continue
            for st in statements(e, orcid):
                key = ("edited by the batch" if q in edits else "not edited by the batch", shape(st))
                table[key] += 1
                examples[key].append((orcid, q, st))
    out("\nReference shapes of the P496 statements (the pair's iD) on the pair items:")
    for (inb, shp), n in table.most_common():
        out(f"  {n:6,}  {inb:24}  {shp}")
    rnd = random.Random(20261017)
    out("\nExamples (iD, item, rank, references with their values, the item's own GND IDs):")
    shown = []
    for key in [k for k, _ in table.most_common()][:12]:
        for orcid, q, st in rnd.sample(examples[key], min(4, len(examples[key]))):
            e = get(q)
            refs = [{p: [str((s.get("datavalue") or {}).get("value", "")).replace("'entity-type': 'item', ", "")
                         for s in v] for p, v in r["snaks"].items()} for r in st.get("references", [])]
            out(f"  {orcid} {q:11} {st['rank']:9} {refs}  item P227={ms.values(e, 'P227')}  [{key[0]}]")
            shown.append((orcid, q, key))
    out(f"\nEdit histories (summaries naming the iD, P496 or batch #{BATCH}), {n_hist} items:")
    main = ("edited by the batch", "P227")              # the batch's own reference: the item's GND ID alone
    picks = []
    for key in sorted((k for k, _ in table.most_common()), key=lambda k: k != main):
        want = n_hist // 3 if key == main else 2       # most of the batch's own shape, two of every other
        picks += [(key, x) for x in rnd.sample(examples[key], min(want, len(examples[key])))]
    found = collections.Counter()
    for key, (orcid, q, st) in picks[:n_hist]:
        hits, n, oldest = history(q, orcid)
        named = any(f"batch/{BATCH}|" in c for _, _, c in hits)
        found[(key[0], key[1], named)] += 1
        out(f"  {q} {orcid} [{key[0]}; {key[1]}] {n} revisions looked at"
            + ("" if n < 500 else f" (newest 500, back to {oldest})"))
        for ts, user, c in hits[:4]:
            out(f"      {ts} {user}: {c[:150]}")
    out("\nBatch named in the history, by shape:")
    for (inb, shp, named), n in sorted(found.items()):
        out(f"  {n:3}  {inb:24}  {shp:40}  {'batch named' if named else 'batch not named'}")


# ---------------------------------------------------------------- the scan

def labels(qs):
    path = CACHE / "labels.json"
    have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = sorted(set(qs) - set(have))
    for i in range(0, len(todo), 50):
        r = api(action="wbgetentities", ids="|".join(todo[i:i + 50]), props="labels", languages="en|mul|de|fr")
        for q in todo[i:i + 50]:
            have[q] = ms.label(r.get("entities", {}).get(q, {})) or q
    if todo:
        path.write_text(json.dumps(have, ensure_ascii=False), encoding="utf-8")
    return have


def cached_call(name, key, make):
    """make() for this key, kept in the cache file `name` (one JSON object for all keys)."""
    path = CACHE / name
    store = _stores.get(name)
    if store is None:
        store = _stores[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if key not in store:
        store[key] = make()
        path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return store[key]


_stores = {}


def holders(orcid):
    """The items that carry the iD now (Wikidata's search)."""
    def make():
        r = api(action="query", list="search", srsearch=f"haswbstatement:P496={orcid}", srlimit=10)
        return sorted((h["title"] for h in r["query"]["search"]), key=lambda q: int(q[1:]))
    return cached_call("holders.json", orcid, make)


def gnd_record(gid):
    """What the GND record (lobid.org) says of its person: name, life dates, occupations, notes, ORCID links."""
    def make():
        d.wait_turn("lobid.org", 1.0)
        try:
            j = curl_json(f"https://lobid.org/gnd/{gid}.json")
        except Exception as e:                        # noqa: BLE001 - a record it cannot read is shown as such
            return {"error": str(e)[:200]}
        jobs = [p for p in j.get("professionOrOccupation", []) if p.get("label")]
        return {"id": j.get("gndIdentifier"), "name": j.get("preferredName"), "born": j.get("dateOfBirth", []),
                "died": j.get("dateOfDeath", []), "occupations": [p["label"] for p in jobs],
                "occupation_ids": [(p.get("id") or "").rsplit("/", 1)[-1] for p in jobs],
                "info": j.get("biographicalOrHistoricalInformation", []),
                "affiliations": [a.get("label") for a in j.get("affiliation", []) if a.get("label")],
                "orcid": [s.get("id") for s in j.get("sameAs", []) if "orcid.org" in (s.get("id") or "")]}
    return cached_call("gnd.json", gid, make)


RESEARCH_JOB = re.compile(                      # GND occupations (German terms) that go with research
    r"(?i)wissenschaft|hochschul|dozent|forscher|ingenieur|mathemati|physik|chemik|biolog|histori|linguist|philolog|"
    r"anglist|hispanist|romanist|germanist|slawist|polonist|philosoph|theolog|psycholog|soziolog|politolog|ökonom|"
    r"kriminolog|ägyptolog|archäolog|ethnolog|geolog|astronom|informatik|statistik|mediziner|arzt$|ärztin|"
    r"bibliothekar|pädagog|jurist")
ARTS_JOB = re.compile(r"(?i)^(musik|instrumental|gesang|tanz|ballett|theater|film)(?!.*wissenschaft)")


def research_jobs(rec):
    """The GND record's occupations that go with research: Musikwissenschaftler, Hochschullehrer, Ägyptologe."""
    return [j for j in (rec or {}).get("occupations", []) if RESEARCH_JOB.search(j) and not ARTS_JOB.match(j)]


def loose_years(texts):
    return [int(m.group()) for t in texts for m in [re.search(r"-?\d+", str(t))] if m]


def plain_years(texts):
    return {int(t) for t in texts if re.fullmatch(r"-?\d{1,4}", str(t))}


def gnd_years(dates):
    return {int(m.group()) for t in dates for m in [re.match(r"-?\d{1,4}", str(t))] if m}


def same_person(row, rec):
    """Whether the GND record fits the item by the years of birth and death: 'dates differ' when the two give years
    more than one apart, or one died before the other was born (another person, or a wrong date on one side);
    'dates agree' when a year agrees within one; None when there is nothing to compare."""
    if not rec or rec.get("error"):
        return None
    bw, dw = plain_years(row["born"]), plain_years(row["died"])
    bg, dg = gnd_years(rec["born"]), gnd_years(rec["died"])

    def gap(a, b):
        return min(abs(x - y) for x in a for y in b)
    differ = (bw and bg and gap(bw, bg) > 1) or (dw and dg and gap(dw, dg) > 1) or \
        (dw and bg and max(dw) < min(bg)) or (bw and dg and max(dg) < min(bw))
    agree = (bw and bg and gap(bw, bg) <= 1) or (dw and dg and gap(dw, dg) <= 1)
    return "dates differ" if differ else "dates agree" if agree else None


def doubts(r):
    """Why a listed item might hold the iD rightly after all: a reason for each rule that fired, when none of them is
    firm, else nothing. Firm: (a) a referenced date of death, a death before 2012 or in the same year in the GND
    record, or a birth before 1920; (b) a referenced or GND-backed date of birth, with a date of death; (c) no
    research occupation in the GND record and no scholarly article by the item; (d) fewer scholarly articles by the
    item than by the other item."""
    rec = r.get("gnd_record") or {}
    born = loose_years(r["born"]) or sorted(gnd_years(rec.get("born", [])))
    g_born, g_died = sorted(gnd_years(rec.get("born", []))), sorted(gnd_years(rec.get("died", [])))
    reasons = []
    for k in r["rules"]:
        if k == "a":
            src, died = r["date_sources"]["a"], loose_years(r["died"])
            backed = g_died and (max(g_died) < ORCID_OPENED[0] or any(abs(x - y) <= 1 for x in g_died for y in died))
            if src == "referenced" or backed or born and max(born) < 1920:
                return []
            reasons.append(f"the GND record gives death in {'/'.join(map(str, g_died))}" if g_died else
                           "the GND record gives no date of death")
        elif k == "b":
            src = r["date_sources"]["b"]
            if r["died"] and (src == "referenced" or g_born and max(g_born) < BORN_BEFORE[0]):
                return []
            reasons += (["it has no date of death"] if not r["died"] else []) + \
                [f"the GND record gives birth in {'/'.join(map(str, g_born))}" if g_born else
                 "the GND record gives no date of birth"]
        elif k == "c":
            jobs, arts = research_jobs(rec), r.get("articles", 0)
            if not jobs and not arts:
                return []
            reasons += ([f"its GND record also says {', '.join(jobs)}"] if jobs else []) + \
                ([f"it is the author of {ms.plural(arts, 'scholarly article')} on Wikidata"] if arts else [])
        elif k == "d":
            mine, theirs = r.get("articles", 0), max([o.get("articles", 0) for o in r["other"]] or [0])
            if not mine or mine < theirs:
                return []
            reasons.append(f"it is the author of at least as many scholarly articles on Wikidata as the other item "
                           f"({mine} to {theirs})")
    return reasons


def ties(qs):
    """{item: (works with it as author (P50), scholarly articles among them, Wikimedia pages)} from QLever."""
    qs = sorted(set(qs))
    works, arts, pages = {}, {}, {}
    for i in range(0, len(qs), 500):
        vals = " ".join("wd:" + q for q in qs[i:i + 500])
        works.update({r[0]: int(r[1]) for r in d.qlever(
            f"SELECT ?a (COUNT(DISTINCT ?w) AS ?n) WHERE {{ VALUES ?a {{ {vals} }} ?w wdt:P50 ?a }} GROUP BY ?a")
            if len(r) == 2})
        arts.update({r[0]: int(r[1]) for r in d.qlever(
            f"SELECT ?a (COUNT(DISTINCT ?w) AS ?n) WHERE {{ VALUES ?a {{ {vals} }} ?w wdt:P50 ?a ; "
            f"wdt:P31 wd:Q13442814 }} GROUP BY ?a") if len(r) == 2})
        pages.update({r[0]: int(r[1]) for r in d.qlever(
            f"SELECT ?a ?n WHERE {{ VALUES ?a {{ {vals} }} ?a wikibase:sitelinks ?n }}") if len(r) == 2})
    return {q: (works.get(q, 0), arts.get(q, 0), pages.get(q, 0)) for q in qs}


def candidates(prs):
    """Items outside the pairs whose GND-cited iD may break a rule (QLever's snapshot): (iD, item) pairs."""
    g = gnd_statements()
    in_pairs = {o for o, _ in prs}
    occ = collections.defaultdict(set)
    for i, v in g["occupations"]:
        occ[i].add(v)
    def year(v):                                     # an unknown value comes as a blank-node hash: no year
        m = re.match(r"(-?\d+)-\d\d-\d\d", v)
        return int(m.group(1)) if m else 9999
    early = {r[0] for r in g["died"] if year(r[1]) <= ORCID_OPENED[0]} | \
            {r[0] for r in g["born"] if year(r[1]) <= BORN_BEFORE[0]}
    ms.clearly_not_research(["Q5"])                  # loads the classes
    odd = {i for i, s in occ.items() if s <= ms._not_research}
    return sorted({(o, i) for i, o in g["ids"] if o not in in_pairs and (i in early or i in odd)}), \
        {r[0]: int(r[1]) for r in g["shared"]}, len(g["ids"]), g["date"]


def scan():
    t0 = time.time()
    b = batch_edits()
    edits = b["edits"]
    out(f"batch #{BATCH}: {sum(map(len, edits.values())):,} edits on {len(edits):,} items, {b['first']} to {b['last']}")
    prs, origin, pairs_date = pairs()
    out(f"{len(prs):,} pairs ({origin}, {pairs_date})")
    fetch([q for _, ab in prs for q in ab])
    count = collections.Counter()
    rows, elsewhere, said_same = [], [], []

    def look(orcid, q, e, scope, other=None, eo=None):
        """Check the item's statement for this iD; a row if it came with the batch and breaks a rule."""
        sts = statements(e, orcid)
        if not sts:
            count[f"{scope}: iD no longer on the item"] += 1
            return False
        st = next((s for s in sts if from_batch(q, s, edits)), None)
        if st is None:
            cited = [s for s in sts if any(gnd_cited(s))]
            if cited:                                 # from another GND import: kept apart, not on the list
                count[f"{scope}: GND-cited iD not from the batch"] += 1
                fired = rules(q, e, other, eo)
                if fired:
                    elsewhere.append({"orcid": orcid, "scope": scope, "item": q, "label": ms.label(e),
                                      "rules": sorted(fired), "evidence": fired, "reference": shape(cited[0]),
                                      "rank": cited[0]["rank"], "other": other})
            return False
        count[f"{scope}: items with the batch's iD"] += 1
        if st["rank"] == "deprecated":
            count[f"{scope}: the batch's iD already deprecated"] += 1
            return True
        fired = rules(q, e, other, eo)
        if not fired:
            return True
        ids, stated = gnd_cited(st)
        row = {"orcid": orcid, "scope": scope, "rules": sorted(fired), "evidence": fired, **about(q, e),
               "date_sources": {k: sourced(e, pid) for k, pid in (("a", "P570"), ("b", "P569")) if k in fired},
               "gnd_in_reference": sorted(set(ids)), "rank": st["rank"], "reference": shape(st),
               "batch_edits": edits.get(q, []), "other": [about(other, eo)] if other else []}
        if q not in edits:                            # the batch edited an item merged into this one
            row["batch_edits"] = [[m] + x for m in merged_into(q) if m in edits for x in edits[m]]
        if other:
            row["other"][0]["iD_from_batch"] = any(from_batch(other, s, edits) for s in statements(eo, orcid))
        rows.append(row)
        return True

    for orcid, (a, bq) in prs:
        ea, eb = get(a), get(bq)
        if gone(ea) or gone(eb):
            count["pairs: no longer a pair (an item merged or deleted)"] += 1
            continue
        count["pairs: scanned"] += 1
        hit = [look(orcid, a, ea, "pair", bq, eb), look(orcid, bq, eb, "pair", a, ea)]
        count[f"pairs: the batch's iD on {sum(hit)} of the two items"] += 1
        if any(hit) and linked(a, ea, bq, eb, "P460"):    # said to be the same: duplicates, not listed
            said_same.append({"orcid": orcid, "items": [a, bq], "labels": [ms.label(ea), ms.label(eb)],
                              "batch_iD_on": [q for q, h in zip((a, bq), hit) if h]})

    cands, shared, n_gnd, snap = candidates(prs)
    out(f"{len(cands):,} GND-cited iDs outside the pairs may break a rule (QLever snapshot of {snap}, "
        f"{n_gnd:,} GND-cited P496 statements in all): fetching them")
    fetch([q for _, q in cands])
    for orcid, q in cands:
        e = get(q)
        if gone(e):
            count["outside pairs: item merged or deleted"] += 1
            continue
        look(orcid, q, e, "three or more items" if orcid in shared else "one item")
    for row in rows:                                  # outside the pairs: who carries the iD now
        if row["scope"] != "pair":
            now = holders(row["orcid"])
            others = [x for x in now if x != row["item"]]
            if others:
                fetch(others)
                row["other"] = [about(x, get(x)) for x in others if not gone(get(x))]
                row["scope"] = "pair" if len(now) == 2 else "three or more items"
                if len(others) == 1 and not gone(get(others[0])):
                    fired = rules(row["item"], get(row["item"]), others[0], get(others[0]))
                    row["rules"], row["evidence"] = sorted(fired), fired
                for o in row["other"]:
                    o["iD_from_batch"] = any(from_batch(o["item"], s, edits) for s in statements(get(o["item"]), row["orcid"]))
            else:
                row["scope"] = "one item"
            row["holders_now"] = now

    t = ties([r["item"] for r in rows] + [o["item"] for r in rows for o in r["other"]])
    lab = labels({x for r in rows for x in r["occupations"] + r["evidence"].get("c", [])}
                 | {x for r in rows for o in r["other"] for x in o["occupations"]})
    for r in rows:
        r["works"], r["articles"], r["sitelinks"] = t.get(r["item"], (0, 0, 0))
        r["occupation_labels"] = [lab.get(x, x) for x in r["occupations"]]
        for o in r["other"]:
            o["works"], o["articles"], o["sitelinks"] = t.get(o["item"], (0, 0, 0))
            o["occupation_labels"] = [lab.get(x, x) for x in o["occupations"]]
    out(f"reading the GND records of {len(rows)} items (lobid.org, cached)")
    for r in rows:                                    # whose GND record carried the iD: this person's or another's
        g = (r["gnd_in_reference"] or r["gnd"])[:1]
        r["gnd_record"] = gnd_record(g[0]) if g else None
        r["gnd_dates"] = same_person(r, r["gnd_record"])
    for r in rows:
        r["check"] = doubts(r)
    verdicts = collections.Counter((r["gnd_dates"] or "nothing to compare") for r in rows)
    rows.sort(key=lambda r: (r["scope"] != "pair", r["orcid"], r["item"]))
    by_rule = collections.Counter(x for r in rows for x in r["rules"])
    combos = collections.Counter("+".join(r["rules"]) for r in rows)
    data = {"date": today(), "batch": {"id": BATCH, "user": BATCH_USER, "edits": sum(map(len, edits.values())),
                                       "items": len(edits), "first": b["first"], "last": b["last"]},
            "pairs": {"origin": origin, "date": pairs_date, "total": len(prs)},
            "outside_pairs": {"qlever_snapshot": snap, "gnd_cited_statements": n_gnd, "candidates": len(cands)},
            "signal": "the P496 statement's reference cites the GND (the batch's reference is the item's GND ID, "
                      "P227, alone) and batch #40464 edited the item, or an item it edited was merged into it since "
                      "(the batch's edits read from Printstream's contributions, whose edit summaries name the batch)",
            "counts": dict(sorted(count.items())), "flagged": len(rows), "by_rule": dict(sorted(by_rule.items())),
            "rule_combinations": dict(combos.most_common()), "rules": RULES,
            "gnd_dates": dict(verdicts.most_common()), "to_check": sum(1 for r in rows if r["check"]),
            "said_to_be_same": said_same,
            "runtime_s": round(time.time() - t0), "rows": rows,
            "gnd_cited_not_from_batch": elsewhere}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "orcid_wrong.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "orcid_wrong.wiki").write_text(page(data, lab), encoding="utf-8")
    for k, v in sorted(count.items()):
        out(f"  {k}: {v:,}")
    out(f"{len(rows):,} flagged: " + ", ".join(f"{RULES[k]}: {v:,}" for k, v in sorted(by_rule.items()))
        + "; combinations " + ", ".join(f"{k}: {v}" for k, v in combos.most_common()))
    out("the GND record that carried the iD: " + ", ".join(f"{k}: {v}" for k, v in verdicts.most_common()))
    out(f"to check first: {data['to_check']}; pairs only said to be the same (P460), not listed: {len(said_same)}")
    out(f"done in {(time.time() - t0) / 60:.1f} min")


# ---------------------------------------------------------------- the page

def q_link(q):
    return f"{{{{Q|{q[1:]}}}}}"


def why(r, lab):
    parts = []
    ev, src = r["evidence"], r.get("date_sources", {})
    for k, what in (("a", "died"), ("b", "born")):
        if k in ev:
            note = "" if src.get(k) == "referenced" else f" <small>({src.get(k)})</small>"
            parts.append(f"{what} " + " or ".join(ev[k]) + note)
    if "c" in ev:
        parts.append("occupation: " + ", ".join(lab.get(x, x) for x in ev["c"]))
    if "d" in ev:
        parts.append("marked as different from the other item ({{P|1889}})")
    text = "; ".join(parts)
    if r.get("check"):
        text += "<br /><small>but " + "; ".join(r["check"]) + "</small>"
    return text


def works_note(x):
    """'author of 12 works (9 scholarly articles)': the works on Wikidata with the item as author (P50)."""
    n, a = x.get("works", 0), x.get("articles", 0)
    return f"<br /><small>author of {ms.plural(n, 'work')}{f' ({a} scholarly)' if a else ''}</small>" if n else ""


def gnd_cell(r):
    """The GND ID that carried the iD, with what its record says of its person."""
    gnds = r["gnd_in_reference"] or r["gnd"]
    cell = ", ".join(f"[https://d-nb.info/gnd/{g} {g}]" for g in gnds)
    rec = r.get("gnd_record") or {}
    if rec and not rec.get("error"):
        b, dd = sorted(gnd_years(rec.get("born", []))), sorted(gnd_years(rec.get("died", [])))
        life = f"{b[0]}–{dd[0]}" if b and dd else f"born {b[0]}" if b else f"died {dd[0]}" if dd else ""
        about_ = ", ".join(x for x in [life] + rec.get("occupations", [])[:3] if x)
        if r.get("gnd_dates") == "dates differ":
            about_ = "''dates differ'': " + about_
        if about_:
            cell += f"<br /><small>{about_}</small>"
    moved = [g for g in r["gnd"] if g not in gnds]
    if r["gnd_in_reference"] and moved:
        cell += "<br /><small>item now: " + ", ".join(f"[https://d-nb.info/gnd/{g} {g}]" for g in moved) + "</small>"
    return cell


def page(data, lab):
    rows = data["rows"]
    br = data["by_rule"]
    pairs_n = sum(1 for r in rows if r["scope"] == "pair")
    with_batch = data["counts"].get("pair: items with the batch's iD", 0)
    ver = data.get("gnd_dates", {})
    lines = [
        f"This list shows {{{{P|496}}}} values that QuickStatements {BATCH_LINK} (\"add missing ORCID via GND\", "
        f"[[User:{BATCH_USER}|{BATCH_USER}]], July 2026) added to items of people who cannot be the holder of that "
        "ORCID iD. The batch copied ORCID iDs from GND records to the items with the same {{P|227}}. The German "
        "National Library added most of these iDs to the GND by machine matching (Culturegraph, origin code cg001: "
        "names and title data matched against the ORCID data), and most were never checked, so some belong to "
        "another person of the same name: the actress Elke Arendt ({{Q|1331446}}) had the iD of the food scientist "
        "Elke K. Arendt ({{Q|88075585}}). In other cases the GND record is right, but the item had the GND ID of "
        "another person of the same name, and the iD came along with it.",
        "",
        "'''Nothing has been removed yet.''' Comments are welcome on [[User talk:" + BATCH_USER + "]].",
        "",
        "== Checks ==",
        "An iD counts as added by the batch when its statement has the batch's reference (the item's {{P|227}}, "
        f"alone) and the batch edited the item, or an item merged into it since (the batch's edit summaries name "
        f"{BATCH_LINK}). Such an item is listed when at least one of these holds:",
        "# its {{P|570}} lies before October 2012, when ORCID opened its registry;",
        "# its {{P|569}} lies before 1900;",
        "# every {{P|106}} it has is of a kind that does not come with an ORCID iD: actor, athlete, musician, singer, "
        "dancer, model, television presenter, voice actor, or a subclass of these;",
        "# it and the other item with the iD are marked as different ({{P|1889}}): one of the two carries the iD "
        "wrongly, most likely the one the GND matched by machine.",
        "Only {{P|569}}, {{P|570}}, {{P|106}} and {{P|1889}} on Wikidata were compared (dates of the best rank, all "
        "of them wholly before the limit); the ORCID records were not consulted. Pairs only said to be the same "
        "({{P|460}}) are not listed: they look like two items for one person. A date marked ''unreferenced'' or "
        "''only from a Wikipedia'' has no other source on the item. The GND column shows what the GND record says "
        "of its person; ''dates differ'' marks a record whose years of birth or death do not fit the item: either "
        "the item has the GND ID of another person, or one of the two has a wrong date. \"Author of\" counts the "
        "works on Wikidata with the item as {{P|50}}. Items that may hold the iD rightly after all are listed "
        "apart, under [[#Check first|Check first]]: no rule that fired is firm there, because the date has no "
        "source and the GND record does not back it (unless the person was born before 1920), a birth before 1900 "
        "comes without a date of death, the GND record names a research occupation or the item wrote scholarly "
        "articles, or the item marked as different wrote at least as many scholarly articles as the other.",
        "",
        "== Counts ==",
        f"* Items checked: both items of each of the {data['pairs']['total']:,} ORCID iDs that are on exactly two "
        f"items ({data['pairs']['date']}); the batch's iD is on {with_batch:,} of these items. Outside these pairs, "
        f"the {data['outside_pairs']['candidates']:,} items with a GND-cited iD that a query found with an early date "
        "or such an occupation were checked as well.",
        f"* '''{len(rows):,}''' items listed ({pairs_n:,} share the iD with another item):",
    ]
    lines += [f"** {RULES[k]}: {v:,}" for k, v in sorted(br.items())]
    multi = {k: v for k, v in data["rule_combinations"].items() if "+" in k}
    if multi:
        lines.append("** more than one of these: " + ", ".join(
            f"{' and '.join(RULES[x] for x in k.split('+'))}: {v:,}" for k, v in multi.items()))
    clear = [r for r in rows if not r.get("check")]
    lines.append(f"* Of these, '''{len(clear):,}''' are listed under [[#List|List]] and "
                 f"'''{len(rows) - len(clear):,}''' under [[#Check first|Check first]].")
    if ver:
        lines.append(f"* The GND record that carried the iD gives the same year of birth or death as the item in "
                     f"{ver.get('dates agree', 0):,} cases: there the GND links its own person to the iD. It gives "
                     f"other years in {ver.get('dates differ', 0):,} (the item has the GND ID of another person, or a "
                     f"wrong date) and no year to compare in {ver.get('nothing to compare', 0):,}.")
    for title, part in (("List", clear), ("Check first", [r for r in rows if r.get("check")])):
        lines += ["", f"== {title} ==", '{| class="wikitable sortable"',
                  "! ORCID iD !! Item !! Why !! GND !! Other item with the iD"]
        for r in part:
            others = "<br />".join(q_link(o["item"]) + works_note(o) for o in r["other"]) or "–"
            lines += ["|-", f"| [https://orcid.org/{r['orcid']} {r['orcid']}] || {q_link(r['item'])}"
                            f"{works_note(r)} || {why(r, lab)} || {gnd_cell(r)} || {others}"]
        lines.append("|}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "batch":
        bb = batch_edits()
        out(f"{sum(map(len, bb['edits'].values())):,} edits on {len(bb['edits']):,} items, {bb['first']} to "
            f"{bb['last']}; other edits in the window: {bb['other']}")
    elif mode == "fetch":
        fetch([q for _, ab in pairs()[0] for q in ab])
    elif mode == "shapes":
        shapes(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif mode == "scan":
        scan()
    else:
        sys.exit(__doc__)
