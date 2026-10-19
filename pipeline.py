"""OKA bot on Wikidata: swisstopo task (Wikidata:Requests for permissions/Bot/OKA bot).

  python pipeline.py plan                      download the latest data and build work/plan.jsonl (local)
  python pipeline.py preview [--n 10]          show what the next edits would change (read-only)
  python pipeline.py run [--kind edit|create] [--batches K] [--batch-size N] [--rate R]
                                               edit as OKA bot; checks each batch after saving and stops on problems
  python pipeline.py run --qids Q1,Q2          re-check items already done (after a rule change); only what is missing
  python pipeline.py verify [--last N]         read back logged edits and check them
  python pipeline.py userpage                  refresh User:OKA bot (also done after every run)
  python pipeline.py status                    counts from edits.csv

plan, preview, run, verify and status take --plan natural to work on the second batch (work/plan_natural.jsonl,
logged as edit:natural / create:natural); without it they work on the first batch (work/plan.jsonl, edit / create).
Every run is resumable: edits.csv records what was done, and every item is re-checked live before editing.
"""
import argparse
import csv
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.parse
from collections import Counter

from common import (BATCHES, KIND_OF, REQUEST, ROOT, SWISSBOUNDARIES3D, SWISSNAMES3D, SWISSNAMES3D_ID, UA, bot_site,
                    cites, curl_json, dist_m, lv95_to_wgs84, out, ref_json)
from edit import build_edit, build_new, build_p131, drop_language
from edit_stations_areas import area_seen, builder
import plan as planmod
import userpage as page_builder

LOG = ROOT / "edits.csv"
FIELDS = ["time", "kind", "qid", "uuid", "name", "revid", "changes"]
MAX_REFUSED = 5          # edits Wikidata refuses in one batch before the run stops
CREATE_INTERVAL = 3.0    # seconds between new items: Wikidata limits how fast an account may create them


def logged():
    if not LOG.exists():
        return []
    with open(LOG, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def log(row):
    new = not LOG.exists()
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def refs_for_today():
    today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    return {SWISSNAMES3D: ref_json(SWISSNAMES3D, today), SWISSBOUNDARIES3D: ref_json(SWISSBOUNDARIES3D, today)}


def log_kind(row):
    """Kind of a plan row as logged: "edit" / "create" in the first batch, "edit:<batch>" / "create:<batch>" later."""
    return f"{row['kind']}:{row['batch']}" if row.get("batch") else row["kind"]


def log_key(kind, qid, uuid):
    """A logged row is one kind (with its batch) of one item (edits) or one swisstopo feature (creations)."""
    return kind, (qid if kind.split(":")[0] == "edit" else uuid)


def pending(kind, batch=""):
    done = {log_key(r["kind"], r["qid"], r["uuid"]) for r in logged()}
    _, rows = planmod.load(batch)
    return [r for r in rows if (kind in (None, r["kind"]))
            and log_key(log_kind(r), r.get("qid"), r["rec"]["uuid"]) not in done]


def chosen(qids, batch=""):
    """Plan rows of these items, logged or not, to re-check them (e.g. after a rule change)."""
    want = set(qids.split(","))
    return [r for r in planmod.load(batch)[1] if r["kind"] == "edit" and r["qid"] in want]


def fetch(ids):
    """Live JSON of up to 50 items (read-only, no login). While Wikidata is unreachable, waits and retries."""
    for attempt in range(1, 11):
        try:
            return curl_json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
                             "&props=info|labels|aliases|descriptions|claims&ids=" + "|".join(ids))["entities"]
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError):   # error page, API error, no network
            out(f"Wikidata did not answer, retrying in {attempt} min")
            time.sleep(60 * attempt)
    raise RuntimeError("Wikidata did not answer wbgetentities for 55 minutes")


# ---------------------------------------------------------------- saving

