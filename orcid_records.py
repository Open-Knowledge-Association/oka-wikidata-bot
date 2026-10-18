"""Second pass over the ORCID wrong-iD list (jz, 18 October 2026): each flagged iD is checked against its public ORCID
record, read-only. The Wikidata rules of orcid_wrong.py are no proof (a violinist may also be a researcher), so an
iD counts as "confirmed wrong" only when the record itself points away from the flagged person:

- it shows work after the person's death, or before their 15th birthday (only a date the GND record gives too
  counts: a reference alone is not enough, see firm());
- it is in the name of someone else (or, when no DOI ties it to the item, with another middle initial);
- it shows nothing dated (empty, private, or undated), and the person died before October 2012 by a date the GND
  record gives too;
- or a person compared it by hand with the item and found someone in another field or place (orcid_hand_checks.tsv).
Everything else is "check first", however clear the Wikidata rules looked. A record whose DOIs are works of the
flagged item itself goes to "check first" too: the iD may be this person's after all.

DOIs that are works (P50) of the other item with the iD never confirm on their own (18 October 2026): the tools that
link papers to authors use whichever item holds the iD, so the papers follow a wrong iD (Jens Timmermann: the
chemist's papers sat on the philosopher's item). The 46 rows where they were the only sign were checked by hand, and
so were the confirmed rows whose dates the GND record does not give (75 in all). orcid_hand_checks.tsv holds the verdicts: wrong,
unclear, same person (merge, the iD stays), right here (the iD is wrong on the other item instead) or wrong date
(the iD is right, the item's dates are not).

  python orcid_records.py      fetch the records (cached in work/merges/cache/orcid/, about one a second), compare,
                               add the evidence to work/merges/orcid_wrong.json and write work/merges/orcid_wrong.wiki

From a record only the name (given, family, credit and other names), the employments and educations (organisation
and years) and the works (DOIs, title, year) are kept; nothing else (no e-mail, no biography). Run it after
`python orcid_wrong.py scan`, which rewrites both files without this pass; it replaces orcid_check_page.py.
"""
import collections
import csv
import json
import random
import re
import sys
import time
from pathlib import Path

import dictionaries as d
import merge_sample as ms
import orcid_check_page as ocp
import orcid_wrong as w
from common import curl_json

ORCID_API = "https://pub.orcid.org/v3.0/{}/record"
RECORDS = w.CACHE / "orcid"
SAFE_DOI = re.compile(r'10\.[^"\\\s<>{}|^`]+')
RETRY = {429, 500, 502, 503, 504}

CONFIRM = {                                       # reason: heading of its group on the page, in this order
    "work after death": "The record shows work after this person's death",
    "work before birth": "The record shows work from before this person could have done it",
    "another name": "The record is in someone else's name",
    "another middle initial": "The record has another middle initial",
    "nothing dated, died before 2012": "The record shows nothing dated, and this person died before ORCID existed",
    "checked by hand": "The record is of someone in another field or place (checked by hand)",
}
PAPERS = {"papers of the other item", "papers of a third item"}     # signs only, never proof (see the docstring)
HAND = Path(__file__).resolve().parent / "orcid_hand_checks.tsv"
HAND_GROUP = {"wrong": ("confirmed wrong", "checked by hand"), "unclear": ("check first", "unclear by hand"),
              "same person": ("not wrong", "same person"), "right here": ("not wrong", "right here"),
              "wrong date": ("not wrong", "wrong date")}
NOT_WRONG = {"same person": ("The two items are one person", "The iD is right. The two items should be merged."),
             "right here": ("The iD belongs here, not on the other item",
                            "The iD is right here; it should be removed from the other item instead."),
             "wrong date": ("The item's dates are wrong, not the iD",
                            "The iD is right; the item's dates should be corrected instead.")}
UNDECIDED = {                                     # first Wikidata rule: (heading, introduction, row function of ocp)
    "c": ("Is this person also a researcher?", "Wikidata gives only an occupation that does not come with an ORCID "
          "iD, and the ORCID record neither confirms nor rules out this person.", ocp.researcher_row),
    "a": ("Is the date of death right?", "Wikidata says the person died before ORCID existed, and the ORCID record "
          "does not settle it.", ocp.death_row),
    "b": ("Is the date of birth right?", "Wikidata says the person was born before 1900, and the ORCID record does "
          "not settle it.", ocp.birth_row),
    "d": ("Which of the two people has this iD?", "Two items with the same iD are marked as different people, and the "
          "ORCID record does not tell which one it is.", ocp.different_row),
}


# ---------------------------------------------------------------- the records

def year(date):
    try:
        return int(((date or {}).get("year") or {}).get("value"))
    except (TypeError, ValueError):
        return None


