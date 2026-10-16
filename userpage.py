"""User:OKA bot: the bot's page, written by whichever run finishes a batch (both tasks use this module).

The page is built from the public logs only (edits.csv for task 1, dictionary_edits.csv and dictionary_left_alone.csv
for task 2) and from the number of records each task has planned (plan_summary_task1.json, plan_summary_task2.json,
committed with the logs), so the swisstopo run and the reference-works run write the same page.
"""
import csv
import datetime
import json

from common import BATCHES, ROOT, SWISSBOUNDARIES3D, SWISSNAMES3D

REPO = "https://github.com/Open-Knowledge-Association/oka-wikidata-bot/blob/main/"
TASK1_LOG, TASK2_LOG, TASK2_LEFT = ROOT / "edits.csv", ROOT / "dictionary_edits.csv", ROOT / "dictionary_left_alone.csv"
SUMMARY = {1: ROOT / "plan_summary_task1.json", 2: ROOT / "plan_summary_task2.json"}
WORKS = {"hds": ("Q642074", "Historical Dictionary of Switzerland"), "snl": ("Q746368", "Store norske leksikon"),
         "nkl": ("Q11992482", "Norsk kunstnerleksikon"), "sml": ("Q19378370", "Store medisinske leksikon")}

# The reasons for each swisstopo batch's limits, as notes under task 1
NOTES = {
    "natural": "Valleys, ridges, waterfalls and caves get no heights. No new items are made for caves: swisstopo's type "
               "\"Grotte, Hoehle\" also covers mine entrances, Lourdes grottoes and natural arches.",
    "settlements": "Localities and neighbourhoods get no heights. New items are made only for localities of 100 to 9,999 "
                   "inhabitants: larger ones all have items, and 60,722 of swissNAMES3D's 68,508 localities have fewer "
                   "than 100 inhabitants (55,156 fewer than 20), mostly single farms and hamlets.",
    "p131": "Buildings, archaeological sites, fountains and similar items with a position but no municipality get only "
            "the municipality their position lies in, from [https://www.swisstopo.admin.ch/en/landscape-model-"
            "swissboundaries3d swissBOUNDARIES3D]: they are not named features in swissNAMES3D. Items that span "
            "municipalities (waters, glaciers, regions, roads, railway lines, bridges, tunnels) are left out.",
    "stations": "Railway stations and other public transport stops get a source for their position, their official name "
                "and, where missing, their municipality, but no labels: station labels follow their own conventions "
                "(\"Bern railway station\", \"Bahnhof Bern\"), which differ from the official name by design.",
    "areas": "Municipalities get their area from [https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d "
             "swissBOUNDARIES3D], in square kilometres with the date the boundaries are valid on, where the item has no "
             "area or a different one; where it has older values, the new one gets preferred rank as the most recent "
             "({{P|7452}} {{Q|Q71533355}}), unless another value is already preferred. An equal area without a date gets "
             "swisstopo as a source; an equal area with an older date is left as it is. Municipalities with a share of "
             "a large lake are left out for now: their area statistics leave the lake out, swisstopo's include it.",
}


# Why values are not added yet: one column each in the table under the overview, with how each kind gets resolved
REASONS = [
    ("duplicate", "Possible duplicate item", "task 1: an item of the same name lies nearby or has no coordinates, so "
     "no new item is made until it is clear whether it is the same feature. The bot compares the two items' kind and "
     "municipality; the rest is checked by hand."),
    ("place", "Place not identified", "the place name in the entry matches no place item, or several (a country or "
     "region only, a former municipality, a church). The matching rules and a checked list of names are being "
     "extended."),
    ("occupation", "Occupation not mapped", "the work's word for the occupation is not yet linked to an occupation "
     "item. The list of words is being extended; offices such as king or bishop are left out by design."),
    ("date", "Date unclear", "the item has the entry's date in another calendar model (the entry's day is Julian, as "
     "used there then, the item's Gregorian: probably wrong on the item), the calendar of the entry's date cannot be "
     "told and the item already has the date, or the source gives the date in a form the bot cannot read."),
    ("identifier", "Identifier issue", "the identifier is also on another item, or the entry's life years do not fit "
     "the item's (probably another person). Checked by hand, then corrected."),
    ("unread", "Entry not read", "the entry has moved or could not be read; moved entries are read again at their new "
     "address."),
    ("other", "Other", "anything else; each case is in the log."),
]


