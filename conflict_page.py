"""The page User:OKA bot/Disagreement checks: how the bot settles a disagreement with an unsourced Wikidata value, and
each checked case with the sources found and the outcome. Nothing is edited on Wikidata here.

  python conflict_page.py build LOG [TITLE]   write work/conflict_page.wiki from a conflict_check log
  python conflict_page.py batch LOG N TITLE [SAMPLE]   write work/conflict_batch.wiki: a batch for the operator's
                                              sample check (a random SAMPLE of its cases when it is large)
  python conflict_page.py save                save it as User:OKA bot/Disagreement checks (OKA bot)
  python conflict_page.py save-batch N        save the batch page as User:OKA bot/Disagreement checks/Batch N
"""
import collections
import csv
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

from common import WORK
from conflict_check import URLS, WORK_NAMES, counted, decide, nested_row

PAGE = "User:OKA bot/Disagreement checks"
OUT = WORK / "conflict_page.wiki"
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}    # $ per million tokens in, out
SEARCH = 0.01                                                                    # $ per web search
SUPPORTS = {"wikidata": "Wikidata's value", "entry": "the entry's value", "both": "fits both", "other": "another value"}
OUTCOME = {"prefer": "prefer the entry's value", "both": "both", "report": "report", "retry": "checked again later",
           "same": "no disagreement"}

HOW = """This page shows how [[User:OKA bot|OKA bot]] settles a disagreement between a reference work and a Wikidata \
value that has no source, as described in [[Wikidata:Requests for permissions/Bot/OKA bot 3|request 3]]. \
'''Nothing on this page has been changed on Wikidata.''' Comments are welcome on the request or on the talk page.

== How it works ==
# '''Which cases.''' A date or place of birth or death where the entry gives another value and Wikidata's value has \
no reference, or only {{P|143}}. Differences that are not disagreements are settled before this step: one place \
inside the other, the same day in the other calendar, a year beside the full date. Where Wikidata's value has any \
other reference, nothing is searched: two sources disagree, so the entry's value is added beside it at the same rank.
# '''The search.''' A language model (Claude, with a web search tool, at most 5 searches per case) gets the person, \
the fact, both values and the entry's link. It lists each page it read that states the fact, with the link, the \
page's words and the value they support, and then says what these sources establish: Wikidata's value, the entry's \
value, a genuine split, or nothing clear. Wikipedia, Wikidata and their copies, the work itself, and sites anyone can \
edit (family trees, Find a Grave, forums) do not count. The prompt and the rules below are in \
[https://github.com/Open-Knowledge-Association/oka-wikidata-bot/blob/main/conflict_check.py conflict_check.py].
# '''The decision''' is made by code, from the sources counted and the model's reading of them:
#* '''prefer the entry's value''': no counted source supports Wikidata's value. The entry is a source; an unsourced \
value or a Wikipedia import is not. The entry's value is added with its reference, at preferred rank as the best \
referenced value ({{P|7452}}: {{Q|Q98386534}}). Wikidata's value stays as it is: the bot neither removes nor \
deprecates it. The case is listed here with its evidence, so that editors can decide whether the old value should \
go. Where other sources confirm the entry's value, the list says that the old value is probably wrong.
#* '''both''': counted sources support each value (one each is enough), and the model does not read them as \
establishing Wikidata's; or the fact is unclear: nothing supports Wikidata's value while sources give yet other \
values, a place name fits both values, or the model's reading contradicts the sources. The entry's value is added \
beside Wikidata's, at the same rank, with the entry as its reference: the two values side by side show that the \
fact is disputed.
#* '''report''': counted sources support Wikidata's value and none supports the entry's (other values found do not \
matter), or they establish Wikidata's value. The entry's value is added with deprecated rank ({{P|2241}}: \
{{Q|Q29998666}}), referenced to the entry, so that the item shows that the work states otherwise and the value is \
not added again; the error is also reported to the work.
#: The model's reading can stop an edit but cannot cause one. The bot never removes or deprecates a value that \
someone else added.
#: The sources found are only used to decide, and are logged here. They are not cited on Wikidata: the bot cites only \
the works of its approved tasks.
# '''Before any edit''', each batch is listed on this page with its evidence, and the operator checks a sample. \
The edit summary of each change links its case here.
"""


def esc(s, n=None):
    """Text from a web page, safe inside a table cell and shortened to n characters."""
    s = re.sub(r"\s+", " ", s or "").strip()
    if n and len(s) > n:
        s = s[:n - 1].rstrip() + "…"
    for a, b in (("&", "&amp;"), ("[", "&#91;"), ("]", "&#93;"), ("{", "&#123;"), ("}", "&#125;"), ("|", "&#124;"),
                 ("<", "&lt;"), (">", "&gt;"), ("''", "'&#39;"), ("~~~", "~&#126;~")):
        s = s.replace(a, b)
    return s


