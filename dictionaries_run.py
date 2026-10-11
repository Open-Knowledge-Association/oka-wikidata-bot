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
import subprocess
import sys
import urllib.parse
from itertools import zip_longest
from pathlib import Path

import dictionaries as d
from dictionaries import IDS, WORK2, WORKS, items, out, qlever

APPROVED = False                  # set once a bureaucrat approves the request; until then `run` refuses to start
HERE = Path(__file__).parent
plan_file = lambda work: WORK2 / f"full_plan_{work}.jsonl"
LOG = HERE / "dictionary_edits.csv"
LEFT = HERE / "dictionary_left_alone.csv"
SUMMARY_PAGE = "User:OKA bot/Dictionaries"
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


def plan():
    work = sys.argv[2]
    entries, skipped = linked(work)
    out(f"{work}: {len(entries)} entries on one item each; {len(skipped)} left out (identifier on several items, or several on one)")
    for n, e in enumerate(entries, 1):
        e["title"], e["facts"], e["notes"] = (d.hds_facts if e["work"] == "hds" else d.snl_facts)(e["id"], e["kind"])
        if n % 5000 == 0:
            out(f"facts for {n} of {len(entries)} entries")
    held_elsewhere(entries)
    entries = [e for e in entries if e["facts"] or e["notes"]]
    with open(plan_file(work), "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(WORK2 / f"full_plan_{work}_skipped.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([("work", "entry", "items", "reason"), *skipped])
    out(f"{work}: {len(entries)} entries with something to add or check, {sum(len(e['facts']) for e in entries)} facts")


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
    done = logged()
    todo = [e for w in WORKS if plan_file(w).exists() for e in map(json.loads, open(plan_file(w), encoding="utf-8"))
            if (e["work"], e["id"]) not in done]
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
    {"plan": plan, "run": run}[sys.argv[1]]()