def trim(orcid, j):
    """The kept parts of a record: name, employments, educations (organisation, years), works (DOIs, title, year)."""
    if "response-code" in j or "error-code" in j:          # deprecated, deactivated, locked or unknown
        msg = str(j.get("developer-message") or j.get("user-message") or "")
        primary = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", msg.replace(orcid, ""))
        return {"orcid": orcid, "status": "error", "fetched": w.today(),
                "error": " ".join(str(x) for x in (j.get("response-code"), j.get("error-code")) if x),
                "primary": primary.group() if primary else None}
    person, acts = j.get("person") or {}, j.get("activities-summary") or {}
    name = person.get("name") or {}

    def value(x):
        return x.get("value") if isinstance(x, dict) else None

    def affiliations(key, kind):
        found = []
        for g in (acts.get(key) or {}).get("affiliation-group") or []:
            for s in g.get("summaries") or []:
                a = s.get(kind) or {}
                found.append({"org": ((a.get("organization") or {}).get("name") or "").strip(),
                              "start": year(a.get("start-date")), "end": year(a.get("end-date"))})
        return found
    works = []
    for g in (acts.get("works") or {}).get("group") or []:
        dois = set()
        for x in (g.get("external-ids") or {}).get("external-id") or []:
            if x.get("external-id-type") == "doi":
                m = re.search(r"10\.\S+", (x.get("external-id-normalized") or {}).get("value")
                              or x.get("external-id-value") or "")
                if m:
                    dois.add(m.group().lower())
        s = (g.get("work-summary") or [{}])[0]
        works.append({"title": ((((s.get("title") or {}).get("title") or {}).get("value")) or "")[:300],
                      "year": year(s.get("publication-date")), "dois": sorted(dois)})
    return {"orcid": orcid, "status": "ok", "fetched": w.today(),
            "name": {"given": value(name.get("given-names")), "family": value(name.get("family-name")),
                     "credit": value(name.get("credit-name")),
                     "others": [o.get("content") for o in (person.get("other-names") or {}).get("other-name") or []
                                if o.get("content")]},
            "employments": affiliations("employments", "employment-summary"),
            "educations": affiliations("educations", "education-summary"),
            "works": works}


def record(orcid):
    """The kept parts of the public record, from the cache or the ORCID public API (one request a second)."""
    path = RECORDS / f"{orcid}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    err = ""
    for attempt in range(1, 5):
        d.wait_turn("pub.orcid.org", 1.0)
        try:
            j = curl_json(ORCID_API.format(orcid), "-H", "Accept: application/json")
        except Exception as e:                        # noqa: BLE001 - network or parse error: wait, then again
            err = str(e)[:200]
        else:
            if j.get("response-code") not in RETRY:
                kept = trim(orcid, j)
                RECORDS.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
                return kept
            err = f"HTTP {j.get('response-code')}"
        time.sleep(10 * attempt)
    return {"orcid": orcid, "status": "error", "error": err, "primary": None}    # not cached: asked again next run


def norm_title(t):
    return " ".join(re.findall(r"\w+", (t or "").casefold()))


def item_titles(qs):
    """{item: normalised titles (P1476) of the works with it as author (P50)}, from QLever; titles shorter than 15
    characters ('Preface', 'Editorial') are left out."""
    found = collections.defaultdict(set)
    qs = sorted(set(qs))
    for i in range(0, len(qs), 200):
        vals = " ".join("wd:" + q for q in qs[i:i + 200])
        for r in d.qlever(f"SELECT ?a ?t WHERE {{ VALUES ?a {{ {vals} }} ?w wdt:P50 ?a ; wdt:P1476 ?t }}"):
            if len(r) == 2 and len(norm_title(r[1])) >= 15:
                found[r[0]].add(norm_title(r[1]))
    return found


def norm_org(s):
    return " ".join(re.findall(r"\w+", (s or "").casefold()))


def same_org(record_org, names):
    """Whether an organisation of the record is this Wikidata organisation: equal to one of its labels or aliases (any
    language), or its English label (two words or more) inside the record's longer name ('University of Florida
    College of Medicine'). A label in another language is not used that way: some are bare umbrella names
    ('University of California' for Berkeley)."""
    a = norm_org(record_org)
    if not a:
        return False
    return a in names["all"] or any(len(n.split()) >= 2 and len(n) >= 10 and f" {n} " in f" {a} " for n in names["en"])


def org_names(qs):
    """{item: {'all': labels and aliases in all languages, 'en': English and multilingual labels}}, normalised: the
    employers and schools of the flagged items (cached)."""
    path = w.CACHE / "org_names.json"
    have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = sorted(set(qs) - set(have), key=lambda q: int(q[1:]))
    for i in range(0, len(todo), 50):
        found = w.api(action="wbgetentities", ids="|".join(todo[i:i + 50]), props="labels|aliases").get("entities", {})
        for q in todo[i:i + 50]:
            e = found.get(q) or {}
            names = {norm_org(v["value"]) for v in e.get("labels", {}).values()} | \
                {norm_org(a["value"]) for al in e.get("aliases", {}).values() for a in al}
            have[q] = {"all": sorted(n for n in names if n),
                       "en": sorted({norm_org(e.get("labels", {}).get(k, {}).get("value")) for k in ("en", "mul")} - {""})}
    if todo:
        path.write_text(json.dumps(have, ensure_ascii=False), encoding="utf-8")
    return have


