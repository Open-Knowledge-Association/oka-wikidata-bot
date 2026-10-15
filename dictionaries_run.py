"""Task 2's full run (Wikidata:Requests for permissions/Bot/OKA bot 2), with the rules of dictionaries.py: every
HDS and SNL entry whose identifier sits on exactly one item, one edit per item, the two works in turn, at most 60
edits a minute with maxlag 5. Nothing is created yet: entries without an item come later.

    python dictionaries_run.py plan hds  # no edits: the entries, their facts, and the checks against Wikidata
    python dictionaries_run.py plan snl  # (HDS once check_links.py has run, SNL once `dictionaries.py prefetch` has)
    python dictionaries_run.py run       # resumable; the log is dictionary_edits.csv, and what the bot leaves
                                         # alone for a person to look at is dictionary_left_alone.csv
"""
import csv
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path

import dictionaries as d
from dictionaries import IDS, WORK2, WORKS, items, out, qlever

APPROVED = True               # Wikidata:Requests for permissions/Bot/OKA bot 2, approved by Ymblanter on 2026-10-14
HERE = Path(__file__).parent
plan_file = lambda work: WORK2 / f"full_plan_{work}.jsonl"
LOG = HERE / "dictionary_edits.csv"
LEFT = HERE / "dictionary_left_alone.csv"
SUMMARY_PAGE = "User:OKA bot/Dictionaries"
PLAN_THREADS = 6             # entries whose facts are worked out at once (lookups are spaced per query service)
SNL_KINDS = {"Q5": "person", "Q755707": "place", "Q4830453": "organisation", "Q43229": "organisation",
             "Q6881511": "organisation"}


# ---------------------------------------------------------------- plan (read only)

def linked(work):
    """{entry ID: item} for the IDs on exactly one item, items with one ID of the work, and the kinds."""
    idp = WORKS[work][1]
    rows = qlever(f"SELECT ?i ?id ?c WHERE {{ ?i wdt:{idp} ?id . OPTIONAL {{ ?i wdt:P31 ?c }} }}")
    by_id, by_item, classes = {}, {}, {}
    for q, ident, c in rows:
        ident = urllib.parse.unquote(ident)            # the query mirror returns some IDs URL-encoded
        by_id.setdefault(ident, set()).add(q)
        by_item.setdefault(q, set()).add(ident)
        classes.setdefault(q, set()).add(c)
    entries, skipped = [], []
    for ident, qs in sorted(by_id.items()):
        q = next(iter(qs))
        if len(qs) > 1:
            skipped.append((work, ident, " ".join(sorted(qs)), "the identifier is on several items"))
        elif len(by_item[q]) > 1:
            skipped.append((work, ident, q, "the item has several identifiers of this work"))
        elif work == "hds":
            entries.append({"work": work, "kind": "person" if "Q5" in classes[q] else "other", "id": ident, "qid": q,
                            "matched_by": "identifier"})
        elif work == "nkl":                           # the artists' lexicon: people only
            if "Q5" in classes[q]:
                entries.append({"work": work, "kind": "person", "id": ident, "qid": q, "matched_by": "identifier"})
        elif work == "sml":                           # the medical lexicon: its terms' other names
            entries.append({"work": work, "kind": "term", "id": ident, "qid": q, "matched_by": "identifier"})
        else:
            kinds = {SNL_KINDS[c] for c in classes[q] if c in SNL_KINDS}
            if len(kinds) == 1:
                entries.append({"work": work, "kind": kinds.pop(), "id": ident, "qid": q, "matched_by": "identifier"})
    return entries, skipped


def held_elsewhere(entries):
    """Drop identifier facts whose value another item already holds (these identifiers are one per item), noting it."""
    wanted = {}
    for e in entries:
        for f in e["facts"]:
            if f["pid"] in IDS:
                wanted.setdefault(f["pid"], set()).add(f["value"]["value"])
    holders = {}
    for pid, values in wanted.items():
        values = sorted(values)
        for i in range(0, len(values), 400):
            lits = " ".join(json.dumps(v) for v in values[i:i + 400])
            for q, v in qlever(f"SELECT ?i ?v WHERE {{ VALUES ?v {{ {lits} }} ?i wdt:{pid} ?v }}"):
                holders.setdefault((pid, v), set()).add(q)
    for e in entries:
        keep = []
        for f in e["facts"]:
            others = holders.get((f["pid"], f["value"]["value"]), set()) - {e["qid"]} if f["pid"] in IDS else set()
            if others:
                e["notes"].append(f"{f['label']} {f['value']['value']} is already on {', '.join(sorted(others))}; not copied")
            else:
                keep.append(f)
        e["facts"] = keep


