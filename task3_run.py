"""Task 3's full run (Wikidata:Requests for permissions/Bot/OKA bot 3, approved by Ymblanter on 2026-11-03), with the
readers of the test run (readers3/) and task 2's edits (dictionaries.build and save): every entry whose identifier
sits on exactly one item, one edit per item and work, the works in turn, at most 60 edits a minute with maxlag 5.

    python task3_run.py crawl [work ...]  # no edits: every linked entry read once, each site at its reader's pace,
                                          # all works at once (one thread each); no queries while reading
    python task3_run.py plan [work ...]   # no edits: the facts of each crawled work and the checks against Wikidata,
                                          # one work at a time, as their crawls end
    python task3_run.py run               # resumable; only the works listed in work/task3/ready.txt (each plan is
                                          # looked at first); the log is task3_edits.csv, and what the bot leaves
                                          # alone for a person to look at is task3_left_alone.csv

Disagreements are only listed here. The approved rules for them (web search, preferred or equal rank) run later in
separate batches, each checked by the operator on a sample first.
"""
import csv
import datetime
import importlib
import json
import os
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path

import dictionaries as d
import dictionaries_run as dr
import sources3 as s

APPROVED = True               # Wikidata:Requests for permissions/Bot/OKA bot 3, approved by Ymblanter on 2026-11-03
HERE = Path(__file__).parent
W3 = s.WORK3
LOG = HERE / "task3_edits.csv"
LEFT = HERE / "task3_left_alone.csv"
READY = W3 / "ready.txt"
PLAN_THREADS = 6
STOP_AFTER = 50               # entries in a row that could not be read: the site is down or refuses the bot, so stop
plan_file = lambda work: W3 / f"full_plan_{work}.jsonl"
crawled = lambda work: W3 / f"crawl_done_{work}"
reader = lambda work: importlib.import_module(f"readers3.{work}")


def works_named(args):
    named = [a for a in args if not a.startswith("--")]
    unknown = set(named) - set(s.WORKS3)
    if unknown:
        sys.exit(f"unknown works: {', '.join(sorted(unknown))}")
    return named or list(s.WORKS3)


# ---------------------------------------------------------------- which entries

def ndb_linked():
    """NDB and ADB articles (not the 1.3 million index entries) whose GND number exactly one human item carries as
    P7902, that item having no other P7902: the test run's selection (readers3.ndb.candidates), for all articles."""
    gnds = sorted({g for g, _ in reader("ndb").articles()})
    on = {}                                        # GND -> {item: number of P7902 values on it}
    for i in range(0, len(gnds), 2000):
        values = " ".join(json.dumps(g) for g in gnds[i:i + 2000])
        for r in d.qlever(f"SELECT ?id ?i (COUNT(DISTINCT ?other) AS ?n) WHERE {{ VALUES ?id {{ {values} }} "
                          "?i wdt:P7902 ?id ; wdt:P31 wd:Q5 ; wdt:P7902 ?other } GROUP BY ?id ?i"):
            on.setdefault(urllib.parse.unquote(r[0]), {})[r[1]] = int(r[2].split("^")[0].strip('"'))
    pairs, skipped = [], []
    for g, its in sorted(on.items()):
        q, n = next(iter(its.items()))
        if len(its) > 1:
            skipped.append(("ndb", g, " ".join(sorted(its)), "the identifier is on several items"))
        elif n > 1:
            skipped.append(("ndb", g, q, "the item has several identifiers of this work"))
        else:
            pairs.append((g, q))
    return pairs, skipped


def linked(work):
    """[(entry ID, item)] for the whole work, cached in work/task3/linked_<work>.json (the crawl and the plan read
    the same list)."""
    path = W3 / f"linked_{work}.json"
    if not path.exists():
        if work == "ndb":
            pairs, skipped = ndb_linked()
        else:
            pairs, skipped = reader(work).candidates(10 ** 9), []
        path.write_text(json.dumps({"pairs": sorted(pairs), "skipped": skipped}, ensure_ascii=False), encoding="utf-8")
    got = json.loads(path.read_text(encoding="utf-8"))
    return [tuple(p) for p in got["pairs"]], got["skipped"]


# ---------------------------------------------------------------- crawl (read only, no queries)

def crawl():
    """Every work at once, one thread each; each site keeps its reader's gap between requests. Queries are switched
    off: read() runs only so that it fetches and caches the pages, and its facts are thrown away (the plan works them
    out again with the lookups batched). A work whose pages fail STOP_AFTER times in a row stops: never route around
    a block."""
    works = works_named(sys.argv[2:])
    lists = {w: linked(w)[0] for w in works if not crawled(w).exists()}
    for w, pairs in lists.items():
        d.out(f"{w}: {len(pairs)} entries to read")
    d.lookup = lambda query: []
    d.qlever = lambda query: []
    d.items = lambda ids: {}
    d.viaf_current = lambda vid: None

    def one(work):
        r, failed, n = reader(work), 0, 0
        for n, (ident, _) in enumerate(lists[work], 1):
            try:
                r.read(ident)
                failed = 0
            except Exception as e:                       # a stubbed lookup can fail an entry too: only a run of
                failed += 1                              # failures stops the work
                if failed == 1:
                    d.out(f"{work} {ident}: {e!r:.160}")
                if failed >= STOP_AFTER:
                    d.out(f"{work}: {STOP_AFTER} entries in a row not read, stopped at {n} of {len(lists[work])}")
                    return
            if n % 500 == 0:
                d.out(f"{work}: {n} of {len(lists[work])} read")
        crawled(work).write_text(datetime.datetime.now(datetime.timezone.utc).isoformat(), encoding="utf-8")
        d.out(f"{work}: all {n} entries read")

    threads = [threading.Thread(target=one, args=(w,), name=w) for w in lists]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ---------------------------------------------------------------- plan (read only)

