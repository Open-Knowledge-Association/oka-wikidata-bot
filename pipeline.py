"""OKA bot on Wikidata: swisstopo task (Wikidata:Requests for permissions/Bot/OKA bot).

  python pipeline.py plan                      download the latest data and build work/plan.jsonl (local)
  python pipeline.py preview [--n 10]          show what the next edits would change (read-only)
  python pipeline.py run [--kind edit|create] [--batches K] [--batch-size N] [--rate R]
                                               edit as OKA bot; checks each batch after saving and stops on problems
  python pipeline.py run --qids Q1,Q2          re-check items already done (after a rule change); only what is missing
  python pipeline.py verify [--last N]         read back logged edits and check them
  python pipeline.py userpage                  refresh User:OKA bot (also done after every run)
  python pipeline.py status                    counts from edits.csv

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
from collections import Counter

from common import (REQUEST, ROOT, SWISSBOUNDARIES3D, SWISSNAMES3D, UA, bot_site, cites, curl_json, dist_m,
                    lv95_to_wgs84, out, ref_json)
from edit import build_edit, build_new, drop_language
import plan as planmod

LOG = ROOT / "edits.csv"
FIELDS = ["time", "kind", "qid", "uuid", "name", "revid", "changes"]
MAX_REFUSED = 5          # edits Wikidata refuses in one batch before the run stops


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


def pending(kind):
    done = {(r["kind"], r["qid"] if r["kind"] == "edit" else r["uuid"]) for r in logged()}
    _, rows = planmod.load()
    return [r for r in rows if (kind in (None, r["kind"]))
            and (r["kind"], r["qid"] if r["kind"] == "edit" else r["rec"]["uuid"]) not in done]


def chosen(qids):
    """Plan rows of these items, logged or not, to re-check them (e.g. after a rule change)."""
    want = set(qids.split(","))
    return [r for r in planmod.load()[1] if r["kind"] == "edit" and r["qid"] in want]


def fetch(ids):
    """Live JSON of up to 50 items (read-only, no login). While Wikidata is unreachable, waits and retries."""
    for attempt in range(1, 11):
        try:
            return curl_json("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
                             "&props=labels|aliases|descriptions|claims&ids=" + "|".join(ids))["entities"]
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
    for _ in range(6):
        params = {"action": "wbeditentity", "data": json.dumps(data), "bot": 1, "token": site.tokens["csrf"],
                  "summary": f"{', '.join(done)} from swisstopo data ({REQUEST})"}
        params.update({"id": entity_id, "baserevid": baserevid} if entity_id else {"new": "item"})
        try:
            r = api.Request(site=site, parameters=params).submit()
            return r["entity"]["id"], r["entity"]["lastrevid"], done
        except APIError as e:
            text = str(e)
            if e.code == "editconflict" or "edit conflict" in text.lower():
                return entity_id, None, ["skipped: edited by someone else since it was read"]
            m = re.search(r"associated with language code ([\w-]+)", text)
            if "label-with-description-conflict" not in text or not m:
                if e.code == "modification-failed":    # the item's existing data fails validation: skip it, log why
                    return entity_id, None, [f"skipped: refused by Wikidata ({e.info[:120]})"]
                raise
            data, done = drop_language(data, done, m.group(1))
            if not data:
                return entity_id, None, done
    raise RuntimeError("too many label/description clashes")


def duplicate_nearby(site, rec):
    """Live search: an item with this exact name that has no coordinates or lies within 2 km."""
    from pywikibot.data import api
    lat, lon = lv95_to_wgs84(rec["e"], rec["n"])
    hits = set()
    for lang in {rec["lang"], "en", "de"}:
        r = api.Request(site=site, parameters={"action": "wbsearchentities", "search": rec["name"], "language": lang,
                                               "strictlanguage": 0, "type": "item", "limit": 20}).submit()
        hits |= {h["id"] for h in r.get("search", []) if h.get("match", {}).get("text", "").casefold() == rec["name"].casefold()}
    for q, ent in (fetch(sorted(hits)[:50]).items() if hits else []):
        cs = [c for c in ent.get("claims", {}).get("P625", []) if c["mainsnak"]["snaktype"] == "value"]
        if not cs:
            return f"{q} has the same name and no coordinates"
        v = cs[0]["mainsnak"]["datavalue"]["value"]
        if dist_m(v["latitude"], v["longitude"], lat, lon) <= 2000:
            return f"{q} has the same name within 2 km"
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
            data, done = build_edit(ent, row, refs)
            entity_id, base = row["qid"], ent.get("lastrevid")
        else:
            reason = duplicate_nearby(site, rec)
            data, done, entity_id = (None, [f"skipped: {reason}"], None) if reason else (build_new(row, refs), ["new item"], None)
        revid = ""
        if data:
            time.sleep(max(0.0, 1.0 / rate - (time.time() - last)))
            entity_id, revid, done = save(site, entity_id, data, done, base)
            last = time.time()
            if revid:
                saved.append({"qid": entity_id, "kind": row["kind"], "row": row, "changes": done})
        refused = bool(done) and done[0].startswith("skipped: refused")
        stats["saved" if revid else "refused" if refused else
              ("skipped" if done and done[0].startswith("skipped") else "no change")] += 1
        log({"time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": row["kind"],
             "qid": entity_id or "", "uuid": rec["uuid"], "name": rec["name"], "revid": revid or "", "changes": "; ".join(done)})
        if stats["refused"] >= MAX_REFUSED:                # more than the odd bad item: likely a bug, so stop
            break
    return stats, saved


def publish_log():
    """Commit and push edits.csv so the public log on GitHub stays current; a failed push never stops the bot."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}    # never wait for a password prompt
    git = lambda *args: subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, env=env)
    try:
        git("add", "edits.csv")
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
            "municipality": lambda: any(cites(c, SWISSBOUNDARIES3D) for c in cl.get("P131", []))}
    if kind == "create":
        changes = ["official name", "municipality"] if row.get("munis") else ["official name"]
        missing += [] if any(c["mainsnak"]["datavalue"]["value"]["id"] == row["cls"] for c in cl.get("P31", [])) else ["class"]
        missing += [] if ok("P625") else ["position"]
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

