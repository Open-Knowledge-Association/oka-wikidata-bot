"""The page User:OKA bot/Disagreement checks: how the bot settles a disagreement with an unsourced Wikidata value, and
each checked case with the sources found and the outcome. Nothing is edited on Wikidata here.

  python conflict_page.py build LOG [TITLE]   write work/conflict_page.wiki from a conflict_check log
  python conflict_page.py save                save it as User:OKA bot/Disagreement checks (OKA bot)
"""
import collections
import csv
import json
import re
import sys
import urllib.parse

from common import WORK
from conflict_check import URLS, WORK_NAMES, counted, decide

PAGE = "User:OKA bot/Disagreement checks"
OUT = WORK / "conflict_page.wiki"
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}    # $ per million tokens in, out
SEARCH = 0.01                                                                    # $ per web search
SUPPORTS = {"wikidata": "Wikidata's value", "entry": "the entry's value", "both": "fits both", "other": "another value"}
OUTCOME = {"replace": "replace", "prefer": "prefer the entry's value", "both": "both", "report": "report",
           "person": "a person checks"}

HOW = """This page shows how [[User:OKA bot|OKA bot]] settles a disagreement between a reference work and a Wikidata \
value that has no source, as described in [[Wikidata:Requests for permissions/Bot/OKA bot 3|request 3]]. \
'''Nothing on this page has been changed on Wikidata.''' Comments are welcome on the request or on the talk page.

== How it works ==
# '''Which cases.''' A date or place of birth or death where the entry gives another value and Wikidata's value has \
no reference, or only {{P|143}}. Differences that are not disagreements are settled before this step: one place \
inside the other, the same day in the other calendar, a year beside the full date. Where Wikidata's value has any \
other reference, nothing is searched and a person checks the case.
# '''The search.''' A language model (Claude, with a web search tool, at most 5 searches per case) gets the person, \
the fact, both values and the entry's link. It lists each page it read that states the fact, with the link, the \
page's words and the value they support, and then says what these sources establish: Wikidata's value, the entry's \
value, a genuine split, or nothing clear. Wikipedia, Wikidata and their copies, the work itself, and sites anyone can \
edit (family trees, Find a Grave, forums) do not count. The prompt and the rules below are in \
[https://github.com/Open-Knowledge-Association/oka-wikidata-bot/blob/main/conflict_check.py conflict_check.py].
# '''The decision''' is made by code, from the sources counted and the model's reading of them:
#* '''replace''': no counted source supports Wikidata's value, and either other sources confirm the entry's (and the \
model reads them so), or Wikidata's value has no reference at all and no Wikipedia article about the person states \
it. Wikidata's value is taken to be wrong: the entry's value is added with its reference, and Wikidata's value is \
removed if it has no reference, or deprecated ({{P|2241}}: {{Q|Q41755623}}) if its only reference is {{P|143}}, so \
that it stays visible and is not imported again.
#* '''prefer the entry's value''': no counted source supports Wikidata's value, none confirms the entry's either \
(often nothing is found at all), and Wikidata's value is a Wikipedia import. The entry is a source; a Wikipedia \
import is not. Wikidata's value stays as it is, and the entry's value is added with preferred rank ({{P|7452}}: \
{{Q|Q98386534}}).
#* '''both''': counted sources support each value (one each is enough), and the model does not read them as \
establishing Wikidata's; or nothing supports Wikidata's value while sources give yet other values, so the fact is \
unclear. The entry's value is added beside Wikidata's, at the same rank, with the entry as its reference: the two \
values side by side show that the fact is disputed.
#* '''report''': counted sources support Wikidata's value and none supports the entry's (other values found do not \
matter), or they establish Wikidata's value. The entry's value is added with deprecated rank ({{P|2241}}: \
{{Q|Q29998666}}), referenced to the entry, so that the item shows that the work states otherwise and the value is \
not added again; the error is also reported to the work.
#* '''a person checks''': a place name fits both values, or the model's reading contradicts the sources.
#: The model's reading can stop an edit but cannot cause one. A value is only removed or deprecated when the counted \
sources show no support for it.
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


def cost(rows):
    tin = sum(int(r["input_tokens"] or 0) for r in rows)
    tout = sum(int(r["output_tokens"] or 0) for r in rows)
    searches = sum(int(r["searches"] or 0) for r in rows)
    pin, pout = PRICES.get(rows[0]["model"], (0, 0))
    return tin * pin / 1e6 + tout * pout / 1e6 + searches * SEARCH, searches


def plain(v):
    """'Frederiksberg (Q30096)' -> 'Frederiksberg'; a date as it is."""
    return re.sub(r" \(Q\d+\)$", "", v.strip())


def compatible(theirs, ours):
    """A less precise Wikidata date that the entry's date falls within ('1996' beside '1996-10-21'): no conflict."""
    return bool(re.fullmatch(r"\d{4}(-\d{2})?", theirs)) and ours.startswith(theirs)