def plan_work(work):
    pairs, skipped = linked(work)
    entries = [{"work": work, "id": ident, "qid": q, "matched_by": "identifier"} for ident, q in pairs]
    partial = W3 / f"full_plan_{work}.partial.jsonl"
    done = {}
    if partial.exists():
        for line in open(partial, encoding="utf-8"):
            if line.strip():
                row = json.loads(line)
                done[(row["id"], row["qid"])] = row
    todo = []
    for e in entries:
        if (e["id"], e["qid"]) in done:
            e.update({k: v for k, v in done[(e["id"], e["qid"])].items() if k in ("title", "kind", "facts", "notes", "cite")})
        else:
            todo.append(e)
    d.settlement_classes()                               # what the threads share is read once, before they start
    r = reader(work)

    def safe(e):
        try:
            got = r.read(e["id"])
        except Exception as x:                           # not readable: listed, nothing added
            got = {"notes": [f"entry not readable ({x!r:.120})"]}
        return {"title": got.get("title", e["id"]), "kind": got.get("kind", "person"),
                "facts": got.get("facts", []), "notes": got.get("notes", []), "canonical": got.get("canonical")}

    dr.warm_up(work, todo, lambda e: r.read(e["id"]), keep=lambda e: True)
    d.out(f"{work}: facts for {len(todo)} entries ({len(entries) - len(todo)} done earlier), {PLAN_THREADS} at a time")
    with open(partial, "a", encoding="utf-8") as f, ThreadPoolExecutor(PLAN_THREADS) as pool:
        for n, (e, got) in enumerate(zip(todo, pool.map(safe, todo)), 1):
            moved = got.pop("canonical")
            e.update(got)
            if moved:                                    # renamed by the work: cited where it is now, and its current
                e["cite"] = moved                        # ID added (the old one deprecated as a redirect)
                e["facts"].append(d.fact(s.WORKS3[moved["work"]][1], d.string_value(moved["slug"]),
                                         f"{moved['work'].upper()} ID"))
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.flush()
            if n % 1000 == 0:
                d.out(f"{work}: facts for {n} of {len(todo)} entries; queries: {dict(d.asked)}")
    dr.held_elsewhere(entries)
    entries = [e for e in entries if e["facts"] or e["notes"]]
    tmp = plan_file(work).with_suffix(".tmp")            # a running `run` reads only whole plans
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, plan_file(work))
    with open(W3 / f"full_plan_{work}_skipped.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([("work", "entry", "items", "reason"), *skipped])
    d.out(f"{work}: {len(entries)} entries with something to add or check, {sum(len(e['facts']) for e in entries)} facts")


def plan():
    """The named works (default: all), each once its crawl has ended, one at a time (they share QLever's one query a
    second); waits for crawls still running."""
    left = [w for w in works_named(sys.argv[2:]) if not plan_file(w).exists() or "--again" in sys.argv]
    while left:
        ready = [w for w in left if crawled(w).exists()]
        if not ready:
            time.sleep(600)
            continue
        plan_work(ready[0])
        left.remove(ready[0])


# ---------------------------------------------------------------- run

def logged():
    if not LOG.exists():
        return set()
    return {(r["work"], r["entry"]) for r in csv.DictReader(open(LOG, encoding="utf-8"))}


def ready_works():
    if not READY.exists():
        return []
    return [w for w in READY.read_text(encoding="utf-8").split() if w in s.WORKS3]


def publish():
    """Commit and push the logs; a failed push never stops the bot."""
    import subprocess
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    git = lambda *args: subprocess.run(["git", "-C", str(HERE), *args], capture_output=True, text=True, env=env)
    try:
        git("add", *[p.name for p in (LOG, LEFT) if p.exists()])
        if git("diff", "--cached", "--quiet").returncode:
            git("commit", "-q", "-m", f"Task 3 log: {sum(1 for _ in open(LOG, encoding='utf-8')) - 1} entries done")
        git("pull", "-q", "--rebase")
        git("push", "-q")
    except OSError:
        pass


def run():
    if not APPROVED:
        sys.exit("Task 3 is not approved yet.")
    from common import bot_site
    site = bot_site(1)                        # at most one edit a second (60 a minute); writes the login settings first
    d.REQUEST2 = "[[Wikidata:Requests for permissions/Bot/OKA bot 3|OKA bot task 3]]"
    while True:
        done = logged()
        works = ready_works()
        todo = [e for w in works if plan_file(w).exists() for e in map(json.loads, open(plan_file(w), encoding="utf-8"))
                if (e["work"], e["id"]) not in done and e["facts"]]
        if not todo:
            if all(w in works for w in s.WORKS3):
                break
            time.sleep(900)                   # works still crawling, planning or waiting to be looked at
            continue
        by_work = [[e for e in todo if e["work"] == w] for w in works]
        todo = [e for group in zip_longest(*by_work) for e in group if e]    # the works in turn
        d.out(f"{len(todo)} entries to do ({', '.join(works)})")
        for b in range(0, len(todo), 50):
            batch = todo[b:b + 50]
            today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
            live = d.items(sorted({e["qid"] for e in batch}))
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
            dr.append(LOG, ("time", "work", "kind", "entry", "item", "revid", "changes"), log)
            dr.append(LEFT, ("work", "entry", "item", "title", "note"), left)
            d.out(f"{b + len(batch)} of {len(todo)}: {sum(1 for r in log if r[5])} saved in this batch")
            if (b // 50) % 10 == 9 or b + 50 >= len(todo):
                publish()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    d.check_occupations()
    {"crawl": crawl, "plan": plan, "run": run}[sys.argv[1]]()
