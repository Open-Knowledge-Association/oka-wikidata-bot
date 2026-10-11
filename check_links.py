"""Check the authority links in the HDS open data (BEACON-VIAF-HLS.txt, BEACON-GND-HLS.txt) against VIAF and
lobid-gnd, so outdated links can be reported to the HDS with the fix, and the bot never copies one to Wikidata:
- VIAF clusters that VIAF abandoned (merged into another cluster, which is named) or that no longer exist;
- GND records that were merged into another record or deleted, and links to undifferentiated name records;
- one VIAF or GND ID given to several HDS biographies (two people cannot share an authority record).

    python check_links.py run       # resumable; one result per ID in work/task2/link_check.jsonl
    python check_links.py report    # hds_link_issues.csv: every outdated link, with the current ID
"""
import collections
import csv
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import UA

HERE = Path(__file__).parent
WORK2 = HERE / "work" / "task2"
OUT = WORK2 / "link_check.jsonl"
FILES = {"viaf": ("BEACON-VIAF-HLS.txt", 1.0), "gnd": ("BEACON-GND-HLS.txt", 0.5)}   # file, seconds per request
_lock = threading.Lock()


def entries(kind):
    """(ID, label, HDS article number) for each line of one link file."""
    for line in open(WORK2 / "opendata" / FILES[kind][0], encoding="utf-8"):
        parts = line.rstrip("\n").split("|")
        if not line.startswith("#") and len(parts) >= 3:
            yield parts[0].strip(), parts[1].strip(), parts[2].rstrip("/").rsplit("/", 1)[-1]


def get(url):
    """(HTTP status, parsed JSON or None). Network errors, 429 and 5xx are retried for about an hour; an answer
    that is not JSON, three times."""
    for attempt in range(10):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                status, body = r.status, r.read()
            try:
                return status, json.loads(body)
            except ValueError:
                if attempt >= 2:
                    return -1, None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (404, 410) or "404 Not Found" in body:      # lobid wraps a missing record in a 500
                return 404, None
            if e.code not in (429, 500, 502, 503, 504):
                return e.code, None
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(min(30 * 2 ** attempt, 900))
    return 0, None


def check_viaf(vid):
    code, d = get(f"https://viaf.org/viaf/{vid}")
    if d is None:
        return {"status": "missing" if code == 404 else f"error {code}"}
    if "ns0:abandoned_viaf_record" in d:
        target = (d["ns0:abandoned_viaf_record"].get("ns0:redirect") or {}).get("ns0:directto")
        return {"status": "replaced", "now": str(target)} if target else {"status": "abandoned"}
    now = str((d.get("ns1:VIAFCluster") or {}).get("ns1:viafID", ""))
    return {"status": "ok"} if now == vid else {"status": "replaced", "now": now} if now else {"status": "unreadable"}


def check_gnd(gid):
    code, d = get(f"https://lobid.org/gnd/{gid}.json")         # a merged record answers as the record it went into
    if d is None:
        return {"status": "missing" if code == 404 else f"error {code}"}
    now = d.get("gndIdentifier", "")
    out = {"status": "ok"} if now == gid else {"status": "replaced", "now": now}
    if "UndifferentiatedPerson" in d.get("type", []):
        out["undifferentiated"] = True
    return out


def done():
    if not OUT.exists():
        return {}
    rows = (json.loads(l) for l in open(OUT, encoding="utf-8") if l.strip())
    return {(r["kind"], r["id"]): r for r in rows if not r["status"].startswith("error")}


def worker(kind, ids):
    check, pause = {"viaf": check_viaf, "gnd": check_gnd}[kind], FILES[kind][1]
    for n, i in enumerate(ids, 1):
        t = time.time()
        row = {"kind": kind, "id": i, **check(i)}
        with _lock, open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if n % 500 == 0:
            print(f"{kind}: {n} of {len(ids)} checked", flush=True)
        time.sleep(max(0.0, pause - (time.time() - t)))
    print(f"{kind}: done, {len(ids)} checked", flush=True)


def run():
    have = done()
    threads = []
    for kind in FILES:
        ids = list(dict.fromkeys(i for i, _, _ in entries(kind) if (kind, i) not in have))
        print(f"{kind}: {len(ids)} to check", flush=True)
        threads.append(threading.Thread(target=worker, args=(kind, ids)))
        threads[-1].start()
    for t in threads:
        t.join()


def report(path=HERE / "hds_link_issues.csv"):
    have, bio = done(), {}
    for r in csv.DictReader(open(WORK2 / "opendata" / "liste_bio_d_utf8.csv", encoding="utf-8")):
        bio[r["ID"].zfill(6)] = True
    rows, stats = [], collections.Counter()
    for kind in FILES:
        lines = list(entries(kind))
        people = collections.defaultdict(set)
        for i, _, hds in lines:
            if hds in bio:
                people[i].add(hds)
        for i, label, hds in lines:
            r = have.get((kind, i), {"status": "unchecked"})
            problems = []
            if r["status"] == "replaced":
                problems.append(("merged into another record" if kind == "gnd" else "abandoned by VIAF, merged into another cluster", r["now"]))
            elif r["status"] in ("missing", "abandoned"):
                problems.append(("no longer exists" if r["status"] == "missing" else "abandoned by VIAF, no successor", ""))
            if r.get("undifferentiated"):
                problems.append(("undifferentiated name record (Tn), not an individual person", ""))
            if len(people[i]) > 1:
                problems.append(("same ID on several HDS biographies: " + ", ".join(sorted(people[i] - {hds})), ""))
            for problem, now in problems:
                stats[(kind, problem.split(":")[0])] += 1
                rows.append({"hds_id": hds, "hds_article": f"https://hls-dhs-dss.ch/de/articles/{hds}/", "name": label,
                             "link": kind.upper(), "id_in_hds": i, "problem": problem, "current_id": now})
            stats[(kind, "checked" if r["status"] != "unchecked" else "unchecked")] += 1
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["hds_id", "hds_article", "name", "link", "id_in_hds", "problem", "current_id"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["link"], r["problem"], r["hds_id"])))
    for k, v in sorted(stats.items()):
        print(f"{k[0]:5} {k[1]:70} {v}")
    print(f"{len(rows)} issues written to {path.name}")


if __name__ == "__main__":
    {"run": run, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "run"]()