def edits(r):
    """The edits the outcome makes on the item, in words."""
    work, fact = WORK_NAMES[r["work"]], r["property"]
    ours = plain(r["entry_value"])
    theirs = [plain(v) for v in r["wikidata_value"].split("; ")]
    conflicting = [v for v in theirs if not compatible(v, ours)] or theirs
    old = ", ".join(conflicting)
    ref = f"reference: {work}"
    d = r["decision"]
    if d == "replace":
        if r["wikidata_sources"] == "no source":
            confirmed = any(x.get("supports") == "entry" for x in counted(json.loads(r["evidence"] or "[]"), r["work"]))
            return (f"Add {fact}: {ours} ({ref}).<br />Remove {old} (it has no reference"
                    + (", and no source supports it)." if confirmed
                       else ", no source supports it, and no Wikipedia article states it)."))
        return (f"Add {fact}: {ours} ({ref}).<br />Deprecate {old}: reason {{{{Q|Q41755623}}}} "
                "(its only reference is the Wikipedia import).")
    if d == "prefer":
        return (f"Add {fact}: {ours}, preferred rank, reason {{{{Q|Q98386534}}}} ({ref}).<br />{old} stays at "
                "normal rank.")
    if d == "both":
        return f"Add {fact}: {ours}, normal rank ({ref}).<br />{old} stays at normal rank."
    if d == "report":
        return (f"Add {fact}: {ours}, deprecated rank, reason {{{{Q|Q29998666}}}} ({ref}).<br />{old} stays at "
                f"normal rank.<br />The error is reported to {work}.")
    return "None for now: a person checks the case."


def table(rows):
    work = WORK_NAMES[rows[0]["work"]]
    lines = ['{| class="wikitable"', f"! Person !! Fact !! Wikidata (reference) !! {work} !! Sources counted !! Outcome "
             "!! Edits on Wikidata"]
    for r in rows:
        ev = json.loads(r["evidence"] or "[]")
        kept = counted(ev, r["work"])
        items = [f"* {SUPPORTS.get(x.get('supports'), esc(x.get('supports')))}: {link(x.get('url', ''))} "
                 f"<small>“{esc(x.get('says'), 160)}”</small>" for x in kept]
        dropped = len(ev) - len(kept)
        if dropped:
            items.append(f"* <small>{dropped} more not counted (Wikipedia copies, user-edited sites or the work itself)</small>")
        ref = "no reference" if r["wikidata_sources"] == "no source" else "only imported from a Wikipedia"
        out = OUTCOME.get(r["decision"], r["decision"])
        if r["decision"] == "replace":
            out += ": Wikidata's value " + ("removed" if r["wikidata_sources"] == "no source" else "deprecated")
        elif r["decision"] == "prefer":
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
        r["decision"] = decide(json.loads(r["evidence"] or "[]"), r.get("established"), r["work"], r["property"],
                               r["wikidata_sources"])
    n = collections.Counter(r["decision"] for r in rows)
    dollars, searches = cost(rows)
    model = rows[0]["model"].replace("claude-", "Claude ").replace("-5-5", " 5.5").replace("sonnet", "Sonnet").replace("haiku", "Haiku")
    day = rows[0]["time"][:10]
    text = HOW + f"""
== {title} ==
{len(rows)} disagreements of this kind from {WORK_NAMES[rows[0]['work']]} \
([[Wikidata:Requests for permissions/Bot/OKA bot 2|task 2]]), checked on {day} with {model} \
({searches} searches, about ${dollars:.2f} in all). Outcome under the rules above: {n['replace']} replace, \
{n['prefer']} prefer the entry's value, {n['both']} both, {n['report']} report, {n['person']} for a person to check.

{table(rows)}
"""
    OUT.write_text(text, encoding="utf-8")
    print(OUT, len(text), "characters;", dict(n), f"${dollars:.2f}")


def save():
    from common import bot_site
    site = bot_site(5)                           # writes the login settings; pywikibot may only be imported after this
    import pywikibot
    p = pywikibot.Page(site, PAGE)
    p.text = OUT.read_text(encoding="utf-8")
    p.save(summary="How disagreements with unsourced values are settled, with a pilot of 20 cases (nothing edited)", bot=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "build":
        build(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "Pilot: 20 cases")
    elif sys.argv[1] == "save":
        save()