def warm_up(work, todo, facts):
    """Run the entries without asking anything, noting the places, people and countries they would look up, then
    fetch those 100 names to a query: the real pass finds nearly all of them answered. Rounds repeat for the entries
    that met unknown names, as a town abroad is looked up once its country is known, and an old name once no item has
    it as its label."""
    todo = [e for e in todo if work == "hds" or (WORK2 / work / f"{e['id'].replace('/', '_')}.json").exists()]
    for n in range(1, 6):
        places, people, regions, met = set(), set(), set(), [False]

        def note_place(name, tiers, langs, country, within=None, min_links=5, aliases=False):
            key = (d.place_name(name), tiers, within, country, aliases)
            if key in d._places:
                return d._places[key]                   # answered in an earlier round
            if key[0] and d.PLAIN.match(key[0]):        # (odd names are asked one by one later)
                met[0] = True
                places.add((name, tiers, langs, country, min_links, aliases, within))

        def note_person(name, born, died):
            if (born or died) and len(name.split()) >= 2 and name not in d._humans and d.PLAIN.match(name):
                met[0] = True
                people.add(name)

        def note_region(raw, answer=lambda name: d._regions[name]):
            name = d.region_name(raw)
            if name in d._regions:
                return answer(name)
            if name and d.PLAIN.match(name):
                met[0] = True
                regions.add(name)

        def note_is_region(raw, unless=None):
            return bool(note_region(raw, lambda name: bool(d._region_items.get(name, set()) - {unless})))

        real = d.place, d.person_named, d.viaf_current, d.region_of, d.is_region
        d.place, d.person_named, d.viaf_current, d.region_of, d.is_region = \
            note_place, note_person, lambda vid: None, note_region, note_is_region
        again = []
        try:
            for e in todo:
                met[0] = False
                try:
                    facts(e)
                except Exception:
                    pass                                # a real error shows in the real pass
                if met[0]:
                    again.append(e)
        finally:
            d.place, d.person_named, d.viaf_current, d.region_of, d.is_region = real
        if not (places or people or regions):
            break
        out(f"{work}, round {n}: {len(todo)} entries, looking up {len(regions)} countries, {len(places)} places and "
            f"{len(people)} people, 100 to a query")
        d.prefetch_regions(regions)
        d.prefetch_places(places)
        d.prefetch_humans(people)
        todo = again