def reason_of(note):
    """The REASONS key of one note (task 2's left-alone log) or skipped change (task 1's log)."""
    if "has the same name" in note:
        return "duplicate"
    if "not matched to one" in note:
        return "place"
    if "not mapped" in note:
        return "occupation"
    if "calendar" in note or "clear format" in note or "notes '" in note or "not used" in note:
        return "date"
    if "wrong item" in note or "another person" in note or "already on" in note:
        return "identifier"
    if "readable" in note or "not marked free" in note or "item not found" in note:
        return "unread"
    return "other"


def rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if path.exists() else []


def planned(task, local):
    """Records per batch or work: counted from the plan files where this clone has them (and saved for the other
    task's runs), else as last saved by the run that has them."""
    saved = json.loads(SUMMARY[task].read_text(encoding="utf-8")) if SUMMARY[task].exists() else {}
    if local:
        saved.update(local)
        SUMMARY[task].write_text(json.dumps(saved, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return saved


def kind_of(change):
    """What one logged change did: 'source', 'names', 'statement', or None (no change, a note, a fix of form)."""
    if not change or change.startswith(("skipped", "precision of")) or "left out" in change or "deprecated" in change:
        return None
    if change.startswith("source for"):
        return "source"
    if change == "other names" or change.endswith(" label") or change == "default label" \
            or change.startswith("names in other languages"):
        return "names"
    if change == "new item":
        return None
    return "statement"


def counts(log_rows, key):
    c = {"done": len({key(r) for r in log_rows}), "edited": 0, "created": 0, "statement": 0, "source": 0, "names": 0,
         "unclear": 0,
         "last": max((r["time"] for r in log_rows), default="")[:10]}
    for r in log_rows:
        changes = [x for x in (r.get("changes") or "").split("; ") if x]
        if r.get("revid"):
            c["created" if r["kind"].split(":")[0] == "create" else "edited"] += 1
        skipped = [x for x in changes if x.startswith("skipped")]
        if skipped:
            c["unclear"] += 1
            why = c.setdefault("why", {})
            why[reason_of(skipped[0])] = why.get(reason_of(skipped[0]), 0) + 1
        for x in changes:
            k = kind_of(x)
            if k:
                c[k] += 1
    return c


def table_rows(task1_plans=None, task2_plans=None):
    """One row per swisstopo batch (task 1) and per reference work (task 2)."""
    out = []
    t1 = rows(TASK1_LOG)
    plans1 = planned(1, task1_plans)
    for b, cfg in BATCHES.items():
        mine = [r for r in t1 if r["kind"].partition(":")[2] == b]
        if not mine and b not in plans1:
            continue
        source, q = ("swissBOUNDARIES3D", SWISSBOUNDARIES3D) if b in ("p131", "areas") else ("swissNAMES3D", SWISSNAMES3D)
        label = f"[[{q}|{source}]]: " + ", ".join(cfg["covers"])
        c = counts(mine, lambda r: (r["kind"], r["qid"] if r["kind"].startswith("edit") else r["uuid"]))
        out.append((1, label, plans1.get(b), c, None))
    t2, left = rows(TASK2_LOG), rows(TASK2_LEFT)
    plans2 = planned(2, task2_plans)
    for w, (q, name) in WORKS.items():
        mine = [r for r in t2 if r["work"] == w]
        if not mine and w not in plans2:
            continue
        notes = [n for n in left if n["work"] == w]
        differs = sum(1 for n in notes if "different value" in n["note"])
        c = counts(mine, lambda r: r["entry"])
        c["unclear"] = len(notes) - differs
        c["why"] = {}
        for n in notes:
            if "different value" not in n["note"]:
                c["why"][reason_of(n["note"])] = c["why"].get(reason_of(n["note"]), 0) + 1
        out.append((2, f"[[{q}|{name}]]", plans2.get(w), c, differs))
    return out


def overview(task1_plans=None, task2_plans=None):
    fmt = lambda n: "–" if n is None else f"{n:,}"
    lines = ['{| class="wikitable sortable" style="text-align:right"',
             "! Task !! Source !! Records !! Done !! Items edited !! Items created !! Statements added !! "
             "Sources added !! Names added !! Not added: Wikidata differs !! Not added: unclear !! Last edit"]
    for task, label, records, c, differs in table_rows(task1_plans, task2_plans):
        lines += ["|-", f'| {task} || style="text-align:left" | {label} || {fmt(records)} || {fmt(c["done"])} || '
                        f'{fmt(c["edited"])} || {fmt(c["created"] if task == 1 else None)} || {fmt(c["statement"])} || '
                        f'{fmt(c["source"])} || {fmt(c["names"])} || {fmt(differs)} || {fmt(c["unclear"])} || '
                        f'{c["last"] or "–"}']
    lines.append("|}")
    lines += [
        "",
        "What the columns count:",
        "* '''Records''': the swisstopo features, or the entries of a reference work, that the batch covers (those "
        "linked to an item, and features that get a new item).",
        "* '''Done''': records the bot has worked through so far, whether it changed something or not.",
        "* '''Items edited''' and '''Items created''': items the bot saved (one edit per item and batch), and new items "
        "for features that had none.",
        "* '''Statements added''': new values, each with its source. '''Sources added''': the source added to a "
        "statement that already had the same value. '''Names added''': labels and aliases.",
        "* '''Not added: Wikidata differs''': values where the source says something other than Wikidata. The bot "
        "changes nothing there and lists the case in the log, to be checked against further sources.",
        "* '''Not added: unclear''': values the bot could not settle with certainty (a place name with several "
        "candidates, a date the source marks as uncertain, a possible duplicate item, an entry it could not read). "
        "They are listed in the log too.",
        f"Logs: task 1 [{REPO}edits.csv edits.csv] (and [{REPO}corrections.csv corrections.csv]); task 2 "
        f"[{REPO}dictionary_edits.csv dictionary_edits.csv] and, for the values not added, "
        f"[{REPO}dictionary_left_alone.csv dictionary_left_alone.csv]. Updated "
        f"{datetime.datetime.now(datetime.timezone.utc):%d %B %Y, %H:%M} UTC.",
        "",
        "=== Not added: unclear, by reason ===",
        "Unclear cases are resolved over time: by better matching, by checked lists of names and words, or by hand, "
        "after which the bot makes the decided edits.",
        "",
    ] + by_reason(task1_plans, task2_plans)
    return "\n".join(lines)


def by_reason(task1_plans=None, task2_plans=None):
    """The unclear cases of each source, one column per reason, and what each reason means."""
    fmt = lambda n: f"{n:,}" if n else "–"
    keys = [k for k, _, _ in REASONS]
    out = ['{| class="wikitable sortable" style="text-align:right"',
           "! Task !! Source !! " + " !! ".join(title for _, title, _ in REASONS) + " !! Total"]
    for task, label, _, c, _ in table_rows(task1_plans, task2_plans):
        why = c.get("why", {})
        out += ["|-", f'| {task} || style="text-align:left" | {label} || '
                      + " || ".join(fmt(why.get(k, 0)) for k in keys) + f" || {fmt(sum(why.values()))}"]
    out += ["|}", ""] + [f"* \'\'\'{title}\'\'\': {text}" for _, title, text in REASONS]
    return out


def task1_section(releases=()):
    releases = list(dict.fromkeys(releases))
    release = ", ".join(r.split("_", 1)[1] for r in releases if r.startswith("swissnames3d_"))
    bounds = ", ".join(r.split("_", 1)[1] for r in releases if r.startswith("swissboundaries3d_"))
    names_release = f", release {release}" if release else ""
    bounds_release = f", release {bounds}" if bounds else ""
    logged = {r["kind"].partition(":")[2] for r in rows(TASK1_LOG)}
    notes = "".join(f"\n* {NOTES[b]}" for b in BATCHES if b in NOTES and (b in logged or b in planned(1, None)))
    return f"""== Task 1: geographic features from swisstopo ==
Approved at [[Wikidata:Requests for permissions/Bot/OKA bot]]. Data from the Federal Office of Topography: [https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d swissNAMES3D] ({{{{Q|{SWISSNAMES3D}}}}}{names_release}) and [https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d swissBOUNDARIES3D] ({{{{Q|{SWISSBOUNDARIES3D}}}}}{bounds_release}).

For Swiss geographic features, the bot adds swisstopo as a source to matching heights and positions, adds missing heights, labels, official names and municipalities, and creates items for official features that have none. Where an existing value comes only from a Wikipedia import and is more than 5 m (height) or 50 m (summit position) off, swisstopo's value is added with preferred rank and {{{{P|7452}}}} {{{{Q|Q98386534}}}}. Pass heights are only sourced when they already match.

The bot does not remove other editors' statements, and does not change existing labels, descriptions or classes. It changes an existing statement only to add a source, to give an old position saved without a precision the precision its digits imply, or to correct its own mistakes (listed in [{REPO}corrections.csv corrections.csv]).{notes}
"""


def task2_section():
    return """== Task 2: facts from reference works ==
Approved at [[Wikidata:Requests for permissions/Bot/OKA bot 2]]. Sources: the [[Q642074|Historical Dictionary of Switzerland]] (HDS), [[Q746368|Store norske leksikon]] (SNL) and the other works on snl.no, [[Q11992482|Norsk kunstnerleksikon]] and [[Q19378370|Store medisinske leksikon]].

For each entry linked to an item, the bot adds the facts the entry states (for people, dates and places of birth and death, occupations, relatives and places of origin; for organisations and places, the corresponding facts), each with a reference to the entry, and adds the entry as a source where the item already has the same value. Where the item has only the year and the entry the exact date, the exact date is added with preferred rank ({{P|7452}} {{Q|Q71536040}}). Dates are entered in the calendar used where they happened, as [[Help:Dates]] asks: Julian in Denmark–Norway until 1700, in Sweden and Finland until 1753, in Britain and Ireland until 1752; where that cannot be told (regions that changed at different times), with {{P|1480}} {{Q|Q18195782}}. Dates the work gives as approximate get {{P|1480}} {{Q|Q5727902}}. Where the entry disagrees with an existing value, nothing is added and the case is listed. Where SNL has renamed an entry, the item's old ID is kept with deprecated rank ({{P|2241}}: {{Q|Q45403344}}) and the current one is added. Only facts are taken; no text is copied.
"""


def build_text(task1_plans=None, task2_plans=None, releases=()):
    return f"""{{{{Bot|7804j|task=Geographic feature updates from swisstopo data ([[Wikidata:Requests for permissions/Bot/OKA bot|approved request]]); facts from reference works such as the Historical Dictionary of Switzerland and Store norske leksikon ([[Wikidata:Requests for permissions/Bot/OKA bot 2|approved request]])|flag=yes|framework=Pywikibot|language=Python|source=https://github.com/Open-Knowledge-Association/oka-wikidata-bot}}}}
''This page is updated automatically by the bot after its runs, with the operator's permission.''

Run by [[User:7804j|7804j]] on behalf of [[m:OKA|OKA]] (Open Knowledge Association). To report a problem, please write on [[User talk:7804j]] or to info@oka.wiki. If the bot misbehaves and I'm not around, any administrator may block it.

'''Source code''' (public, MIT licence): [https://github.com/Open-Knowledge-Association/oka-wikidata-bot github.com/Open-Knowledge-Association/oka-wikidata-bot].

== Overview ==
{overview(task1_plans, task2_plans)}

{task1_section(releases)}
{task2_section()}"""


def save(site, summary, task1_plans=None, task2_plans=None, releases=()):
    """Write the page if it changed; returns whether it did."""
    import pywikibot
    text = build_text(task1_plans, task2_plans, releases)
    page = pywikibot.Page(site, "User:OKA bot")
    if page.exists() and page.text.strip() == text.strip():
        return False
    page.text = text
    page.save(summary=summary, minor=True, bot=True)
    return True
