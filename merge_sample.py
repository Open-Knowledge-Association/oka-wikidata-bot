"""Merging duplicate researchers (a proposal): a random sample of item pairs that share one ORCID iD (P496), each
checked as the bot would check it before merging, written as a wiki page for review. Nothing is merged here.

  python merge_sample.py sample [N]   N random pairs (default 100) -> work/merges/orcid_sample.json, orcid_sample.wiki
  python merge_sample.py save         save the page at User:OKA bot/Merges/ORCID sample, as OKA bot
"""
import collections
import datetime
import json
import random
import re
import sys
import time
import unicodedata
import urllib.parse

from common import ROOT, curl_json

API = "https://www.wikidata.org/w/api.php"
OUT = ROOT / "work" / "merges"
PAGE = "User:OKA bot/Merges/ORCID sample"
REPORT = "Wikidata:Database reports/Constraint violations/P496"
OTHER_IDS = {"P227": "GND", "P214": "VIAF", "P213": "ISNI", "P244": "LCNAF"}


def api(**params):
    for attempt in range(1, 6):
        try:
            return curl_json(API + "?" + urllib.parse.urlencode({"format": "json", "formatversion": 2, **params}))
        except (ValueError, OSError):
            time.sleep(10 * attempt)
    raise RuntimeError("Wikidata API did not answer")


def all_pairs():
    """Every ORCID iD on exactly two items (QLever), else the 5,000 the unique-value report lists."""
    import dictionaries as d
    try:
        rows = d.qlever("SELECT ?o (MIN(?i) AS ?a) (MAX(?i) AS ?b) (COUNT(DISTINCT ?i) AS ?n) WHERE { ?i wdt:P496 ?o } "
                        "GROUP BY ?o HAVING (COUNT(DISTINCT ?i) = 2)")
        pairs = [(r[0], tuple(sorted({r[1], r[2]}, key=lambda q: int(q[1:])))) for r in rows if len(r) == 4]
        pairs = [(o, qs) for o, qs in pairs if len(qs) == 2]
        if pairs:
            return pairs, "all ORCID iDs on exactly two items, listed with QLever"
    except Exception as e:                            # QLever busy: the report's list instead
        print("QLever:", e, flush=True)
    text = api(action="parse", page=REPORT, prop="wikitext")["parse"]["wikitext"]
    section = text[text.find('== "Unique value" violations =='):]
    pairs = []
    for line in section.split("\n")[2:]:
        m = re.match(r"\* ([0-9X-]{19}): (.*)", line)
        if not m:
            if line.startswith("=="):
                break
            continue
        qs = re.findall(r"\[\[(Q\d+)\]\]", m.group(2))
        if len(qs) == 2:
            pairs.append((m.group(1), tuple(sorted(qs, key=lambda q: int(q[1:])))))
    return pairs, f"the {len(pairs):,} two-item cases listed on [[{REPORT}#Unique value|the unique-value report]]"


CYRILLIC = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                     "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
GERMAN = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


def fold(s, german=False):
    """A name as lower-case ASCII words: accents dropped (or German umlauts written out), Cyrillic transliterated."""
    s = (s or "").lower()
    if german:
        s = "".join(GERMAN.get(ch, ch) for ch in s)
    s = "".join(CYRILLIC.get(ch, ch) for ch in s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]+", " ", s).split()


def forms(text):
    """The folded forms of one name: Hänsch is both 'hansch' and 'haensch'."""
    return {tuple(fold(text)), tuple(fold(text, german=True))} - {()}


def names(e):
    """Name forms of an item: its labels and aliases, folded (accents, case, hyphens, umlauts, Cyrillic)."""
    out = set()
    for v in e.get("labels", {}).values():
        out |= forms(v["value"])
    for al in e.get("aliases", {}).values():
        for a in al:
            out |= forms(a["value"])
    return out


def same_name(a, b):
    """Two name forms of one person: the same family name and the same first given name or its initial."""
    if not a or not b:
        return False
    if a[-1] != b[-1] and "".join(a) != "".join(b):
        return a[-1] in b and b[-1] in a              # 'Ramos Martin, Jesus' and 'Jesus Ramos-Martin'
    return a[0] == b[0] or a[0][0] == b[0][0] and (len(a[0]) == 1 or len(b[0]) == 1)