def save(site, entity_id, data, done, baserevid=None):
    """One wbeditentity call. The edit is refused if the item changed since it was read (baserevid), so the bot
    never overwrites someone else's change; on a label/description clash, that language is left out and retried."""
    import re
    from pywikibot.data import api
    from pywikibot.exceptions import APIError
    for attempt in range(1, 13):
        params = {"action": "wbeditentity", "data": json.dumps(data), "bot": 1, "token": site.tokens["csrf"],
                  "summary": f"{', '.join(done)} from swisstopo data ({REQUEST})"}
        params.update({"id": entity_id, "baserevid": baserevid} if entity_id else {"new": "item"})
        try:
            r = api.Request(site=site, parameters=params).submit()
            return r["entity"]["id"], r["entity"]["lastrevid"], done
        except APIError as e:
            text = str(e)
            if e.code in ("no-automatic-entity-id", "ratelimited") or "anti-abuse" in text:
                time.sleep(60 * min(attempt, 5))  # Wikidata's limit on new items (or edits): wait, then retry
                continue
            if e.code == "editconflict" or "edit conflict" in text.lower():
                return entity_id, None, ["skipped: edited by someone else since it was read"]
            m = re.search(r"associated with language code ([\w-]+)", text)
            if "label-with-description-conflict" not in text or not m:
                if e.code in ("modification-failed", "failed-save"):   # the item's existing data fails validation
                    why = re.search(r"wikibase-validator-[\w-]+", text)     # (e.g. a sitelink another item also has)
                    return entity_id, None, [f"skipped: refused by Wikidata ({why.group(0) if why else e.info[:120]})"]
                raise
            data, done = drop_language(data, done, m.group(1))
            if not data:
                return entity_id, None, done
    raise RuntimeError("Wikidata kept refusing: rate limit or label/description clashes")


def duplicate_nearby(site, rec, far=None, munis=()):
    """Live search: an item with this exact name that lies within 2 km, or in the same municipality (P131; an old
    position can be kilometres off: "Waldhaus" in Lützelflüh), or is a Swiss or place-like item without coordinates;
    with `far` (a later batch's "same_kind_name" distance), also an item of the same kind within `far`, unless its
    position comes from swissNAMES3D (then it is another swisstopo feature, e.g. a valley of the same name created
    just before). Family names, disambiguation pages and the like without coordinates do not count."""
    lat, lon = lv95_to_wgs84(rec["e"], rec["n"])
    hits = set()
    for lang in {rec["lang"], "en", "de"}:
        r = curl_json("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&type=item&limit=20"
                      f"&strictlanguage=0&language={lang}&search={urllib.parse.quote(rec['name'])}")
        hits |= {h["id"] for h in r.get("search", []) if h.get("match", {}).get("text", "").casefold() == rec["name"].casefold()}
    for q, ent in (fetch(sorted(hits)[:50]).items() if hits else []):
        claims = ent.get("claims", {})
        ids = lambda pid: {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in claims.get(pid, [])
                           if c["mainsnak"]["snaktype"] == "value"}
        if set(munis) & ids("P131"):
            return f"{q} has the same name in the same municipality"
        cs = [c for c in claims.get("P625", []) if c["mainsnak"]["snaktype"] == "value"]
        if not cs:
            if "Q39" in ids("P17") or not ids("P17") and any(KIND_OF.get(k) for k in ids("P31")):   # Swiss, or a
                # place of a known kind with no country (a German street of the same name does not count)
                return f"{q} has the same name and no coordinates"
            continue
        v = cs[0]["mainsnak"]["datavalue"]["value"]
        d = dist_m(v["latitude"], v["longitude"], lat, lon)
        if d <= 2000:
            return f"{q} has the same name within 2 km"
        kinds = {KIND_OF.get(c["mainsnak"].get("datavalue", {}).get("value", {}).get("id"))
                 for c in ent.get("claims", {}).get("P31", [])}
        if far and d <= far and rec["group"] in kinds and not any(cites(c, SWISSNAMES3D) for c in cs):
            return f"{q} has the same name and kind within {far // 1000} km"
    return None


def run_batch(site, rows, rate, refs):
    stats, saved = Counter(), []
    last = 0.0
    edits = [r for r in rows if r["kind"] == "edit"]
    live = {}
    for i in range(0, len(edits), 50):
        live.update(fetch([r["qid"] for r in edits[i:i + 50]]))
    for row in rows:
        rec = row["rec"]
        base = None
        if row["kind"] == "edit":
            ent = live.get(row["qid"], {"missing": ""})
            data, done = builder(row)(ent, row, refs)
            entity_id, base = row["qid"], ent.get("lastrevid")
        else:
            reason = duplicate_nearby(site, rec, BATCHES[row.get("batch", "")]["same_kind_name"].get(rec["group"]),
                                      row.get("munis", []))
            data, done, entity_id = (None, [f"skipped: {reason}"], None) if reason else (build_new(row, refs), ["new item"], None)
        revid = ""
        if data:
            interval = max(1.0 / rate, CREATE_INTERVAL if row["kind"] == "create" else 0.0)
            time.sleep(max(0.0, interval - (time.time() - last)))
            entity_id, revid, done = save(site, entity_id, data, done, base)
            last = time.time()
            if revid:
                saved.append({"qid": entity_id, "kind": row["kind"], "row": row, "changes": done})
        refused = bool(done) and done[0].startswith("skipped: refused")
        stats["saved" if revid else "refused" if refused else
              ("skipped" if done and done[0].startswith("skipped") else "no change")] += 1
        log({"time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": log_kind(row),
             "qid": entity_id or "", "uuid": rec["uuid"], "name": rec["name"], "revid": revid or "", "changes": "; ".join(done)})
        if stats["refused"] >= MAX_REFUSED:                # more than the odd bad item: likely a bug, so stop
            break
    return stats, saved