def link(url):
    """An external link labelled with its site."""
    url = url.strip().replace(" ", "%20").replace("[", "%5B").replace("]", "%5D")
    site = urllib.parse.urlsplit(url).netloc.removeprefix("www.") or url[:40]
    return f"[{url} {site}]"


def value(v, url=None):
    """'Frederiksberg (Q30096)' → the label linked to its item (or to the entry, with the item's ID beside it);
    dates as they are."""
    parts = []
    for part in v.split("; "):
        m = re.fullmatch(r"(.+) \((Q\d+)\)", part)
        if url:
            parts.append(f"[{url} {esc(m[1])}] <small>([[{m[2]}|{m[2]}]])</small>" if m else f"[{url} {esc(part)}]")
        else:
            parts.append(f"[[{m[2]}|{esc(m[1])}]]" if m else esc(part))
    return "; ".join(parts)


def cost(rows, tokens_share=1.0):
    """Dollars and searches; tokens_share 0.5 for a run through the Message Batches API (half the token price)."""
    tin = sum(int(r["input_tokens"] or 0) for r in rows)
    tout = sum(int(r["output_tokens"] or 0) for r in rows)
    searches = sum(int(r["searches"] or 0) for r in rows)
    pin, pout = PRICES.get(rows[0]["model"], (0, 0))
    return (tin * pin / 1e6 + tout * pout / 1e6) * tokens_share + searches * SEARCH, searches


def plain(v):
    """'Frederiksberg (Q30096)' -> 'Frederiksberg'; a date as it is."""
    return re.sub(r" \(Q\d+\)$", "", v.strip())


def compatible(theirs, ours):
    """A less precise Wikidata date that the entry's date falls within ('1996' beside '1996-10-21'): no conflict."""
    return bool(re.fullmatch(r"\d{4}(-\d{2})?", theirs)) and ours.startswith(theirs)


def confirmed(r):
    """Counted sources support the entry's value, and the model reads them so."""
    return r.get("established") == "entry" and any(
        x.get("supports") == "entry" for x in counted(json.loads(r["evidence"] or "[]"), r["work"]))


def edits(r):
    """The edits the outcome makes on the item, in words."""
    work, fact = WORK_NAMES[r["work"]], r["property"]
    ours = plain(r["entry_value"])
    theirs = [plain(v) for v in r["wikidata_value"].split("; ")]
    conflicting = [v for v in theirs if not compatible(v, ours)] or theirs
    old = ", ".join(conflicting)
    ref = f"reference: {work}"
    d = r["decision"]
    if d == "prefer":
        return (f"Add {fact}: {ours}, preferred rank, reason {{{{Q|Q98386534}}}} ({ref}).<br />{old} stays at "
                "normal rank.<br />" + (f"Other sources confirm the entry's value, so {old} is probably wrong; "
                                        if confirmed(r) else f"No source supports {old}; ")
                + "editors decide whether it should go.")
    if d == "same":
        return f"None: {old} lies inside {ours} or the other way round, so the two values agree at different precision."
    if d == "both":
        return f"Add {fact}: {ours}, at the same rank as {old} ({ref}).<br />{old} stays as it is."
    if d == "report":
        return (f"Add {fact}: {ours}, deprecated rank, reason {{{{Q|Q29998666}}}} ({ref}).<br />{old} stays at "
                f"normal rank.<br />The error is reported to {work}.")
    return "None yet: the model gave no answer; the case is checked again."


def table(rows):
    work = WORK_NAMES[rows[0]["work"]]
    lines = ['{| class="wikitable"', f"! Person !! Fact !! Wikidata (reference) !! {work} !! Sources counted !! Outcome "
             "!! Edits on Wikidata"]
    refs = {"no source": "no reference", "imported from a Wikipedia": "only imported from a Wikipedia",
            "cites another source": "cites another source"}
    for r in rows:
        ev = json.loads(r["evidence"] or "[]")
        kept = counted(ev, r["work"])
        items = [f"* {SUPPORTS.get(x.get('supports'), esc(x.get('supports')))}: {link(x.get('url', ''))} "
                 f"<small>“{esc(x.get('says'), 160)}”</small>" for x in kept]
        dropped = len(ev) - len(kept)
        if dropped:
            items.append(f"* <small>{dropped} more not counted (Wikipedia copies, user-edited sites or the work itself)</small>")
        ref = refs.get(r["wikidata_sources"], r["wikidata_sources"])
        out = OUTCOME.get(r["decision"], r["decision"])
        if r["decision"] == "prefer":
            out += " (Wikidata's value kept)"
        url = URLS[r["work"]].format(r["entry"])
        lines += ["|-", f"| {{{{Q|{r['qid']}}}}} || {r['property']} || {value(r['wikidata_value'])} <small>({ref})</small> "
                        f"|| {value(r['entry_value'], url)} ||\n" + ("\n".join(items) or "''none found''"),
                  f"| '''{out}'''<br /><small>{esc(r['note'], 300)}</small>", f"| {edits(r)}"]
    lines.append("|}")
    return "\n".join(lines)


