"""OKA bot, task 3 (Wikidata:Requests for permissions/Bot/OKA bot 3): the test run, about 10 edits per source.

  python task3.py sample <work> [n]   read up to n linked entries (default 30) and keep the first 10 with something
                                      to add (read-only): work/task3/sample_<work>.json
  python task3.py preview [work]      show what each kept entry's edit would change (read-only)
  python task3.py run [work]          save the kept edits as OKA bot, then list all test edits at User:OKA bot/Test run 3
  python task3.py report              rewrite User:OKA bot/Test run 3 from the log

The edits are task 2's (dictionaries.build and save): facts referenced to the entry, a source for equal values, and
nothing where the item says something else (listed under "Left alone").
"""
import datetime
import importlib
import json
import sys
import time

import dictionaries as d
import sources3 as s

REPORT_PAGE = "User:OKA bot/Test run 3"
PER_WORK = 10
URLS = {"ndb": "https://www.deutsche-biographie.de/{}.html", "lex": "https://lex.dk/{}",
        "dkl": "https://kvindebiografiskleksikon.lex.dk/{}", "kb": "https://kansallisbiografia.fi/kansallisbiografia/henkilo/{}",
        "skbl": "https://www.skbl.se/sv/artikel/{}", "sbl": "https://sok.riksarkivet.se/sbl/Presentation.aspx?id={}",
        "oebl": "https://www.biographien.ac.at/oebl/oebl_{}.xml",
        "wgw": "https://www.geschichtewiki.wien.gv.at/Special:URIResolver/?curid={}",
        "hessbio": "https://www.lagis-hessen.de/de/subjects/idrec/sn/bio/id/{}", "saebi": "https://saebi.isgv.de/gnd/{}",
        "sbi": "https://www.slovenska-biografija.si/oseba/sbi{}/", "uppslag": "https://uppslagsverket.fi/sv/sok/view-170045-{}",
        "bpn": "http://www.biografischportaal.nl/persoon/{}", "ehlfl": "https://historisches-lexikon.li/{}",
        "dodis": "https://dodis.ch/{}"}


def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")


def reader(work):
    return importlib.import_module(f"readers3.{work}")


def sample_path(work):
    return s.WORK3 / f"sample_{work}.json"


def sample(work, n=30):
    """Read linked entries until PER_WORK of them have something to add; keep those."""
    r = reader(work)
    chosen, seen = [], 0
    for ident, qid in r.candidates(n):
        if len(chosen) >= PER_WORK:
            break
        seen += 1
        try:
            got = r.read(ident)
        except Exception as e:                    # a page the reader cannot read: reported, not used
            d.out(f"{work} {ident}: not read ({e!r})")
            continue
        entry = {"work": work, "id": ident, "qid": qid, "matched_by": "identifier", **got}
        live = d.items([qid])
        labels = d.place_labels(live, [entry])
        claims, done, notes = d.build(live.get(qid, {}), entry, today(), labels)
        d.out(f"{work} {ident:>24} {qid:>11} {entry['title'][:30]:30} {'; '.join(done) or 'no change'}"
              + (f"   [{' | '.join(notes)}]" if notes else ""))
        if claims:
            chosen.append(entry)
    s.WORK3.mkdir(parents=True, exist_ok=True)
    sample_path(work).write_text(json.dumps(chosen, ensure_ascii=False, indent=1), encoding="utf-8")
    d.out(f"-- {work}: {len(chosen)} entries with something to add, of {seen} read")


def chosen_entries(work=None):
    works = [work] if work else list(s.WORKS3)
    return [e for w in works if sample_path(w).exists()
            for e in json.loads(sample_path(w).read_text(encoding="utf-8"))]


def preview(work=None):
    chosen = chosen_entries(work)
    live = d.items(sorted({c["qid"] for c in chosen}))
    labels = d.place_labels(live, chosen)
    for c in chosen:
        claims, done, notes = d.build(live.get(c["qid"], {}), c, today(), labels)
        d.out(f"{c['work']:7} {c['qid']:>11} {c['title'][:34]:34} {'; '.join(done) or 'no change'}"
              + (f"   [{' | '.join(notes)}]" if notes else ""))