def publish_log():
    """Commit and push edits.csv so the public log on GitHub stays current; a failed push never stops the bot."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}    # never wait for a password prompt
    git = lambda *args: subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, env=env)
    try:
        git("add", "edits.csv", "plan_summary_task1.json")
        if git("diff", "--cached", "--quiet").returncode:
            saved = sum(1 for r in logged() if r["revid"])
            git("commit", "-q", "-m", f"Edit log: {saved} saved edits")
        git("pull", "-q", "--rebase")             # code changes pushed from elsewhere
        git("push", "-q")
    except OSError:                               # no git where the bot runs
        pass


# ---------------------------------------------------------------- checks after saving

def check(entity, kind, row, changes):
    """Read-back check of one saved edit; returns the changes that are not visible on the item."""
    cl, rec = entity.get("claims", {}), row["rec"]
    ok = lambda pid: any(cites(c, SWISSNAMES3D) for c in cl.get(pid, []))
    missing = []
    want = {"height": lambda: ok("P2044"), "source for height": lambda: ok("P2044"),
            "swisstopo height (preferred)": lambda: any(cites(c, SWISSNAMES3D) and c["rank"] == "preferred" for c in cl.get("P2044", [])),
            "source for position": lambda: ok("P625"),
            "precision of position (from its digits)": lambda: any(
                cites(c, SWISSNAMES3D) and c["mainsnak"]["datavalue"]["value"].get("precision") for c in cl.get("P625", [])),
            "swisstopo position (preferred)": lambda: any(cites(c, SWISSNAMES3D) and c["rank"] == "preferred" for c in cl.get("P625", [])),
            "default label": lambda: entity.get("labels", {}).get("mul", {}).get("value") == rec["name"],
            "official name": lambda: ok("P1448"),
            "municipality": lambda: any(cites(c, SWISSBOUNDARIES3D) for c in cl.get("P131", [])),
            "area": lambda: area_seen(entity, row), "source for area": lambda: area_seen(entity, row),
            "swissNAMES3D ID": lambda: any(cites(c, SWISSNAMES3D) and c["mainsnak"].get("datavalue", {}).get("value")
                                           == rec["uuid"].strip("{}").upper() for c in cl.get(SWISSNAMES3D_ID, []))}
    if kind.split(":")[0] == "create":                  # "create" or "create:<batch>"
        changes = ["official name", "municipality"] if row.get("munis") else ["official name"]
        missing += [] if any(c["mainsnak"]["datavalue"]["value"]["id"] == row["cls"] for c in cl.get("P31", [])) else ["class"]
        missing += [] if ok("P625") else ["position"]
        if row.get("add_id") and not want["swissNAMES3D ID"]():
            missing.append("swissNAMES3D ID")
    for ch in changes:
        if ch in want and not want[ch]():
            missing.append(ch)
        elif ch.endswith(" label") and len(ch) <= 9 and entity.get("labels", {}).get(ch.split()[0], {}).get("value") != rec["name"]:
            missing.append(ch)
    return missing


def verify(saved):
    problems = []
    for i in range(0, len(saved), 50):
        chunk = saved[i:i + 50]
        live = fetch([s["qid"] for s in chunk])
        for s in chunk:
            miss = check(live.get(s["qid"], {}), s["kind"], s["row"], s["changes"])
            if miss:
                problems.append((s["qid"], miss))
    return problems


# ---------------------------------------------------------------- the bot's user page

def task1_records():
    """Records per batch: the features of its current plan, plus any logged ones a later plan no longer has (such as
    the border items taken out on 10 October); and the releases the plans were built from."""
    plans = {b: planmod.load(b) for b in BATCHES if planmod.plan_path(b).exists()}
    keys = {}
    for b, (_, plan_rows) in plans.items():
        keys[b] = {log_key(log_kind(r), r.get("qid"), r.get("rec", {}).get("uuid")) for r in plan_rows}
    for r in logged():
        b = r["kind"].partition(":")[2]
        if b in keys:
            keys[b].add(log_key(r["kind"], r["qid"], r["uuid"]))
    return {b: len(k) for b, k in keys.items()}, [h["release"] for h, _ in plans.values()]


def userpage_text():
    """The bot's user page (see userpage.py), with the counts of task 1's plans in this clone."""
    records, releases = task1_records()
    return page_builder.build_text(task1_plans=records, releases=releases)


def userpage(site):
    records, releases = task1_records()
    return page_builder.save(site, "Update task status (automatic, after a run)", task1_plans=records, releases=releases)


# ---------------------------------------------------------------- CLI

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["plan", "preview", "run", "verify", "userpage", "status"])
    ap.add_argument("--kind", choices=["edit", "create"])
    ap.add_argument("--batches", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--rate", type=float, default=1 / 3, help="edits per second (approved: at most 20 per minute)")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--last", type=int, default=500)
    ap.add_argument("--qids", help="run: re-check these already logged items (comma-separated), e.g. after a rule change")
    ap.add_argument("--plan", choices=[b for b in BATCHES if b], default="",
                    help="plan, preview, run, verify, status: work on this later batch (default: the first, work/plan.jsonl)")
    a = ap.parse_args()

    if a.mode == "plan":
        planmod.build(a.plan)
    elif a.mode == "status":
        rows = logged()
        out(dict(Counter((r["kind"], "saved" if r["revid"] else r["changes"].split(":")[0] or "no change") for r in rows)))
        out(f"pending: {len(pending('edit', a.plan))} edits, {len(pending('create', a.plan))} creations")
    elif a.mode == "preview":
        refs = refs_for_today()
        rows = chosen(a.qids, a.plan) if a.qids else pending(a.kind or "edit", a.plan)[:a.n]
        edits = [r for r in rows if r["kind"] == "edit"]
        live = {}
        for i in range(0, len(edits), 50):
            live.update(fetch([r["qid"] for r in edits[i:i + 50]]))
        if len(rows) > 30:                       # large previews: totals only
            c = Counter()
            for r in edits:
                data, done = builder(r)(live.get(r["qid"], {"missing": ""}), r, refs)
                c.update(["(no change)"] if not done else [d.split(" (")[0].split(":")[0] for d in done])
            out(f"{len(edits)} items: {dict(c.most_common())}")
            return
        for r in rows:
            if r["kind"] == "edit":
                data, done = builder(r)(live.get(r["qid"], {"missing": ""}), r, refs)
                out(f"{r['qid']:>11} {r['rec']['name'][:28]:28} {'; '.join(done) or 'no change'}")
            else:
                d = build_new(r, refs)
                out(f"{'new':>11} {r['rec']['name'][:28]:28} {d['descriptions']['en']['value']} | "
                    f"{sorted(c['mainsnak']['property'] for c in d['claims'])}")
    elif a.mode == "verify":
        rows = [r for r in logged() if r["revid"] and r["kind"].partition(":")[2] == a.plan][-a.last:]
        _, plan_rows = planmod.load(a.plan)
        by_key = {log_key(log_kind(p), p.get("qid"), p["rec"]["uuid"]): p for p in plan_rows}
        saved = [{"qid": r["qid"], "kind": r["kind"], "row": by_key[log_key(r["kind"], r["qid"], r["uuid"])],
                  "changes": r["changes"].split("; ")} for r in rows
                 if log_key(r["kind"], r["qid"], r["uuid"]) in by_key]
        problems = verify(saved)
        out(f"verified {len(saved)} edits: {len(problems)} with problems", problems[:20])
    elif a.mode == "userpage":
        out("user page updated" if userpage(bot_site(1)) else "user page already up to date")
    elif a.mode == "run":
        if sys.platform == "win32":               # keep Windows awake while the run lasts (released on exit)
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        site = bot_site(max(1, round(1 / a.rate)))
        refs = refs_for_today()
        total = Counter()
        for b in range(1 if a.qids else a.batches):
            rows = chosen(a.qids, a.plan) if a.qids else pending(a.kind, a.plan)[:a.batch_size]
            if not rows:
                break
            stats, saved = run_batch(site, rows, a.rate, refs)
            total.update(stats)
            problems = verify(saved)
            publish_log()
            out(f"batch {b + 1}: {dict(stats)}; check: {len(saved) - len(problems)}/{len(saved)} ok")
            if problems:
                out("stopping: problems after saving:", problems[:10])
                break
            if stats["refused"] >= MAX_REFUSED:
                out(f"stopping: Wikidata refused {stats['refused']} edits in this batch (see edits.csv)")
                break
        userpage(site)
        out(f"run done: {dict(total)}; pending: {len(pending('edit', a.plan))} edits, "
            f"{len(pending('create', a.plan))} creations")


if __name__ == "__main__":
    main()