def build(log, title):
    rows = sorted(csv.DictReader(open(log, encoding="utf-8")), key=lambda r: r["entry"])
    for r in rows:                               # the outcome under the current rules, from the logged evidence
        r["decision"] = decide(json.loads(r["evidence"] or "[]"), r.get("established"), r["work"], r["property"])
    n = collections.Counter(r["decision"] for r in rows)
    sure = sum(r["decision"] == "prefer" and confirmed(r) for r in rows)
    dollars, searches = cost(rows)
    model = rows[0]["model"].replace("claude-", "Claude ").replace("-5-5", " 5.5").replace("sonnet", "Sonnet").replace("haiku", "Haiku")
    day = rows[0]["time"][:10]
    text = HOW + f"""
== {title} ==
{len(rows)} disagreements of this kind from {WORK_NAMES[rows[0]['work']]} \
([[Wikidata:Requests for permissions/Bot/OKA bot 2|task 2]]), checked on {day} with {model} \
({searches} searches, about ${dollars:.2f} in all). Outcome under the rules above: {n['prefer']} prefer the \
entry's value ({sure} of them with other sources confirming it), {n['both']} both, {n['report']} report.

{table(rows)}
"""
    OUT.write_text(text, encoding="utf-8")
    print(OUT, len(text), "characters;", dict(n), f"${dollars:.2f}")


BATCH_OUT = WORK / "conflict_batch.wiki"


def batch(log, number, title, sample=None):
    """One batch for the operator's check before it runs: its counts, and its cases (or a random sample of them),
    grouped by work. The full list is the log on GitHub."""
    import random
    rows = list(csv.DictReader(open(log, encoding="utf-8")))
    since = os.environ.get("BATCH_SINCE")                 # only the cases logged from then on (a log holds batches)
    rows = [r for r in rows if not since or r["time"] >= since]
    for r in rows:
        if r["model"] != "none":                 # searched cases: the outcome under the current rules
            r["decision"] = decide(json.loads(r["evidence"] or "[]"), r.get("established"), r["work"], r["property"])
        if nested_row(r):                        # one place inside the other: no disagreement, nothing edited
            r["decision"] = "same"
    n = collections.Counter(r["decision"] for r in rows)
    shown = random.Random(int(number)).sample(rows, int(sample)) if sample and int(sample) < len(rows) else rows
    searched = [r for r in rows if r["model"] != "none"]
    dollars, searches = cost(searched, float(os.environ.get("BATCH_TOKENS_SHARE", "1"))) if searched else (0.0, 0)
    repo = "https://github.com/Open-Knowledge-Association/oka-wikidata-bot/blob/main/"
    bold = "'" * 3
    text = f"""This is batch {number} of the disagreement edits of [[User:OKA bot|OKA bot]] ([[Wikidata:Requests for permissions/Bot/OKA bot 3|request 3]]). The rules are on [[User:OKA bot/Disagreement checks]]. {bold}Nothing on this page has been changed on Wikidata yet{bold}: the operator checks a sample first.

== {title} ==
{len(rows)} cases{f", {searches} searches, about ${dollars:.2f}" if searched else ""}. Outcome: {n['prefer']} prefer the entry's value, {n['both']} both, {n['report']} report, {n['same']} no disagreement after all (one place inside the other), {n['retry']} checked again later. {"" if shown is rows else f"Shown below: a random sample of {len(shown)}. "}All cases: [{repo}{Path(log).name} {Path(log).name}].
"""
    for work in sorted({r["work"] for r in shown}):
        text += f"\n=== {WORK_NAMES[work]} ===\n" + table(sorted([r for r in shown if r["work"] == work],
                                                                   key=lambda r: r["entry"])) + "\n"
    BATCH_OUT.write_text(text, encoding="utf-8")
    print(BATCH_OUT, len(text), "characters;", dict(n), f"${dollars:.2f}")


def save_batch(number):
    from common import bot_site
    site = bot_site(5)
    import pywikibot
    p = pywikibot.Page(site, f"{PAGE}/Batch {number}")
    p.text = BATCH_OUT.read_text(encoding="utf-8")
    p.save(summary=f"Disagreement batch {number}, for the operator's sample check (nothing edited on items yet)", bot=True)


def save():
    from common import bot_site
    site = bot_site(5)                           # writes the login settings; pywikibot may only be imported after this
    import pywikibot
    p = pywikibot.Page(site, PAGE)
    p.text = OUT.read_text(encoding="utf-8")
    p.save(summary="New rule: the bot never removes or deprecates others' values; the entry's value gets preferred rank "
                   "and the case is listed here (nothing edited)", bot=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "batch":
        batch(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else None)
    elif sys.argv[1] == "save-batch":
        save_batch(sys.argv[2])
    elif sys.argv[1] == "build":
        build(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "Pilot: 20 cases")
    elif sys.argv[1] == "save":
        save()