def doi_authors(dois):
    """{DOI: [authors (P50) of the Wikidata works with that DOI (P356)]}, None for a DOI not on Wikidata; QLever."""
    path = w.CACHE / "doi_authors.json"
    have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = sorted({x for x in dois if x not in have and SAFE_DOI.fullmatch(x)})
    for i in range(0, len(todo), 300):
        chunk = todo[i:i + 300]
        vals = " ".join(f'"{x.upper()}"' for x in chunk)
        on, by = set(), collections.defaultdict(set)
        for r in d.qlever(f"SELECT ?doi ?a WHERE {{ VALUES ?doi {{ {vals} }} ?w wdt:P356 ?doi . "
                          f"OPTIONAL {{ ?w wdt:P50 ?a }} }}"):
            if r and r[0]:
                on.add(r[0].lower())
                if len(r) > 1 and re.fullmatch(r"Q\d+", r[1]):
                    by[r[0].lower()].add(r[1])
        for x in chunk:
            have[x] = sorted(by[x]) if x in on else None
        path.write_text(json.dumps(have), encoding="utf-8")
        if (i // 300) % 10 == 9:
            w.out(f"  DOIs looked up on Wikidata: {i + len(chunk):,} of {len(todo):,}")
    return have


# ---------------------------------------------------------------- the comparison

def record_name(rec):
    """The name the record shows (its credit name, else given and family name; the first, if it lists several)."""
    n = rec.get("name") or {}
    return ((n.get("credit") or "").split(";")[0].strip()
            or " ".join(x for x in (n.get("given"), n.get("family")) if x))


TITLES = {"dr", "prof", "professor", "mr", "mrs", "ms", "phd", "md", "dipl", "ing", "mag", "habil", "sir", "dame"}


def record_forms(rec):
    """The record's names as folded word tuples, titles ('Dr.', 'Prof.') left out."""
    n = rec.get("name") or {}
    texts = [" ".join(x for x in (n.get("given"), n.get("family")) if x), n.get("credit") or ""] + (n.get("others") or [])
    forms = set().union(*[ms.forms(t) for t in texts if t])
    return {f for f in (tuple(t for t in x if t not in TITLES) for x in forms) if f}


def distance(a, b):
    """Edit distance (Levenshtein) of two short words."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def middle_conflict(rec_forms, item_forms):
    """Whether every name form that agrees in first and family name has another middle initial."""
    seen = False
    for x in rec_forms:
        for y in item_forms:
            if len(x) > 1 and len(y) > 1 and x[0] == y[0] and x[-1] == y[-1]:
                mx, my = {t[0] for t in x[1:-1]}, {t[0] for t in y[1:-1]}
                if not mx or not my or mx & my:
                    return False
                seen = True
    return seen


def firm(r, e, pid, key):
    """Whether the item's dates of birth or death can be relied on: only when the GND record gives them too (for a
    death, any GND date of death before 2012 will do). A reference alone is not enough (18 October 2026): Philip
    Molyneux 'died 2006' by the GND record of 2017, which now says 2023; Philippe Joly and Daniel I. Rees carry a
    namesake's dates from library records; a living philosopher 'died 2004' by a library record (Zbigniew
    Stawrowski). The rows whose dates the GND record does not give were checked by hand (orcid_hand_checks.tsv)."""
    mine, theirs = w.loose_years(r[key]), w.gnd_years(((r.get("gnd_record") or {}).get(key)) or [])
    if not mine or not theirs:
        return False
    return pid == "P570" and max(theirs) < w.ORCID_OPENED[0] or any(abs(x - y) <= 1 for x in mine for y in theirs)


def span(years):
    return "" if not years else str(years[0]) if years[0] == years[-1] else f"{years[0]} to {years[-1]}"


def compare(r, rec, authors, titles, orgs):
    """What the record shows about this item: verdict, group, reasons, facts, and one plain sentence."""
    q, others = r["item"], [o["item"] for o in r["other"]]
    e = w.get(q) or {}
    res = {"verdict": "check first", "reasons": [], "evidence": [], "group": None}
    facts, reasons = res["evidence"], res["reasons"]
    if rec.get("status") != "ok":
        res["group"] = "unreadable"
        why = {"409 9044": "it has been deactivated"}.get(rec.get("error"), rec.get("error") or "no answer")
        res["sentence"] = f"The ORCID record could not be read ({why})" + (
            f"; the iD was merged into [https://orcid.org/{rec['primary']} {rec['primary']}]." if rec.get("primary") else ".")
        facts.append(res["sentence"])
        return res
    works, jobs = rec["works"], rec["employments"] + rec["educations"]
    dois = sorted({x for wk in works for x in wk["dois"]})
    on_wd = {x: authors[x] for x in dois if authors.get(x) is not None}
    mine = [x for x in on_wd if q in on_wd[x]]
    theirs = {o: [x for x in on_wd if o in on_wd[x]] for o in others}
    same_titles = [wk["title"] for wk in works if norm_title(wk["title"]) in titles.get(q, set())]
    wyears = sorted(wk["year"] for wk in works if wk["year"])
    jyears = sorted(y for j in jobs for y in (j["start"], j["end"]) if y)
    died = w.loose_years(r["died"]) if firm(r, e, "P570", "died") else []      # only dates one can rely on
    born = w.loose_years(r["born"]) if firm(r, e, "P569", "born") else []
    c = {"name": record_name(rec) or "a name not made public", "rec": rec, "works": works, "dois": dois,
         "on_wd": on_wd, "mine": mine, "same_titles": same_titles, "wyears": wyears, "died": died, "born": born,
         "other_hits": [(o, xs) for o, xs in theirs.items() if xs], "third": [], "late": [], "late_jobs": [],
         "early": [], "early_jobs": [], "lifetime": [], "org_hit": None, "org_any": None}
    facts.append(f"the ORCID record ({c['name']}) lists {ms.plural(len(works), 'work')}"
                 + (f" from {span(wyears)}" if wyears else "") + f", {len(dois)} with a DOI ({len(on_wd)} of these on "
                 f"Wikidata), {ms.plural(len(rec['employments']), 'employment')} and "
                 f"{ms.plural(len(rec['educations']), 'education')}")
    if on_wd:
        facts.append(f"{len(mine)} of its DOIs are works of this item on Wikidata" + "".join(
            f", {len(xs)} of {o}" for o, xs in theirs.items()))
    if same_titles:
        facts.append(f"{len(same_titles)} of its works have the title of a work of this item on Wikidata, e.g. "
                     f"\"{same_titles[0][:80]}\"")

    # the years: work after the death, or before the 15th birthday; work from the lifetime of someone who died
    own_works = False
    if died:
        last = max(died)
        c["lifetime"] = sorted([y for y in wyears if y <= last] + [j["start"] for j in jobs if j["start"] and
                                                                     j["start"] <= last])
        c["late"] = [y for y in wyears if y >= last + 3]
        c["late_jobs"] = [j for j in jobs if j["start"] and j["start"] > last or j["end"] and j["end"] >= last + 2]
        if len(c["late"]) >= 3 or c["late_jobs"]:     # a paper or two can come out after a death; not a dozen
            reasons.append("work after death")
            facts.append(f"work dated {span(c['late'] or sorted(j['start'] or j['end'] for j in c['late_jobs']))}, "
                         f"after the death in {'/'.join(map(str, died))}")
        if c["lifetime"]:
            facts.append(f"also work dated {span(c['lifetime'])}, in this person's lifetime")
        # a record made after the death to list this person's own works: their name, work from their lifetime, no
        # post after the death, and no run of later papers that are the other item's works
        doi_year = {x: wk["year"] for wk in works for x in wk["dois"] if wk["year"]}
        later_by_other = sum(1 for _, xs in c["other_hits"] for x in xs if (doi_year.get(x) or 0) >= last + 3)
        own_works = last <= w.ORCID_OPENED[0] and bool(c["lifetime"]) and not c["late_jobs"] and later_by_other < 3
    if born:
        first = min(born)
        c["early"] = [y for y in wyears if y < first + 15]
        c["early_jobs"] = [j for j in rec["employments"] if j["start"] and j["start"] < first + 15]   # not schools
        dated = c["early"] + [j["start"] for j in c["early_jobs"]]
        if len(dated) >= 2 or any(y < first for y in dated):     # one date alone may be a slip (a post 'from' the birth year)
            reasons.append("work before birth")
            facts.append(f"work dated {span(c['early'] or sorted(j['start'] for j in c['early_jobs']))}, before the "
                         f"15th birthday of someone born in {'/'.join(map(str, born))}")
    after_death = "work after death" in reasons

    # ties to this item: a post or study at its own employer or school (P108, P69), if the post fits its life
    for j in jobs:
        for pid in ("P108", "P69"):
            hit = next((o for o in ms.values(e, pid, "id")
                        if same_org(j["org"], orgs.get(o) or {"all": [], "en": []})), None)
            if hit and not c["org_any"]:              # a place of this item, whatever the dates say
                c["org_any"] = (j, pid)
            s = j["start"]
            if s and (died and s > max(died) - 2 or born and not min(born) + 15 <= s <= max(born) + 75):
                continue                              # not a post this person can have held
            if not s and after_death:
                continue                              # undated, in the record of someone alive after the death
            if hit and not c["org_hit"]:
                c["org_hit"] = (j, pid)
                facts.append(f"the record's {'post' if j in rec['employments'] else 'study'} at {j['org']} is at this "
                             f"item's {'employer' if pid == 'P108' else 'school'} on Wikidata ({hit})")

    # whose works the record's papers are; titles count only when no DOI is anyone's work here
    other_max = max([len(xs) for xs in theirs.values()] + [0])
    toward = (bool(mine) and len(mine) >= other_max or not mine and not other_max and bool(same_titles)
              or bool(c["org_hit"]))
    if not mine and not same_titles:
        if c["other_hits"]:
            reasons.append("papers of the other item")
        rf = record_forms(rec)
        for a, n in collections.Counter(x for k in on_wd for x in on_wd[k] if x != q and x not in others).most_common():
            if n < 2:
                break
            ea = w.get(a) or {}
            if rf and any(ms.same_name(x, y) for x in rf for y in ms.names(ea)):
                c["third"].append((a, n, ms.label(ea), ms.values(ea, "P496")))
        if c["third"]:
            reasons.append("papers of a third item")
            a, n, lab, ids = c["third"][0]
            facts.append(f"{n} of its DOIs are works of {a} ({lab}), whose name fits the record"
                         + (f" and which carries ORCID iD {', '.join(ids)}" if ids else ""))

    # the name
    rf, inames = record_forms(rec), ms.names(e)
    if rf and inames:
        full = [x for x in rf if len(x) > 1]          # 'Stefano' alone cannot be compared
        shared_surname = any(set(x[1:]) & set(y[1:]) for x in full for y in inames if len(y) > 1)
        near_surname = any(distance(x[-1], y[-1]) <= 1 for x in full for y in inames if len(y) > 1 and
                           min(len(x[-1]), len(y[-1])) >= 4)    # 'Baber' for 'Barber'
        if full and not shared_surname and not near_surname and not any(ms.same_name(x, y) for x in rf for y in inames):
            # another family name ('Heinz Kurz' for Lujo Brentano); a spelling of the given name ('Nitu' for 'Neetu'),
            # a short form ('Jack' for 'John'), a second surname ('Hernández Bayter') or a slip ('Baber') is not counted
            reasons.append("another name")
            facts.append(f"the record's name ({c['name']}) has another family name than any label of the item")
        elif not mine and middle_conflict(rf, inames):
            reasons.append("another middle initial")
            facts.append(f"the record's name ({c['name']}) has another middle initial than the item ({r['label']})")

    # nothing dated: only a sourced death before 2012 decides (jz's rule for empty records: a reference on the item)
    c["death_years"] = w.loose_years(r["died"])
    if not wyears and not jyears and "a" in r["rules"] and died:
        reasons.append("nothing dated, died before 2012")
        if not died:
            facts.append("the person's own GND record (same year of birth) gives no date of death")

    # another item's papers never confirm alone: the tools that link papers to authors follow whichever item holds the
    # iD, so they cannot tell a namesake from a second item for this same person (those rows are checked by hand)
    firm_reasons = [x for x in reasons if x not in PAPERS]
    dates_only = firm_reasons and set(firm_reasons) <= {"work after death", "work before birth",
                                                         "nothing dated, died before 2012"}
    if toward:
        res["group"] = "ties to this item"
    elif dates_only and c["org_any"]:                 # the dates say someone else, the places say this person
        res["group"] = "ties to this item"
        c["date_conflict"] = True
    elif own_works and "another name" not in reasons:  # may list this person's own works, made after the death
        res["group"] = "lifetime works"
    elif firm_reasons:
        res["verdict"] = "confirmed wrong"
        res["group"] = min(firm_reasons, key=list(CONFIRM).index)
    elif reasons:
        res["group"] = "one person?"
    elif mine:                                        # most papers are the other item's, one or two this item's
        res["group"] = "mostly the other item's papers"
    else:
        res["group"] = "nothing public" if not works and not jobs else "undecided"
    res["sentence"] = sentence(r, res["group"], c)
    return res


def jobs_text(jobs):
    j = jobs[0]
    when = f" from {j['start']}" if j["start"] else f" until {j['end']}" if j["end"] else ""
    return f"a post at {j['org'] or 'an organisation'}{when}"


def as_works(n, who):
    """'1 as a work of X', '12 as works of X'."""
    return f"none as a work of {who}" if not n else f"{n} as {'a work' if n == 1 else 'works'} of {who}"


def sentence(r, g, c):
    """One plain sentence: what the ORCID record shows, and why it is or is not this person."""
    name, rec, died, born = c["name"], c["rec"], c["died"], c["born"]
    count = f"The ORCID record lists {ms.plural(len(c['dois']), 'work')} with a DOI"
    others = " and ".join(as_works(len(xs), w.q_link(o)) for o, xs in c["other_hits"])
    if g == "work after death":
        what = f"work from {span(c['late'])}" if len(c["late"]) >= 3 else jobs_text(c["late_jobs"])
        return f"The ORCID record of {name} shows {what}, but this person died in {'/'.join(map(str, died))}."
    if g == "work before birth":
        what = f"work from {span(c['early'])}" if c["early"] else jobs_text(c["early_jobs"])
        return f"The ORCID record of {name} shows {what}, but this person was born in {'/'.join(map(str, born))}."
    if g == "another name":
        return f"The ORCID record is in the name of {name}, not {r['label']}."
    if g == "another middle initial":
        return f"The ORCID record is in the name of {name}; this item is {r['label']}, another middle initial."
    if g == "nothing dated, died before 2012":
        return (f"The ORCID record of {name} shows nothing dated, and this person died in "
                f"{'/'.join(map(str, c['death_years']))} (a sourced date), before ORCID existed.")
    if g == "ties to this item":
        parts = []
        if c.get("org_hit"):
            j, pid = c["org_hit"]
            parts.append(f"shows {jobs_text([j])}, which Wikidata gives as this item's "
                         f"{'employer' if pid == 'P108' else 'place of study'}")
        if c["mine"] or others:
            parts.append(f"has on Wikidata {as_works(len(c['mine']), 'this item')}" + (f" and {others}" if others else "")
                         + f" among its {ms.plural(len(c['dois']), 'work')} with a DOI")
        elif c["same_titles"]:
            parts.append(f"lists {ms.plural(len(c['same_titles']), 'work')} with the title of a work of this item on "
                         f"Wikidata (\"{c['same_titles'][0][:60]}\")")
        if c.get("date_conflict"):
            j, pid = c["org_any"]
            return (f"The ORCID record of {name} shows {jobs_text([j])}, which Wikidata gives as this item's "
                    f"{'employer' if pid == 'P108' else 'place of study'}, but the record's dates do not fit this "
                    "person's dates on Wikidata, so one of them may be wrong.")
        other_max = max([len(xs) for _, xs in c["other_hits"]] + [0])
        verdict = ("so the iD may be this person's after all" if len(c["mine"]) >= other_max else
                   "but most of its papers are the other item's, so it is not clear whose iD it is")
        return f"The ORCID record of {name} " + "; it ".join(parts) + f", {verdict}."
    if g == "one person?":
        if c["third"]:
            a, n, lab, _ = c["third"][0]
            third = as_works(n, w.q_link(a)) + f" (another item named {lab})"
            others = f"{others} and {third}" if others else third
        return (f"{count}; {len(c['on_wd'])} of them {'is' if len(c['on_wd']) == 1 else 'are'} on Wikidata, {others}, "
                "and none as a work of this item. That alone does not settle it: the papers follow whichever item "
                "holds the iD.")
    if g == "mostly the other item's papers":
        return (f"{count}; on Wikidata {others}, but also {as_works(len(c['mine']), 'this item')}, perhaps linked to "
                "this item by mistake.")
    if g == "lifetime works":
        return (f"The ORCID record of {name} lists work from {span(sorted(c['wyears'] + c['lifetime']))}, partly from "
                f"before this person's death in {'/'.join(map(str, died))}, so it may list their own works or a "
                "namesake's.")
    if g == "nothing public":
        return f"The ORCID record of {name} shows no works and no posts, so it cannot tell."
    bits = []
    if c["works"]:
        bits.append(ms.plural(len(c["works"]), "work") + (f" from {span(c['wyears'])}" if c["wyears"] else ""))
    if rec["employments"] or rec["educations"]:
        bits.append(jobs_text(rec["employments"] or rec["educations"]))
    wd = (" none of its DOIs is on Wikidata as a work of this item or the other" if r["other"] else
          " none of its DOIs is on Wikidata as a work of this item") if c["dois"] else " no DOI"
    return f"The ORCID record of {name} lists {' and '.join(bits)};{wd}, and nothing in it rules this person out."


# ---------------------------------------------------------------- the page

def gnd_short(r):
    return ocp.gnd(r) or "–"


def page(data):
    rows = data["rows"]
    conf = [r for r in rows if r["orcid_verdict"] == "confirmed wrong"]
    fine = [r for r in rows if r["orcid_verdict"] == "not wrong"]
    rest = [r for r in rows if r["orcid_verdict"] == "check first"]
    by_conf = collections.Counter(r["orcid_group"] for r in conf)
    by_hand = [r for r in rows if r.get("orcid_hand")]
    lines = [
        f"This list shows {{{{P|496}}}} values that QuickStatements {w.BATCH_LINK} (\"add missing ORCID via GND\", "
        f"[[User:{w.BATCH_USER}|{w.BATCH_USER}]], July 2026) added to Wikidata items. The batch copied ORCID iDs from "
        "GND records to the items with the same {{P|227}}. The German National Library added most of these iDs to the "
        "GND by machine matching (names and title data matched against the ORCID data), and most were never checked, "
        "so some belong to another person of the same name: the actress Elke Arendt ({{Q|1331446}}) had the iD of the "
        "food scientist Elke K. Arendt ({{Q|88075585}}).",
        "",
        "'''Nothing has been removed yet.''' Comments are welcome on [[User talk:" + w.BATCH_USER + "]].",
        "",
        "== How the list was made ==",
        "# Wikidata: the items that got an iD from the batch were listed when the person died before ORCID existed "
        "(October 2012), was born before 1900, has only occupations that do not come with an ORCID iD (actor, athlete, "
        "musician…), or is marked as different ({{P|1889}}) from the other item with the same iD.",
        "# ORCID: each listed iD was then compared with its public ORCID record: whose works its papers are on "
        "Wikidata ({{P|50}}, by DOI), the years of its works, posts and studies, the places of its posts, and the name.",
        "'''Confirmed wrong''' means the ORCID record itself shows someone else:",
        "* it shows work after this person's death (at least three works, or a post) or from before they were 15;",
        "* it is in the name of someone else;",
        "* it shows nothing dated, and this person died before ORCID existed;",
        "* or, compared by hand with the item, its posts, studies and works are those of someone in another field or "
        "place.",
        "",
        "Two signs do not count on their own. The record's papers being works ({{P|50}}) of the other item with the "
        "iD prove little: the tools that link papers to their authors use whichever item holds the iD, so the papers "
        "follow a wrong iD too. And a date of birth or death counts only when the GND record gives it too: some items "
        "carry a namesake's dates, even with a source. "
        f"The {len(by_hand)} rows that rested on such signs were checked by hand against the record's posts, studies "
        f"and works and the item's own sources; {len(fine)} of them turned out not to be wrong (see '''Not wrong after "
        "all''').",
        "",
        "Everything else is listed under '''Check first''', however clear the Wikidata rules looked: among them records "
        "with papers of this item or a post at its own employer or school, records that may list a dead person's own "
        "works, and records whose papers are the other item's when the two items may be one person.",
        "",
        "== Counts ==",
        f"* '''{len(rows):,}''' items listed by the Wikidata rules: the ORCID record confirms '''{len(conf):,}''' as "
        f"wrong, '''{len(fine):,}''' are not wrong after all, and '''{len(rest):,}''' are to check first.",
        "* Confirmed wrong, by what the record shows:"] + [
        f"** {v}: {CONFIRM[k][0].lower() + CONFIRM[k][1:]}"
        for k, v in sorted(by_conf.items(), key=lambda kv: list(CONFIRM).index(kv[0]))] + [
        "* Not wrong after all:"] + [
        f"** {sum(1 for r in fine if r['orcid_group'] == k)}: {t[0].lower() + t[1:]}" for k, (t, _) in NOT_WRONG.items()
        if any(r["orcid_group"] == k for r in fine)] + [
        "* Check first, by question:"] + [f"** {n}: {t}" for t, n in check_groups(rest)] + [
        "",
        "== Confirmed wrong ==",
        "The ORCID record shows that the iD belongs to someone else.",
    ]
    for key, title in CONFIRM.items():
        part = sorted((r for r in conf if r["orcid_group"] == key), key=lambda x: x.get("label") or "")
        if not part:
            continue
        lines += ["", f"=== {title} ({len(part)}) ==="]
        if key == "checked by hand":
            lines += ["On Wikidata the record's papers are works of the other item with the iD, which alone proves "
                      "little. Each was compared by hand with the record's posts, studies and works.", ""]
        lines += ['{| class="wikitable sortable"',
                  "! Person !! What the ORCID record shows !! GND record !! ORCID !! Other item with the iD"]
        for r in part:
            others = ", ".join(w.q_link(o["item"]) for o in r["other"]) or "–"
            lines += ["|-", f"| {w.q_link(r['item'])} || {r['orcid_sentence']} || {gnd_short(r)} || "
                            f"[https://orcid.org/{r['orcid']} {r['orcid']}] || {others}"]
        lines.append("|}")
    if fine:
        lines += ["", "== Not wrong after all ==",
                  "The rules listed these, but a check by hand showed that the iD is right: the mistake is elsewhere."]
        for key, (title, what) in NOT_WRONG.items():
            part = sorted((r for r in fine if r["orcid_group"] == key), key=lambda x: x.get("label") or "")
            if not part:
                continue
            lines += ["", f"=== {title} ({len(part)}) ===", what, "", '{| class="wikitable sortable"',
                      "! Person !! What we found !! GND record !! ORCID !! Other item with the iD"]
            for r in part:
                others = ", ".join(w.q_link(o["item"]) for o in r["other"]) or "–"
                lines += ["|-", f"| {w.q_link(r['item'])} || {r['orcid_sentence']} || {gnd_short(r)} || "
                                f"[https://orcid.org/{r['orcid']} {r['orcid']}] || {others}"]
            lines.append("|}")
    lines += ["", "== Check first ==",
              "These need a look before anything is removed: the ORCID record does not settle them. Each row says what "
              "the record shows, what Wikidata says, and the question to answer."]
    for title, intro, part, rowfn, question in check_tables(rest):
        lines += ["", f"=== {title} ({len(part)}) ===", intro, "", '{| class="wikitable sortable"',
                  "! Person !! What the ORCID record shows !! What Wikidata says !! Question !! GND record !! ORCID"]
        for r in sorted(part, key=lambda x: x.get("label") or ""):
            found, q = rowfn(r)
            lines += ["|-", f"| {w.q_link(r['item'])} || {r['orcid_sentence']} || {found} || {question or q} || "
                            f"{gnd_short(r)} || [https://orcid.org/{r['orcid']} {r['orcid']}]"]
        lines.append("|}")
    return "\n".join(lines) + "\n"


def check_tables(rest):
    """(heading, introduction, rows, row function, question or None) for each group of the check-first part."""
    tables = [("Unclear after a check by hand",
               "These were compared by hand with the record and the item's sources, which do not settle them.",
               [r for r in rest if r["orcid_group"] == "unclear by hand"], first_row,
               "Does the iD belong to this person?"),
              ("Does the iD belong to this person after all?",
               "Some of the record's papers are works of this item on Wikidata (at least as many as of the other item), "
               "or the record shows a post or studies at the place Wikidata gives as this item's employer or school.",
               [r for r in rest if r["orcid_group"] == "ties to this item"], first_row,
               "Is this the person of the ORCID record? Then the iD stays."),
              ("Are the two items one person?",
               "The record's papers are works of the other item with the iD and none of this item, but that alone "
               "does not settle it, and nothing else in the record does: the two items may be one person with a wrong "
               "date or occupation on this item.",
               [r for r in rest if r["orcid_group"] == "one person?"], first_row,
               "If they are one person, the items should be merged and the iD stays; if not, it is the other person's."),
              ("Are this item's few papers really someone else's?",
               "Most of the record's papers are works of the other item with the iD on Wikidata, but one or a few are "
               "linked to this item, perhaps by mistake.",
               [r for r in rest if r["orcid_group"] == "mostly the other item's papers"], first_row,
               "If this item's papers are the other person's, the iD is not this person's."),
              ("Does the record list this person's own works?",
               "This person died before ORCID existed, but the record also lists work from their lifetime: it may be "
               "a list of their own works, made after their death, or the record of a namesake.",
               [r for r in rest if r["orcid_group"] == "lifetime works"], first_row,
               "Whose works are these: this person's, or a namesake's?")]
    undecided = [r for r in rest if r["orcid_group"] == "undecided"]
    for rule, (title, intro, fn) in UNDECIDED.items():
        tables.append((title, intro, [r for r in undecided if r["rules"][0] == rule], fn, None))
    tables.append(("The ORCID record shows nothing public", "The record lists no works and no posts, so it cannot "
                   "tell.", [r for r in rest if r["orcid_group"] == "nothing public"], first_row, None))
    tables.append(("The ORCID record could not be read", "", [r for r in rest if r["orcid_group"] == "unreadable"],
                   first_row, None))
    return [t for t in tables if t[2]]


def check_groups(rest):
    return [(t[0], len(t[2])) for t in check_tables(rest)]


def first_row(r):
    """What Wikidata says, by the first rule the item broke (the plain words of orcid_check_page)."""
    return UNDECIDED[r["rules"][0]][2](r)


# ---------------------------------------------------------------- run

def hand_checks():
    """(iD, item) -> the row of orcid_hand_checks.tsv: verdict and the sentence for the page."""
    with open(HAND, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    bad = [r for r in rows if r["verdict"] not in HAND_GROUP]
    if bad:
        raise SystemExit(f"{HAND.name}: unknown verdict in {[(r['item'], r['verdict']) for r in bad]}")
    return {(r["orcid"], r["item"]): r for r in rows}


def main():
    t0 = time.time()
    path = w.OUT / "orcid_wrong.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    ids = sorted({r["orcid"] for r in rows})
    w.out(f"{len(rows)} flagged rows, {len(ids)} ORCID records")
    recs = {}
    for k, orcid in enumerate(ids, 1):
        recs[orcid] = record(orcid)
        if k % 50 == 0:
            w.out(f"  {k} of {len(ids)} records")
    all_dois = {x for rec in recs.values() if rec.get("status") == "ok" for wk in rec["works"] for x in wk["dois"]}
    w.out(f"looking up {len(all_dois):,} DOIs on Wikidata (QLever, cached)")
    authors = doi_authors(all_dois)
    third = collections.Counter()                     # the other authors of the records' works: fetched to compare names
    for r in rows:
        rec = recs[r["orcid"]]
        if rec.get("status") == "ok":
            skip = {r["item"]} | {o["item"] for o in r["other"]}
            hits = collections.Counter(a for wk in rec["works"] for x in wk["dois"] for a in (authors.get(x) or [])
                                       if a not in skip)
            third.update(a for a, n in hits.items() if n >= 2)
    w.fetch(list(third))
    titles = item_titles([r["item"] for r in rows])
    orgs = org_names({o for r in rows for pid in ("P108", "P69") for o in ms.values(w.get(r["item"]) or {}, pid, "id")})
    hand = hand_checks()
    for r in rows:
        res = compare(r, recs[r["orcid"]], authors, titles, orgs)
        h = hand.get((r["orcid"], r["item"]))
        r["orcid_hand"] = h["verdict"] if h else None
        if h:                                         # a person's verdict replaces the rules
            (res["verdict"], res["group"]), res["sentence"] = HAND_GROUP[h["verdict"]], h["text"]
            res["evidence"].append(f"checked by hand: {h['verdict']}")
        r["orcid_verdict"], r["orcid_group"], r["orcid_reasons"] = res["verdict"], res["group"], res["reasons"]
        r["orcid_evidence"], r["orcid_sentence"] = res["evidence"], res["sentence"]
        rec = recs[r["orcid"]]
        r["orcid_record"] = {"status": rec.get("status"), "name": record_name(rec) if rec.get("status") == "ok" else None,
                             "works": len(rec.get("works") or []), "employments": len(rec.get("employments") or []),
                             "educations": len(rec.get("educations") or [])}
    conf = collections.Counter(r["orcid_group"] for r in rows if r["orcid_verdict"] == "confirmed wrong")
    fine = collections.Counter(r["orcid_group"] for r in rows if r["orcid_verdict"] == "not wrong")
    rest = collections.Counter(r["orcid_group"] for r in rows if r["orcid_verdict"] == "check first")
    data["orcid_pass"] = {"date": w.today(), "records": len(ids), "confirmed_wrong": sum(conf.values()),
                          "not_wrong": sum(fine.values()), "check_first": sum(rest.values()),
                          "confirmed_by": dict(conf.most_common()), "not_wrong_by": dict(fine.most_common()),
                          "check_first_by": dict(rest.most_common()), "checked_by_hand": len(hand),
                          "all_reasons": dict(collections.Counter(x for r in rows for x in r["orcid_reasons"])),
                          "runtime_s": round(time.time() - t0)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (w.OUT / "orcid_wrong.wiki").write_text(page(data), encoding="utf-8")
    w.out(f"confirmed wrong: {sum(conf.values())} {dict(conf.most_common())}")
    w.out(f"not wrong: {sum(fine.values())} {dict(fine.most_common())}")
    w.out(f"check first: {sum(rest.values())} {dict(rest.most_common())}")
    loose = [r for r in rows if r["orcid_verdict"] == "check first" and r["orcid_group"] == "one person?"
             and not r["orcid_hand"] and not r.get("check")]
    if loose:                                         # papers were the only sign of a firm listing: check these by hand
        w.out(f"{len(loose)} rows to check by hand (add them to {HAND.name}): " + ", ".join(r["item"] for r in loose))
    w.out(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "fetch":       # only fetch the records into the cache
        flagged = sorted({r["orcid"] for r in json.loads((w.OUT / "orcid_wrong.json").read_text(encoding="utf-8"))["rows"]})
        for n_, orcid_ in enumerate(flagged, 1):
            record(orcid_)
            if n_ % 50 == 0:
                w.out(f"  {n_} of {len(flagged)} records")
        w.out(f"{len(flagged)} records cached")
    elif len(sys.argv) > 1 and sys.argv[1] == "sample":    # the random confirmed rows, for the report
        dd = json.loads((w.OUT / "orcid_wrong.json").read_text(encoding="utf-8"))
        pool = [r for r in dd["rows"] if r.get("orcid_verdict") == "confirmed wrong"]
        for r in random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 1018).sample(pool, min(10, len(pool))):
            print(f"{r['orcid']} {r['item']} {r['label']} [{r['orcid_group']}]: {r['orcid_sentence']}")
    else:
        main()