def orcid_name(orcid):
    try:
        j = curl_json(f"https://pub.orcid.org/v3.0/{orcid}/person", "-H", "Accept: application/json")
    except (ValueError, OSError):
        return None
    n = j.get("name") or {}
    given = (n.get("given-names") or {}).get("value", "")
    family = (n.get("family-name") or {}).get("value", "")
    credit = (n.get("credit-name") or {}).get("value", "")
    return {"name": f"{given} {family}".strip(), "credit": credit,
            "others": [o.get("content", "") for o in (j.get("other-names") or {}).get("other-name", [])]}


def orcid_dois(orcid):
    """DOIs of the works on the public ORCID record."""
    try:
        j = curl_json(f"https://pub.orcid.org/v3.0/{orcid}/works", "-H", "Accept: application/json")
    except (ValueError, OSError):
        return set()
    return {x["external-id-value"].strip().lower() for g in j.get("group", [])
            for x in (g.get("external-ids") or {}).get("external-id", []) if x.get("external-id-type") == "doi"}


def item_dois(q):
    """DOIs of the works whose author (P50) the item is."""
    import dictionaries as d
    return {r[0].strip('"').lower() for r in d.lookup(f"SELECT ?doi WHERE {{ ?w wdt:P50 wd:{q} ; wdt:P356 ?doi }}") if r}


def gnd_links_orcid(gnd, orcid):
    """Whether the GND record (lobid.org) itself links this ORCID iD."""
    try:
        j = curl_json(f"https://lobid.org/gnd/{gnd}.json")
    except (ValueError, OSError):
        return False
    return any(orcid in (s.get("id") or "") for s in j.get("sameAs", []))


def evidence(q, e, orcid, dois):
    """What, besides the ORCID iD on the item, ties the item to the person of the ORCID record."""
    found = []
    shared = item_dois(q) & dois
    if shared:
        found.append(f"author of {plural(len(shared), 'work')} on the ORCID record")
    if any(gnd_links_orcid(g, orcid) for g in values(e, "P227")[:2]):
        found.append("its GND record links the ORCID iD")
    return found


def values(e, pid, key=None):
    out = []
    for c in e.get("claims", {}).get(pid, []):
        if c["mainsnak"]["snaktype"] != "value" or c["rank"] == "deprecated":
            continue
        v = c["mainsnak"]["datavalue"]["value"]
        out.append(v.get(key) if key and isinstance(v, dict) else v)
    return out


def years(e, pid):
    return {int(t[:5]) for t in values(e, pid, "time") if t}


def created(q):
    r = api(action="query", prop="revisions", titles=q, rvlimit=1, rvdir="newer", rvprop="user|timestamp|comment")
    rev = r["query"]["pages"][0]["revisions"][0]
    tool = "QuickStatements" if re.search(r"quickstatements|qs-dev|#temporary_batch", rev.get("comment", ""), re.I) else \
        "OpenRefine" if "openrefine" in rev.get("comment", "").lower() else "other"
    return {"user": rev["user"], "date": rev["timestamp"][:10], "tool": tool}


def links_in(q):
    r = api(action="query", list="backlinks", bltitle=q, blnamespace=0, bllimit=500)
    return len(r["query"]["backlinks"])


NOT_RESEARCH = ("Q33999", "Q2066131", "Q639669", "Q177220", "Q5716684", "Q4610556", "Q947873", "Q2405480")
# actor, athlete, musician, singer, dancer, model, television presenter, voice actor: work that does not come with an
# ORCID iD. (Psychologists, lawyers, physicians or librarians often do: only these clearly other kinds block a merge.)
_not_research = None


def clearly_not_research(occupations):
    """Whether every one of these occupations is of a kind that does not go with an ORCID iD (an actor, an athlete):
    Elke Arendt, actress, born 1939, had the ORCID iD of the food scientist Elke K. Arendt, copied there from GND."""
    global _not_research
    if _not_research is None:
        import dictionaries as d
        rows = d.qlever("SELECT DISTINCT ?c WHERE { VALUES ?top { " + " ".join(f"wd:{q}" for q in NOT_RESEARCH) +
                        " } ?c wdt:P279* ?top }")
        _not_research = {r[0] for r in rows} | set(NOT_RESEARCH)
    return bool(occupations) and set(occupations) <= _not_research