def userpage(site):
    import pywikibot
    rows = logged()
    edited = len({r["qid"] for r in rows if r["kind"] == "edit" and r["revid"]})
    created = sum(1 for r in rows if r["kind"] == "create" and r["revid"])
    last = max((r["time"] for r in rows), default="")[:10]
    header, plan_rows = planmod.load()
    text = f"""{{{{Bot|7804j|task=Geographic feature updates from swisstopo data ([[Wikidata:Requests for permissions/Bot/OKA bot|approved request]])|flag=yes|framework=Pywikibot|language=Python|source=https://github.com/Open-Knowledge-Association/oka-wikidata-bot}}}}
''This page is updated automatically by the bot after each run, with the operator's permission.''

Run by [[User:7804j|7804j]] on behalf of [[m:OKA|OKA]] (Open Knowledge Association). To report a problem, please write on [[User talk:7804j]] or to info@oka.wiki. If the bot misbehaves and I'm not around, any administrator may block it.

'''Source code''' (public, MIT licence): [https://github.com/Open-Knowledge-Association/oka-wikidata-bot github.com/Open-Knowledge-Association/oka-wikidata-bot], with the [https://github.com/Open-Knowledge-Association/oka-wikidata-bot/blob/main/edits.csv list of every edit] the bot has made.

== Task: geographic feature updates from swisstopo data ==
Approved at [[Wikidata:Requests for permissions/Bot/OKA bot]]. Data: [https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d swissNAMES3D] ({{{{Q|{SWISSNAMES3D}}}}}, release {header['release']}) and [https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d swissBOUNDARIES3D] ({{{{Q|{SWISSBOUNDARIES3D}}}}}), from the Federal Office of Topography.

For Swiss mountains, summits, hills, passes, lakes, reservoirs and glaciers, the bot adds swisstopo as a source to matching heights and positions, adds missing heights, labels, official names and municipalities, and creates items for official features that have none. It never removes anything or changes existing labels, descriptions or classes. Where an existing value comes only from a Wikipedia import and is more than 5 m (height) or 50 m (summit position) off, swisstopo's value is added with preferred rank and {{{{P|7452}}}} {{{{Q|Q98386534}}}}. Pass heights are only sourced when they already match.

* Items edited: {edited:,}. Items created: {created:,}. Last run: {last}.
* Planned: {sum(1 for r in plan_rows if r['kind'] == 'edit'):,} items to check and {sum(1 for r in plan_rows if r['kind'] == 'create'):,} to create.
* Every edit is listed in [https://github.com/Open-Knowledge-Association/oka-wikidata-bot/blob/main/edits.csv edits.csv].
"""
    page = pywikibot.Page(site, "User:OKA bot")
    if page.exists() and page.text.strip() == text.strip():
        return False
    page.text = text
    page.save(summary="Update task status (automatic, after a run)", minor=True, bot=True)
    return True


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
    a = ap.parse_args()

    if a.mode == "plan":
        planmod.build()
    elif a.mode == "status":
        rows = logged()
        out(dict(Counter((r["kind"], "saved" if r["revid"] else r["changes"].split(":")[0] or "no change") for r in rows)))
        out(f"pending: {len(pending('edit'))} edits, {len(pending('create'))} creations")
    elif a.mode == "preview":
        refs = refs_for_today()
        rows = chosen(a.qids) if a.qids else pending(a.kind or "edit")[:a.n]
        edits = [r for r in rows if r["kind"] == "edit"]
        live = {}
        for i in range(0, len(edits), 50):
            live.update(fetch([r["qid"] for r in edits[i:i + 50]]))
        if len(rows) > 30:                       # large previews: totals only
            c = Counter()
            for r in edits:
                data, done = build_edit(live.get(r["qid"], {"missing": ""}), r, refs)
                c.update(["(no change)"] if not done else [d.split(" (")[0].split(":")[0] for d in done])
            out(f"{len(edits)} items: {dict(c.most_common())}")
            return
        for r in rows:
            if r["kind"] == "edit":
                data, done = build_edit(live.get(r["qid"], {"missing": ""}), r, refs)
                out(f"{r['qid']:>11} {r['rec']['name'][:28]:28} {'; '.join(done) or 'no change'}")
            else:
                d = build_new(r, refs)
                out(f"{'new':>11} {r['rec']['name'][:28]:28} {d['descriptions']['en']['value']} | "
                    f"{sorted(c['mainsnak']['property'] for c in d['claims'])}")
    elif a.mode == "verify":
        rows = [r for r in logged() if r["revid"]][-a.last:]
        _, plan_rows = planmod.load()
        by_key = {(p["kind"], p["qid"] if p["kind"] == "edit" else p["rec"]["uuid"]): p for p in plan_rows}
        saved = [{"qid": r["qid"], "kind": r["kind"], "row": by_key[(r["kind"], r["qid"] if r["kind"] == "edit" else r["uuid"])],
                  "changes": r["changes"].split("; ")} for r in rows
                 if (r["kind"], r["qid"] if r["kind"] == "edit" else r["uuid"]) in by_key]
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
            rows = chosen(a.qids) if a.qids else pending(a.kind)[:a.batch_size]
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
        out(f"run done: {dict(total)}; pending: {len(pending('edit'))} edits, {len(pending('create'))} creations")


if __name__ == "__main__":
    main()