def plan():
    work = sys.argv[2]
    entries, skipped = linked(work)
    review = WORK2 / "hds_unlinked_review.csv"            # HDS entries matched by `unlinked`: the run adds the ID
    if work == "hds" and review.exists():
        entries += [{"work": "hds", "kind": "person" if r["list"] == "bio" else "other", "id": r["hds_id"],
                     "qid": r["item"], "matched_by": r["matched_by"]}
                    for r in csv.DictReader(open(review, encoding="utf-8")) if r["item"]]
    out(f"{work}: {len(entries)} entries on one item each; {len(skipped)} left out (identifier on several items, or several on one)")
    partial = WORK2 / f"full_plan_{work}.partial.jsonl"          # facts as they are computed: a restart resumes
    done = {}
    if partial.exists():
        for line in open(partial, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                done[(r["id"], r["qid"])] = r
    todo = []
    for e in entries:
        if (e["id"], e["qid"]) in done:
            e.update({k: done[(e["id"], e["qid"])][k] for k in ("title", "facts", "notes")})
        else:
            todo.append(e)
    d.settlement_classes()                              # what the threads share is read once, before they start
    if work == "hds":
        d.hds_open()
    else:
        for pid in ("P4342", "P5080", "P5081"):
            d.by_id(pid)
        unread = [e for e in todo if not (WORK2 / work / f"{e['id'].replace('/', '_')}.json").exists()]
        out(f"{work}: reading {len(unread)} entries not read yet, one a second")
        for e in unread:                                # read first, so that the warm-up sees every entry
            try:
                d.snl_entry(e["id"], work)
            except (ValueError, AttributeError, subprocess.CalledProcessError):
                pass                                    # noted by snl_facts as not readable
    facts = (lambda e: d.hds_facts(e["id"], e["kind"])) if work == "hds" else \
        (lambda e: d.snl_facts(e["id"], e["kind"], e["work"]))
    warm_up(work, todo, facts)
    out(f"{work}: facts for {len(todo)} entries ({len(entries) - len(todo)} done earlier), {PLAN_THREADS} at a time")
    with open(partial, "a", encoding="utf-8") as f, ThreadPoolExecutor(PLAN_THREADS) as pool:
        for n, (e, got) in enumerate(zip(todo, pool.map(facts, todo)), 1):
            e["title"], e["facts"], e["notes"] = got
            if work != "hds":                           # renamed or moved by SNL: the reference cites it where it is
                try:
                    moved = d.snl_entry(e["id"], work).get("canonical")
                except (ValueError, AttributeError, subprocess.CalledProcessError):
                    moved = None
                if moved:
                    e["cite"] = moved
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.flush()
            if n % 1000 == 0:
                out(f"facts for {n} of {len(todo)} entries; queries: {dict(d.asked)}")
    held_elsewhere(entries)
    entries = [e for e in entries if e["facts"] or e["notes"]]
    tmp = plan_file(work).with_suffix(".tmp")         # a running `run` reads only whole plans
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, plan_file(work))
    with open(WORK2 / f"full_plan_{work}_skipped.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([("work", "entry", "items", "reason"), *skipped])
    out(f"{work}: {len(entries)} entries with something to add or check, {sum(len(e['facts']) for e in entries)} facts")


# ---------------------------------------------------------------- HDS entries no item carries yet (read only)

ARTICLES = {"Le", "La", "Les", "L'", "Der", "Die", "Das", "Il", "Lo", "I", "Gli", "El", "Las", "Los"}
SWISS_WORDS = re.compile(r"swiss|schweiz|suisse|svizzer|switzerland|helvet|eidgen", re.I)


def hds_titles(kind, hid):
    """The entry's title in each HDS language: 'Le Démocrate', 'Martin Jost', 'Burgdorf (Gemeinde)'."""
    out = {}
    for lang in "dfir":
        r = _lists[(kind, lang)].get(hid)
        if not r:
            continue
        lemma, comp = r["Lemma"].strip(), r["Complement"].strip()
        if kind == "bio" or comp in ARTICLES or comp.endswith("'"):
            t = f"{comp} {lemma}".replace("' ", "'").strip() if comp else lemma
        else:
            t = f"{lemma} ({comp})" if comp else lemma
        out["rm" if lang == "r" else {"d": "de", "f": "fr", "i": "it"}[lang]] = t
    return out


def years(precision):
    """'//1450// - 1501' -> ((1450, True), (1501, False)): each year, and whether the HDS marks it uncertain."""
    sides = (precision or "").split(" - ")
    got = []
    for s in (sides + ["", ""])[:2]:
        m = re.search(r"\d{3,4}", s)
        got.append((int(m.group(0)), "//" in s) if m else (None, False))
    return tuple(got)


_lists = {}
HDS_URL = re.compile(r"hls-dhs-dss\.ch/(?:(?:de|fr|it|rm)/articles/(\d{6})|textes/[dfir]/[DFIR](\d{3,6})\.php)")


def api_get(url):
    """A read from a Wikimedia API; its rate-limit pages (HTTP 429, not JSON) are waited out."""
    for attempt in range(1, 9):
        time.sleep(0.5)
        try:
            return d.curl_json(url)
        except ValueError:
            time.sleep(30 * attempt)
    raise RuntimeError(f"no JSON from {url[:120]}")


def wiki_links():
    """{HDS ID: {item}}: the Wikipedia articles (de, fr, it, en, rm) that link an HDS article, as their items."""
    path = WORK2 / "wiki_hds_links.json"
    if path.exists():
        return {k: set(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    by_title = {}
    for wiki in ("de", "fr", "it", "en", "rm"):
        part = WORK2 / f"wiki_hds_links_{wiki}.json"                # per-wiki progress, so a restart skips it
        if part.exists():
            by_title.update({(wiki, t): set(v) for t, v in json.loads(part.read_text(encoding="utf-8")).items()})
            continue
        api = f"https://{wiki}.wikipedia.org/w/api.php"
        for prefix in ("hls-dhs-dss.ch/de/articles/", "hls-dhs-dss.ch/fr/articles/", "hls-dhs-dss.ch/it/articles/",
                       "hls-dhs-dss.ch/rm/articles/", "hls-dhs-dss.ch/textes/"):
            for proto in ("https", "http"):
                cont = {}
                while True:
                    params = {"action": "query", "list": "exturlusage", "euquery": prefix, "euprotocol": proto,
                              "eunamespace": 0, "eulimit": 500, "format": "json", **cont}
                    r = api_get(api + "?" + urllib.parse.urlencode(params))
                    for u in r.get("query", {}).get("exturlusage", []):
                        m = HDS_URL.search(u["url"])
                        if m:
                            by_title.setdefault((wiki, u["title"]), set()).add((m.group(1) or m.group(2)).zfill(6))
                    if "continue" not in r:
                        break
                    cont = r["continue"]
        part.write_text(json.dumps({t: sorted(v) for (w, t), v in by_title.items() if w == wiki}), encoding="utf-8")
        out(f"{wiki}wiki: {sum(1 for w, _ in by_title if w == wiki)} articles link the HDS")
    links = {}
    for wiki in ("de", "fr", "it", "en", "rm"):
        titles = sorted(t for w, t in by_title if w == wiki)
        for i in range(0, len(titles), 50):
            r = api_get(f"https://{wiki}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "titles": "|".join(titles[i:i + 50]),
                 "redirects": 1, "format": "json"}))
            norm = {n["to"]: n["from"] for n in r.get("query", {}).get("normalized", [])}
            for p in r.get("query", {}).get("pages", {}).values():
                q = p.get("pageprops", {}).get("wikibase_item")
                if q:
                    for hid in by_title.get((wiki, norm.get(p["title"], p["title"])), ()):
                        links.setdefault(hid, set()).add(q)
    path.write_text(json.dumps({k: sorted(v) for k, v in links.items()}), encoding="utf-8")
    return links


def unlinked():
    """Match the HDS entries that no item carries to existing items; strong matches become entries of the run
    (they add the HDS ID and the facts), the rest is listed for review in work/task2/hds_unlinked_review.csv."""
    d0 = WORK2 / "opendata"
    for kind in ("bio", "fam", "geo", "tem"):
        for lang in "dfir":
            p = d0 / f"liste_{kind}_{lang}_utf8.csv"
            _lists[(kind, lang)] = {r["ID"].zfill(6): r for r in csv.DictReader(open(p, encoding="utf-8"))} if p.exists() else {}
    linked_ids = {urllib.parse.unquote(i).zfill(6) for _, i in qlever("SELECT ?i ?id WHERE { ?i wdt:P902 ?id }")}
    todo = [(kind, hid) for kind in ("bio", "fam", "geo", "tem") for hid in _lists[(kind, "d")] if hid not in linked_ids]
    titles = {hid: hds_titles(kind, hid) for kind, hid in todo}
    lits = sorted({(t, lang) for ts in titles.values() for lang0, t in ts.items()
                   for lang in {lang0, "de", "fr", "it", "en", "mul"}})
    found, info = {}, {}                          # title -> items with it as label or alias; item -> what it holds
    for i in range(0, len(lits), 200):
        values = " ".join(f"{json.dumps(t)}@{lang}" for t, lang in lits[i:i + 200])
        for t, q in [r for pred in ("rdfs:label", "skos:altLabel")          # one predicate per query: fast
                     for r in qlever("SELECT DISTINCT ?l ?i WHERE { VALUES ?l { " + values + " } ?i " + pred + " ?l }")]:
            found.setdefault(t, {})[q] = info.setdefault(q, {"hds": set(), "classes": set(), "born": set(), "died": set(),
                                                              "swiss": False})
    qs = sorted(info)
    for i in range(0, len(qs), 300):
        vals = " ".join("wd:" + q for q in qs[i:i + 300])
        for pid, key in (("P902", "hds"), ("P31", "classes"), ("P569", "born"), ("P570", "died")):
            for q, v in qlever(f"SELECT ?i ?v WHERE {{ VALUES ?i {{ {vals} }} ?i wdt:{pid} ?v }}"):
                m = re.match(r"(-?\d{1,4})-", v) if key in ("born", "died") else None
                if key in ("born", "died"):
                    if m:
                        info[q][key].add(int(m.group(1)))
                else:
                    info[q][key].add(v)
        for q in [r[0] for r in qlever(f"SELECT DISTINCT ?i WHERE {{ VALUES ?i {{ {vals} }} {{ ?i wdt:P17 wd:Q39 }} UNION "
                                       f"{{ ?i wdt:P27 wd:Q39 }} }}")]:
            info[q]["swiss"] = True
        for q, desc in qlever(f"SELECT ?i ?d WHERE {{ VALUES ?i {{ {vals} }} ?i schema:description ?d "
                              "FILTER(LANG(?d) IN ('en','de','fr','it')) }"):
            info[q]["swiss"] |= bool(SWISS_WORDS.search(desc))
    links = wiki_links()
    path = WORK2 / "hds_topic_matches_checked.json"          # {HDS ID: item, or "" when rejected}, filled by hand
    approved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    not_topics = {"Q5", "Q101352", "Q202444", "Q12308941", "Q11879590", "Q3409032", "Q4167410", "Q13406463",
                  "Q4167836", "Q11266439"}       # people, names, disambiguation/list/category/template pages
    review, strong = [], []
    for kind, hid in todo:
        ts = titles[hid]
        cands = {}
        for lang, t in ts.items():
            for q, f in found.get(t, {}).items():
                cands.setdefault(q, dict(f, titles=set()))["titles"].add(t)
        free = {q: c for q, c in cands.items() if not c["hds"]}         # an item with an HDS ID is another entry
        linked_items = links.get(hid, set())
        why, pick = "", None
        if kind == "bio":
            (b, bu), (dd, du) = years(_lists[("bio", "d")][hid]["Precision"])
            def fits(c):          # a certain year that matches and none that differs; '//' years are only mentions
                checks = [(y, have) for y, unsure, have in ((b, bu, c["born"]), (dd, du, c["died"])) if y and not unsure and have]
                return bool(checks) and all(y in have for y, have in checks)
            people = {q: c for q, c in free.items() if "Q5" in c["classes"]}
            good = [q for q, c in people.items() if fits(c)]
            wiki = [q for q in people if q in linked_items]
            if len(good) == 1:
                pick, why = good[0], "name and life years"
            elif len(wiki) == 1:
                pick, why = wiki[0], "name, and its Wikipedia article links this HDS entry"
            else:
                why = f"{len(good)} people with this name and life years" if good else \
                      f"{len(people)} people with this name, none with these life years"
        else:                   # topics are only proposed: homonyms ('Schaffner', the steward vs the train conductor)
            topical = {q: c for q, c in free.items() if not c["classes"] & not_topics}
            wiki = [q for q in topical if q in linked_items]
            if approved.get(hid):                          # checked by hand: the right item, proposed or not
                pick, why = approved[hid], "checked by hand (Wikipedia articles linking this HDS entry, titles)"
            elif len(wiki) == 1:
                why = f"proposed {wiki[0]}: title, and its Wikipedia article links this HDS entry" + \
                      (" (rejected by hand)" if hid in approved else "")
            else:
                why = f"{len(topical)} items with this title"
        row = {"hds_id": hid, "list": kind, "title_de": ts.get("de", ""), "title_fr": ts.get("fr", ""),
               "title_it": ts.get("it", ""), "years": _lists[(kind, "d")][hid]["Precision"],
               "item": pick or "", "matched_by": why,
               "candidates": " ".join(sorted(free))[:300], "taken": " ".join(sorted(q for q, c in cands.items() if c["hds"]))[:200],
               "wikipedia_links": " ".join(sorted(linked_items))[:200]}
        (strong if pick else review).append(row)
    with open(WORK2 / "hds_unlinked_review.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, list((strong + review)[0]))
        w.writeheader()
        w.writerows(strong + review)
    out(f"{len(todo)} HDS entries without an item: {len(strong)} matched, {len(review)} for review")
    for kind in ("bio", "fam", "geo", "tem"):
        out(f"  {kind}: {sum(r['list'] == kind for r in strong)} matched, {sum(r['list'] == kind for r in review)} for review")
    return strong


# ---------------------------------------------------------------- reading the HDS pages (no edits)

CRAWL_DELAY = 21              # seconds between HDS pages: its robots.txt asks generic bots for 20
CHALLENGE = re.compile(r"Just a moment|cf-chl|challenge-platform|cf_chl_opt|Attention Required", re.I)


def crawl_hds():
    """Read the HDS article pages, as the HDS robots.txt allows generic bots: one page every 21 s, under the bot's
    own name. Keeps only the article (the <main> part) and its date fields, in work/task2/hds/<id>.html, which
    hds_facts() reads. Biographies linked from Wikidata first, then the other entries. Resumable. At the first
    challenge or block page it stops: such a page is never solved or worked around."""
    import time
    import urllib.error
    import urllib.request
    from common import UA
    folder = WORK2 / "hds"
    folder.mkdir(parents=True, exist_ok=True)
    lists = {kind: [r["ID"].zfill(6) for r in csv.DictReader(open(WORK2 / "opendata" / f"liste_{kind}_d_utf8.csv",
                                                                     encoding="utf-8"))]
             for kind in ("bio", "tem", "fam", "geo")}
    on_wd = {urllib.parse.unquote(i).zfill(6) for _, i in qlever("SELECT ?i ?id WHERE { ?i wdt:P902 ?id }")}
    order = [h for h in lists["bio"] if h in on_wd] + [h for h in lists["bio"] if h not in on_wd]
    order += [h for kind in ("tem", "fam", "geo") for h in lists[kind]]
    todo = [h for h in dict.fromkeys(order) if not (folder / f"{h}.html").exists()]
    out(f"HDS pages to read: {len(todo)} (one every {CRAWL_DELAY} s, about {len(todo) * CRAWL_DELAY / 86400:.1f} days)")
    last, odd = 0.0, 0
    for n, hid in enumerate(todo, 1):
        time.sleep(max(0.0, CRAWL_DELAY - (time.time() - last)))
        last = time.time()
        url = f"https://hls-dhs-dss.ch/de/articles/{hid}/"
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    status, body = r.status, r.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                status, body = e.code, e.read().decode("utf-8", "replace")
                break
            except (urllib.error.URLError, OSError) as e:
                status, body = 0, str(e)
                time.sleep(60 * attempt)
        if status in (403, 429, 503) or CHALLENGE.search(body[:20000]):
            out(f"stopped at {hid} after {n - 1} pages: the HDS answered with a challenge or block (HTTP {status})")
            sys.exit(2)
        if status == 404:
            continue
        main = re.search(r"<main\b.*?</main>", body, re.S)
        if status != 200 or not main:
            odd += 1
            out(f"{hid}: unexpected answer (HTTP {status}); skipped")
            if odd >= 20:
                sys.exit("20 unexpected answers: stopping to look at it")
            continue
        props = "".join(re.findall(r"<(\w+)[^>]*\bitemprop=\"[^\"]+\"[^>]*>[^<]*</\1>", body))
        (folder / f"{hid}.html").write_text(main.group(0) + "\n" + props, encoding="utf-8")
        if n % 500 == 0:
            out(f"{n} of {len(todo)} pages read")
    out("all HDS pages read")


# ---------------------------------------------------------------- run (after approval)

def logged():
    if not LOG.exists():
        return set()
    return {(r["work"], r["entry"]) for r in csv.DictReader(open(LOG, encoding="utf-8"))}


def append(path, header, rows):
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def publish():
    """Commit and push the two logs; a failed push never stops the bot."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    git = lambda *args: subprocess.run(["git", "-C", str(HERE), *args], capture_output=True, text=True, env=env)
    try:
        git("add", LOG.name, LEFT.name)
        if git("diff", "--cached", "--quiet").returncode:
            git("commit", "-q", "-m", f"Task 2 log: {sum(1 for _ in open(LOG, encoding='utf-8')) - 1} entries done")
        git("pull", "-q", "--rebase")
        git("push", "-q")
    except OSError:
        pass


def summary_text():
    rows = list(csv.DictReader(open(LOG, encoding="utf-8")))
    left = list(csv.DictReader(open(LEFT, encoding="utf-8"))) if LEFT.exists() else []
    repo = "https://github.com/Open-Knowledge-Association/oka-wikidata-bot/blob/main/"
    lines = [f"Progress of the full run of [[Wikidata:Requests for permissions/Bot/OKA bot 2]], written by the bot "
             f"({datetime.datetime.now(datetime.timezone.utc):%d %B %Y, %H:%M} UTC). Each edit adds or sources facts "
             "from one entry of the [[Q642074|Historical Dictionary of Switzerland]] (HDS) or "
             "[[Q746368|Store norske leksikon]] (SNL); nothing is removed or changed.", "",
             '{| class="wikitable"', "! Work !! Entries checked !! Edits !! Cases left alone"]
    for work in WORKS:
        mine = [r for r in rows if r["work"] == work]
        lines += ["|-", f"| {work.upper()} || {len(mine)} || {sum(1 for r in mine if r['revid'])} || "
                        f"{sum(1 for r in left if r['work'] == work)}"]
    lines += ["|}", "", f"Every edit is in [{repo}{LOG.name} {LOG.name}]. Where an entry disagrees with the item, or a "
              f"value cannot be matched, the bot leaves the item alone; those cases are in [{repo}{LEFT.name} "
              f"{LEFT.name}] for a person to look at."]
    return "\n".join(lines)


def run():
    if not APPROVED:
        sys.exit("Task 2 is not approved yet; the full run starts only after a bureaucrat approves the request.")
    from common import bot_site
    site = bot_site(1)                        # at most one edit a second (60 a minute); writes the login settings first
    import pywikibot
    d.REQUEST2 = "[[Wikidata:Requests for permissions/Bot/OKA bot 2|OKA bot task 2]]"
    while True:                               # a work whose plan is finished meanwhile is taken up after the others
        done = logged()
        unread = lambda e: not e["facts"] and "entry not readable through the API" in e["notes"]   # kept for later
        todo = [e for w in WORKS if plan_file(w).exists() for e in map(json.loads, open(plan_file(w), encoding="utf-8"))
                if (e["work"], e["id"]) not in done and not unread(e)]
        if not todo:
            break
        by_work = [[e for e in todo if e["work"] == w] for w in WORKS]
        todo = [e for pair in zip_longest(*by_work) for e in pair if e]      # the works in turn
        out(f"{len(todo)} entries to do")
        for b in range(0, len(todo), 50):
            batch = todo[b:b + 50]
            today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
            live = items(sorted({e["qid"] for e in batch}))
            labels = d.place_labels(live, batch)
            log, left = [], []
            for e in batch:
                ent = live.get(e["qid"], {})
                claims, changes, notes = d.build(ent, e, today, labels) if ent else ([], [], ["item not found"])
                revid, err = d.save(site, e["qid"], claims, changes, e["work"], ent.get("lastrevid")) if claims else (None, None)
                now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                log.append((now, e["work"], e["kind"], e["id"], e["qid"], revid or "",
                            err or ("; ".join(changes) if revid else "no change")))
                left += [(e["work"], e["id"], e["qid"], e.get("title", ""), n) for n in notes]
            append(LOG, ("time", "work", "kind", "entry", "item", "revid", "changes"), log)
            append(LEFT, ("work", "entry", "item", "title", "note"), left)
            out(f"{b + len(batch)} of {len(todo)}: {sum(1 for r in log if r[5])} saved in this batch")
            if (b // 50) % 10 == 9 or b + 50 >= len(todo):
                publish()
                page = pywikibot.Page(site, SUMMARY_PAGE)
                page.text = summary_text()
                page.save("Progress of task 2", minor=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d.check_occupations()
    {"plan": plan, "run": run, "unlinked": unlinked, "crawl-hds": crawl_hds}[sys.argv[1]]()