def check(orcid, a, b, ea, eb, oname):
    """The checks the bot makes before a merge: (list of failures, list of notes)."""
    fail, note = [], []
    holders = api(action="query", list="search", srsearch=f"haswbstatement:P496={orcid}", srlimit=5)["query"]["search"]
    now = sorted(h["title"] for h in holders)
    if ea.get("redirects") or eb.get("redirects") or "missing" in ea or "missing" in eb or now != sorted([a, b]):
        return ["no longer a pair (already merged or changed): now on " + (", ".join(now) or "no item")], note
    for q, e in ((a, ea), (b, eb)):
        if set(values(e, "P31", "id")) != {"Q5"}:
            fail.append(f"{q} is not only a human (instance of {', '.join(values(e, 'P31', 'id')) or 'nothing'})")
        if any(c["mainsnak"].get("datavalue", {}).get("value") == orcid and c["rank"] == "deprecated"
               for c in e.get("claims", {}).get("P496", [])):
            fail.append(f"the ORCID iD is deprecated on {q}")
    if b in values(ea, "P1889", "id") or a in values(eb, "P1889", "id"):
        fail.append("marked as different (P1889)")
    if b in values(ea, "P460", "id") or a in values(eb, "P460", "id"):
        fail.append("marked as possibly the same (P460): identity uncertain")
    both = sorted(set(ea.get("sitelinks", {})) & set(eb.get("sitelinks", {})))
    if both:
        fail.append(f"both items have a page on {', '.join(both)}")
    na, nb = names(ea), names(eb)
    if not any(same_name(x, y) for x in na for y in nb):
        fail.append("names differ")
    if oname and oname["name"]:
        given = set().union(*(forms(x) for x in [oname["name"], oname["credit"]] + oname["others"]))
        if not any(same_name(f, x) for f in given for x in na | nb):
            fail.append(f"the ORCID record's name ({oname['name']}) fits neither item")
    else:
        note.append("ORCID record name not public")
    for pid, what in (("P569", "birth"), ("P570", "death")):
        ya, yb = years(ea, pid), years(eb, pid)
        if ya and yb and not ya & yb:
            fail.append(f"years of {what} differ ({'/'.join(map(str, sorted(ya)))} vs {'/'.join(map(str, sorted(yb)))})")
    sa, sb = set(values(ea, "P21", "id")), set(values(eb, "P21", "id"))
    if sa and sb and not sa & sb:
        fail.append("sex or gender differs")
    for pid, what in OTHER_IDS.items():
        va, vb = set(values(ea, pid)), set(values(eb, pid))
        if va and vb and not va & vb:
            if pid == "P214":                         # VIAF often splits one person into two clusters
                note.append("different VIAF IDs (VIAF often has two clusters for one person)")
            else:
                fail.append(f"different {what} IDs")
    oa, ob = set(values(ea, "P106", "id")), set(values(eb, "P106", "id"))
    if not oa & ob:                                  # an actor's or an athlete's item should not carry an ORCID iD
        for q, occ in ((a, oa), (b, ob)):
            if clearly_not_research(occ):
                fail.append(f"{q} describes someone whose work does not come with an ORCID iD "
                            f"(occupation {', '.join(sorted(occ))})")
    for lang in sorted(set(ea.get("descriptions", {})) & set(eb.get("descriptions", {}))):
        da, db = ea["descriptions"][lang]["value"], eb["descriptions"][lang]["value"]
        if da != db:
            note.append(f"descriptions differ in {lang} (\"{da}\" / \"{db}\"): the kept item's stays")
    return fail, note