def run(work=None):
    from common import bot_site
    site = bot_site(5)                           # writes the login settings; pywikibot may only be imported after this
    d.REQUEST2 = s.REQUEST3                      # the edit summaries link this request
    chosen = chosen_entries(work)
    log = s.WORK3 / "testrun.json"
    rows = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    done_before = {(r["work"], r["id"]) for r in rows}
    chosen = [c for c in chosen if (c["work"], c["id"]) not in done_before]
    live = d.items(sorted({c["qid"] for c in chosen}))
    labels = d.place_labels(live, chosen)
    for c in chosen:
        ent = live.get(c["qid"], {})
        claims, done, notes = d.build(ent, c, today(), labels)
        if not claims:
            d.out(f"{c['work']} {c['qid']}: nothing to add any more")
            continue
        revid, err = d.save(site, c["qid"], claims, done, c["work"], ent.get("lastrevid"))
        d.out(f"{c['work']} {c['qid']:>11} {'saved ' + str(revid) if revid else err}: {'; '.join(done)}")
        if revid:
            rows.append({**c, "done": done, "revid": revid})
            log.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(5)
    write_report(site)


def write_report(site=None):
    from common import bot_site
    site = site or bot_site(5)
    import pywikibot
    rows = json.loads((s.WORK3 / "testrun.json").read_text(encoding="utf-8"))
    chosen = [c for c in chosen_entries() if (c["work"], c["id"]) in {(r["work"], r["id"]) for r in rows}]
    live = d.items(sorted({c["qid"] for c in chosen}))
    labels = d.place_labels(live, chosen)
    notes = [(c, n) for c in chosen for n in d.build(live.get(c["qid"], {}), c, today(), labels)[2]]
    lines = [f"This page lists the test edits for [[Wikidata:Requests for permissions/Bot/OKA bot 3]]. The bot wrote it. "
             "Each edit adds or sources facts from one entry of the work named, linked to the item by the work's "
             "identifier; the facts are referenced to the entry.", "",
             '{| class="wikitable sortable"', "! # !! Work !! Entry !! Item !! Changes !! Diff"]
    for i, r in enumerate(rows, 1):
        work_q = s.WORKS3[r["work"]][0]
        url = URLS[r["work"]].format(r["id"])
        lines += ["|-", f"| {i} || {{{{Q|{work_q[1:]}}}}} || [{url} {r['title']}] || {{{{Q|{r['qid'][1:]}}}}} "
                        f"|| {'; '.join(r['done'])} || [[Special:Diff/{r['revid']}|diff]]"]
    lines.append("|}")
    lines += ["", "== Left alone ==", "Values the bot does not add for these entries, and why (checked when this page "
              "was written). Most are occupations in words the bot cannot map to an item yet: a map of the most "
              "frequent occupation words of each work (German and Norwegian first), each checked on Wikidata, is "
              "being built, so that the full run adds them."]
    lines += [f"* {s.WORKS3[c['work']][2]}, {c['title']} ({{{{Q|{c['qid'][1:]}}}}}): {d.clean(n)}" for c, n in notes] \
        or ["* none"]
    fixes = s.WORK3 / "corrections.json"         # the bot's own mistakes in the test run, and how they were undone
    if fixes.exists():
        lines += ["", "== Corrections ==", "Test edits the bot corrected itself:"]
        lines += [f"* {{{{Q|{x['qid'][1:]}}}}}: {x['what']} ([[Special:Diff/{x['revid']}|diff]])"
                  for x in json.loads(fixes.read_text(encoding="utf-8"))]
    page = pywikibot.Page(site, REPORT_PAGE)
    page.text = "\n".join(lines) + "\n"
    page.save(summary=f"Test run report for [[Wikidata:Requests for permissions/Bot/OKA bot 3]] ({len(rows)} edits)",
              bot=True)
    d.out(f"{len(rows)} test edits listed at {REPORT_PAGE}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    mode, rest = sys.argv[1], sys.argv[2:]
    if mode == "sample":
        sample(rest[0], int(rest[1]) if len(rest) > 1 else 30)
    elif mode == "report":
        write_report()
    else:
        {"preview": preview, "run": run}[mode](rest[0] if rest else None)