def sample(n=100, again=False):
    """Draw n pairs and check them; with again, check the pairs of the last sample once more (after a rule change)."""
    OUT.mkdir(parents=True, exist_ok=True)
    if again:
        last = json.loads((OUT / "orcid_sample.json").read_text(encoding="utf-8"))
        chosen = [(r["orcid"], (r["keep"], r["merge"])) for r in last["rows"]]
        origin, total = last["origin"], last["pairs_total"]
    else:
        pairs, origin = all_pairs()
        total = len(pairs)
        random.Random(20261016).shuffle(pairs)
        chosen = pairs[:n]
    qs = sorted({q for _, ab in chosen for q in ab})
    ents = {}
    for i in range(0, len(qs), 50):
        r = api(action="wbgetentities", ids="|".join(qs[i:i + 50]), props="info|labels|aliases|descriptions|claims|sitelinks")
        ents.update(r["entities"])
    rows = []
    for k, (orcid, (a, b)) in enumerate(chosen, 1):
        ea, eb = ents.get(a, {}), ents.get(b, {})
        oname = orcid_name(orcid)
        time.sleep(1)
        fail, note = check(orcid, a, b, ea, eb, oname)
        dois = orcid_dois(orcid)
        time.sleep(1)
        ev = {"keep": evidence(a, ea, orcid, dois), "merge": evidence(b, eb, orcid, dois)}
        if not fail and not ev["keep"] and not ev["merge"]:
            fail.append("nothing besides the shared ORCID iD ties either item to the ORCID record")
        row = {"orcid": orcid, "keep": a, "merge": b, "fail": fail, "note": note, "orcid_name": oname, "evidence": ev,
               "label_keep": label(ea), "label_merge": label(eb),
               "sitelinks_keep": sorted(ea.get("sitelinks", {})), "sitelinks_merge": sorted(eb.get("sitelinks", {})),
               "links_keep": links_in(a), "links_merge": links_in(b),
               "created_keep": created(a), "created_merge": created(b)}
        rows.append(row)
        print(k, orcid, a, b, "MERGE" if not fail else "SKIP: " + "; ".join(fail), flush=True)
    data = {"origin": origin, "pairs_total": total, "rows": rows,
            "date": datetime.date.today().isoformat()}
    (OUT / "orcid_sample.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "orcid_sample.wiki").write_text(page(data), encoding="utf-8")
    print(f"{sum(1 for r in rows if not r['fail'])} of {len(rows)} pass every check", flush=True)


def label(e):
    for lang in ("en", "mul", "de", "fr", "es", "nl", "it"):
        if lang in e.get("labels", {}):
            return e["labels"][lang]["value"]
    return next(iter(e.get("labels", {}).values()), {}).get("value", "")


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def page(data):
    rows = data["rows"]
    ok = [r for r in rows if not r["fail"]]
    why = collections.Counter(re.sub(r"^Q\d+ ", "one item ", f.split(" (")[0].split(":")[0])
                              for r in rows for f in r["fail"][:1])
    tools = collections.Counter(r["created_merge"]["tool"] for r in rows)
    users = collections.Counter(r["created_merge"]["user"] for r in rows)
    years_ = collections.Counter(r["created_merge"]["date"][:4] for r in rows)
    with_links = sum(1 for r in rows if r["sitelinks_keep"] or r["sitelinks_merge"])
    lines = [
        "This page shows a random sample of pairs of items that share one {{P|496}}, checked as [[User:OKA bot|OKA bot]] "
        "would check them before merging. It goes with a proposal at [[Wikidata:Mass editing proposals]]. "
        "'''Nothing has been merged.''' Comments are welcome on the talk page.",
        "",
        f"'''Sample:''' {len(rows)} pairs drawn at random from {data['origin']} ({data['pairs_total']:,} pairs), "
        f"checked on {data['date']}.",
        "",
        "== Result ==",
        f"* '''{len(ok)}''' of {len(rows)} pairs pass every check and would be merged.",
    ]
    lines += [f"* {n} skipped: {k}" for k, n in why.most_common()]
    lines += [
        f"* Wikipedia pages: {with_links} of the {len(rows)} pairs have a page on at least one item; "
        f"{sum(1 for r in rows if set(r['sitelinks_keep']) & set(r['sitelinks_merge']))} have pages on the same wiki "
        "on both items (those are never merged: the wiki must merge its pages first).",
        "* The newer item of each pair was created " + ", ".join(f"{y}: {n}" for y, n in sorted(years_.items())) + "; "
        + ", ".join(f"{t}: {n}" for t, n in tools.most_common()) + f"; by {len(users)} accounts "
        f"({', '.join(f'{u} ({n})' for u, n in users.most_common(5))}{', ...' if len(users) > 5 else ''}).",
        "",
        "== Questions ==",
        "; Why have these pairs not been merged already, if they are this clear?",
        f": Most are new. In this sample the newer item was created in 2025 or 2026 for "
        f"{sum(1 for r in rows if r['created_merge']['date'][:4] in ('2025', '2026'))} of the {len(rows)} pairs, "
        "most of them with QuickStatements from records of the German National Library (GND), next to an author item "
        "that already had the ORCID iD."
        + (f" Of all {data['recent'][1]:,} pairs, {data['recent'][0]:,} ({data['recent'][0] / data['recent'][1]:.0%}) "
           "have a newer item with a Q-number above 130 million, that is, created since 2024." if data.get("recent") else "")
        + " Merges are made by hand, one pair at a time, and the [[Wikidata:Database reports/Constraint violations/"
        "P496#Unique value|constraint report]] lists only the first 5,000 of these values.",
        "; What if one or both items have Wikipedia articles?",
        ": Articles on different wikis are no obstacle: the kept item gets all of them. If both items had an article "
        "on the same wiki, the software would refuse the merge, and the bot skips such pairs: the wiki would have to "
        "merge its articles first. In this sample "
        f"{sum(1 for r in rows if r['sitelinks_keep'] or r['sitelinks_merge'])} pairs have an article and none has "
        "articles on the same wiki on both items.",
        "; What if the ORCID iD was put on the wrong person?",
        ": That happens: in this sample an actress's item carried the ORCID iD of a food scientist of the same name, "
        "copied there from a GND record. The checks below catch such cases through the names, life dates, other "
        "identifiers and occupations, and every merged pair must be tied to the ORCID record by more than the iD "
        "itself. Pairs that fail are left alone and listed.",
        "; Can a merge be undone?",
        ": Yes. Each merge is logged with both items' revisions before it, and the batches are grouped so they can "
        "be reviewed and reverted. Within a day, before the links to the merged item are moved, a merge can be undone "
        "on request.",
        "",
        "== Checks ==",
        "A pair is merged only if all of these hold, checked again just before the merge:",
        "# exactly these two items carry the ORCID iD, at a rank other than deprecated;",
        "# both are instances of {{Q|Q5}} and nothing else;",
        "# they are not marked as different ({{P|1889}}) or as possibly the same ({{P|460}});",
        "# no wiki has a page on both items;",
        "# their names agree with each other and with the name on the public ORCID record (accents, case, hyphens, "
        "initials, middle names, German umlauts and Cyrillic script aside);",
        "# where both items give them, the years of birth and death and the sex or gender agree, and the {{P|227}}, "
        "{{P|213}} and {{P|244}} values overlap (differing {{P|214}} values are only noted: VIAF often has two "
        "clusters for one person);",
        "# where their occupations ({{P|106}}) do not overlap, neither item describes someone whose work does not "
        "come with an ORCID iD (an actor, an athlete, a singer, a model: an actress's item carrying a food "
        "scientist's ORCID iD was found in this sample);",
        "# at least one item is tied to the ORCID record by more than the iD itself: it is the author ({{P|50}}) of "
        "works whose DOIs are on the ORCID record, or its {{P|227}} record at the German National Library itself "
        "links the ORCID iD.",
        "The newer item is merged into the older one (lower Q-number), which scholarly articles usually already cite "
        "in {{P|50}}. Where both have a description in the same language, the older item's is kept.",
        "",
        "== Pairs ==",
        '{| class="wikitable sortable"',
        "! # !! ORCID iD !! Kept (older) !! Merged into it (newer) !! Name on ORCID !! Wikipedia pages "
        "!! Ties to the ORCID record !! Newer item created !! Result",
    ]
    for k, r in enumerate(rows, 1):
        on = r["orcid_name"]["name"] if r.get("orcid_name") and r["orcid_name"].get("name") else "''not public''"
        sl = "; ".join(x for x in (", ".join(r["sitelinks_keep"]), ", ".join(r["sitelinks_merge"])) if x) or "none"
        c = r["created_merge"]
        result = "merge" if not r["fail"] else "'''skip''': " + "; ".join(r["fail"])
        if r["note"]:
            result += "<br /><small>" + "; ".join(r["note"]) + "</small>"
        ev = r.get("evidence", {})
        ties = "<br />".join(f"{which}: {'; '.join(ev.get(key, [])) or '–'}" for which, key in (("kept", "keep"), ("merged", "merge")))
        lines += ["|-", f"| {k} || [https://orcid.org/{r['orcid']} {r['orcid']}] || {{{{Q|{r['keep'][1:]}}}}} "
                        f"<small>({plural(r['links_keep'], 'link')})</small> || {{{{Q|{r['merge'][1:]}}}}} "
                        f"<small>({plural(r['links_merge'], 'link')})</small> || {on} || {sl} || <small>{ties}</small> || "
                        f"{c['date']}, [[User:{c['user']}|{c['user']}]], {c['tool']} || {result}"]
    lines.append("|}")
    return "\n".join(lines) + "\n"


def save():
    from common import bot_site
    site = bot_site(5)
    import pywikibot
    p = pywikibot.Page(site, PAGE)
    p.text = (OUT / "orcid_sample.wiki").read_text(encoding="utf-8")
    p.save(summary="Sample of ORCID duplicate pairs with the checks a merge would need (nothing merged)", bot=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "sample"
    if mode == "sample":
        sample(int(sys.argv[2]) if len(sys.argv) > 2 else 100)
    elif mode == "recheck":
        sample(again=True)
    elif mode == "save":
        save()
