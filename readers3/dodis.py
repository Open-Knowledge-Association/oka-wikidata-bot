"""Dodis (P701), the database of the Diplomatic Documents of Switzerland (dodis.ch, a research centre of the Swiss
Academy of Humanities and Social Sciences). Each person's page, dodis.ch/P<n>, shows the person's record as schema.org
data: name, dates of birth and death (machine-readable, beside the date shown), places of birth and death (Dodis's
own place entries, shown by their English name), gender, 'Reference country', 'Activity' (Dodis's own list) and
'Relations to other persons' (each line linking the other person's own entry).

Access: robots.txt bars all crawlers and names Claude's agents; Dodis's documented open-data export holds no life
data. jz decided (2026-10-17) on a slow crawl for the test run: the bot's own user agent (sources3.polite_get), one
page every 12 seconds, at most about 40 person pages (12 of them read on 2026-10-16 and taken from there), stopping
for good at the first page that is not a person's record (a challenge, CAPTCHA, refusal or block): no further page is
asked for, nothing is tried around it. Only the person's record is kept, not the functions or the documents that
mention the person.

Facts:
- sex ('Gender');
- dates of birth and death: the record's machine-readable date when the date shown says the same (in the Swiss
  calendar rule: a day from 1583 to 1812 only sources an equal value);
- places of birth and death: Dodis shows its place entry's English name and no country, and the place entries are
  not on Wikidata; a name is used only when the one Swiss town so named, the one well-known city so named anywhere
  and the towns so named in the person's reference countries all lead to the same single item ('Vienna', 'Bern');
  else noted ('Baden' in Switzerland and in Austria);
- occupations through a checked mapping of Dodis's activity words (others noted), not where the item already gives
  the occupation more precisely ('engineer' beside 'civil engineer');
- father, mother, spouse, siblings and children from the relation lines of those kinds whose other person is linked
  to his or her own Dodis entry, that entry's ID being on exactly one item, a human whose year of birth fits the
  relation ('is the child of' read in its direction; a parent is father or mother by the relative's sex on
  Wikidata). Grandchildren, in-laws and the like are not used."""
import html
import re

import dictionaries as d
import sources3 as s
from readers3 import ndb as n
from readers3 import oebl
from readers3.sbi import without_coarser

WORK = "dodis"
URL = "https://dodis.ch/{}"
GAP = 12.0                                      # seconds between pages (jz: at least 10)
MAX_PAGES = 40                                  # person pages in the cache, at most (jz: about 40 in all)
SWITZERLAND = "Q39"
LANGS = ("en", "de", "fr", "it")
SEX = {"male": n.MALE, "female": n.FEMALE}
# Dodis's activity words -> (item, its English label, checked on Wikidata 2026-10-17); others are noted
ACTIVITIES = {
    "Politician": ("Q82955", "politician"), "Diplomat": ("Q193391", "diplomat"), "Engineer": ("Q81096", "engineer"),
    "Trade Unionist": ("Q15627169", "trade unionist"), "Journalist": ("Q1930187", "journalist"),
    "Economist": ("Q188094", "economist"), "Banker": ("Q806798", "banker"), "Entrepreneur": ("Q131524", "entrepreneur"),
    "Lawyer": ("Q40348", "lawyer"), "Jurist": ("Q185351", "jurist"), "Civil Servant": ("Q212238", "civil servant"),
    "Scientist": ("Q901", "scientist"), "Industrialist": ("Q6606110", "industrialist"),
    "Theologian": ("Q1234713", "theologian"), "Historian": ("Q201788", "historian"), "Writer": ("Q36180", "writer"),
    "Physician": ("Q39631", "physician"), "Architect": ("Q42973", "architect"), "Artist": ("Q483501", "artist"),
    "Businessman": ("Q43845", "businessperson"), "Publisher": ("Q2516866", "publisher"),
    "Translator": ("Q333634", "translator"), "Teacher": ("Q37226", "teacher"), "Philosopher": ("Q4964182", "philosopher"),
}
SIGNS_OF_A_BLOCK = re.compile(r"captcha|cf-chl|challenge-platform|just a moment|attention required|access denied|"
                              r"forbidden|too many requests|rate limit", re.I)
_stopped = None                                 # why the crawl stopped, once it has


def candidates(n_):
    return s.linked("P701", n_)


def person_record(text, ident):
    """The person's record of a Dodis page (the schema.org Person block up to the Metagrid links, scripts left out),
    or None when the page holds no record of this person."""
    start = text.find('itemtype="http://schema.org/Person"')
    end = text.find('id="metagrid-widget"', start)
    if start < 0 or end < 0:
        return None
    block = re.sub(r"<script\b.*?</script>", "", text[start:end], flags=re.S)
    shown = re.search(r'<h2>Information about Person <a href="https?://dodis\.ch/(P\d+)".*?</h2>', block, re.S)
    content = block.find('<div class="blue-box-content">')     # (the portrait and its links are left out)
    return shown.group(0) + "\n" + block[content:] if shown and shown.group(1) == ident and content >= 0 else None


def page(ident):
    """The person's record, read once (one page every 12 seconds, at most MAX_PAGES pages; none after a block)."""
    def fetch():
        global _stopped
        if _stopped:
            raise RuntimeError(f"not read: the crawl stopped at an earlier page ({_stopped})")
        if len(list((s.WORK3 / WORK).glob("P*.html"))) >= MAX_PAGES:
            raise RuntimeError(f"not read: {MAX_PAGES} pages read already (jz's limit for the test)")
        text = s.polite_get(URL.format(ident), gap=GAP)
        record = person_record(text, ident)
        if record is None:
            why = "a challenge or block page" if SIGNS_OF_A_BLOCK.search(text[:20000]) else "no record of this person"
            _stopped = f"{URL.format(ident)} gave {why} ({len(text)} bytes)"
            raise RuntimeError(f"crawl stopped: {_stopped}")
        return record
    return s.cached(WORK, f"{ident}.html", fetch)


# ---------------------------------------------------------------- dates and places

def shown_date(text):
    """(year, month, day) of a date as Dodis shows it: '17.3.1865', '3.1865', '1771'; None for anything else."""
    text = " ".join(text.split())
    for rx, order in ((r"(\d{1,2})\.(\d{1,2})\.(\d{3,4})", (2, 1, 0)), (r"(\d{1,2})\.(\d{3,4})", (1, 0, None)),
                      (r"(\d{3,4})", (0, None, None))):
        m = re.fullmatch(rx, text)
        if m:
            g = [int(x) for x in m.groups()]
            return tuple(g[i] if i is not None else None for i in order)
    return None


def machine_date(raw):
    m = re.fullmatch(r"(\d{4})(?:-(\d\d)(?:-(\d\d))?)?", raw or "")
    return tuple(int(x) if x else None for x in m.groups()) if m else None


def life_line(record):
    """{'*': (date shown, place id, place name), '†': ...} of the line under the name ('* 17.3.1865 Telšiai • †
    16.12.1922 Warsaw', the places linked to Dodis's place entries)."""
    m = re.search(r'<div style="margin-bottom:\.3em">(.*?)</div>', record, re.S)
    if not m:
        return {}
    text = re.sub(r'<a href="https?://dodis\.ch/(G\d+)"[^>]*>(.*?)</a>', lambda a: f"⟦{a.group(1)}|{d.clean(a.group(2))}⟧",
                  m.group(1), flags=re.S)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
    found = {}
    for part in text.split("•"):
        p = re.fullmatch(r"\s*([*†])\s*(.*?)\s*(?:⟦(G\d+)\|([^⟧]*)⟧)?\s*", part)
        if not p:
            continue
        when, gid, name = p.group(2), p.group(3), p.group(4)
        unlinked = None if gid else re.fullmatch(r"(\S*\d)\s+(\D.*)", when)
        if unlinked:                                  # a place shown without a link to its entry
            when, name = unlinked.groups()
        found[p.group(1)] = (when, gid, name)
    return found


def countries_named(record):
    """The items of the person's reference countries ('Reference country: Switzerland • Poland')."""
    m = re.search(r"Reference country:(.*?)</div>", record, re.S)
    found = []
    for name in re.findall(r"<a [^>]*>([^<]+)</a>", m.group(1)) if m else []:
        region = s.region_named(d.clean(name), ("en",))
        if region and region[0] == "country":
            found += [c for c in region[1] if c not in found]
    return found


def dodis_place(name, countries, label, notes):
    """The one item that the Swiss town of this name, the well-known city of this name and the towns of this name in
    the reference countries all lead to; else None (noted)."""
    if not name or not d.PLAIN.match(name) or re.search(r"[()\d,/]", name):
        notes.append(f"{label} '{name}' not matched to one place item")
        return None
    found = {q for q in [s.town(name, LANGS, SWITZERLAND), d.world_city(name, LANGS)]
             + [s.town(name, LANGS, c) for c in countries if c != SWITZERLAND] if q}
    if len(found) == 1:
        return found.pop()
    notes.append(f"{label} '{name}' not matched to one place item"
                 + (f" (several: {', '.join(sorted(found))})" if found else ""))
    return None


# ---------------------------------------------------------------- relatives

def relation_kind(phrase, subject_left):
    """What the other person is to the subject, from a relation line 'LEFT <phrase> RIGHT'."""
    word = re.sub(r"^is\s+(?:the\s+)?|\s+(?:of|to)$", "", phrase.strip().lower())
    if word in ("child", "son", "daughter"):                  # LEFT is the child of RIGHT
        return "parent" if subject_left else "child"
    if word in ("father", "mother", "parent"):                # LEFT is the father of RIGHT
        return "child" if subject_left else ("parent" if word == "parent" else word)
    if word in ("brother", "sister", "sibling", "half-brother", "half-sister"):
        return "sibling"
    if word in ("married", "wife", "husband", "spouse"):
        return "spouse"
    return None


def relation_lines(record, subject):
    """[(relation, Dodis ID, name)] of the 'Relations to other persons' lines that link the other person and name
    the subject as the record does ('Blocher, Wolfram is the child of Blocher, Eduard')."""
    at = record.find("Relations to other persons")
    found = []
    for line in re.findall(r"<p\b[^>]*>(.*?)</p>", record[at:], re.S) if at >= 0 else []:
        links = re.findall(r'<a href="/(P\d+)">([^<]*)</a>', line)
        if len(links) != 1:
            continue
        text = re.sub(r'<a href="/P\d+">[^<]*</a>', "⟦⟧", line)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split()).split(" • ")[0].strip()
        left = re.fullmatch(r"⟦⟧\s+(is .+?)\s+" + re.escape(subject), text)
        right = re.fullmatch(re.escape(subject) + r"\s+(is .+?)\s+⟦⟧", text)
        kind = relation_kind(left.group(1), False) if left else relation_kind(right.group(1), True) if right else None
        if kind:
            found.append((kind, links[0][0], d.clean(links[0][1])))
    return found


def year_born(q):
    years = {int(m.group(1)) for r in d.lookup(f"SELECT ?b WHERE {{ wd:{q} wdt:P569 ?b }}")
             if r and (m := re.match(r"(\d{4})-", r[0]))}
    return years.pop() if len(years) == 1 else None


def relatives(record, subject, born, notes):
    facts, seen = [], set()
    for kind, rid, name in relation_lines(record, subject):
        q = s.item_with("P701", rid)
        if not q:
            notes.append(f"{kind} '{name}': Dodis {rid} is on no single item; not used")
            continue
        if not d.lookup(f"SELECT ?c WHERE {{ wd:{q} wdt:P31 wd:Q5 . BIND(1 AS ?c) }}"):
            notes.append(f"{kind} '{name}': the item of Dodis {rid} ({q}) is not a human; not used")
            continue
        if kind == "parent":
            kind = {n.MALE: "father", n.FEMALE: "mother"}.get(d.sex(q))
            if not kind:
                notes.append(f"parent '{name}' ({q}): no single sex on Wikidata, so neither father nor mother; not used")
                continue
        if (kind, q) in seen:
            continue
        seen.add((kind, q))
        if not n.years_fit(kind, born, year_born(q)):
            notes.append(f"{kind} '{name}' ({q}): the years of birth do not fit; not used")
            continue
        f = n.relative_fact(kind, q, notes, name)
        if f:
            facts.append(f)
    return facts


# ---------------------------------------------------------------- the entry

def read(ident):
    record = page(ident)
    family = d.clean((re.search(r'itemprop="familyName">(.*?)</span>', record) or [None, ""])[1])
    given = d.clean((re.search(r'itemprop="givenName">(.*?)</span>', record) or [None, ""])[1])
    title = f"{given} {family}".strip() or ident
    subject = f"{family}, {given}" if given else family
    facts, notes, born = [], [], None
    sex = re.search(r"Gender:\s*(male|female)\b", record)
    if sex:
        facts.append(d.fact("P21", d.item_value(SEX[sex.group(1)]), "sex or gender"))
    countries = countries_named(record)
    line = life_line(record)
    for sym, prop, (dpid, dlabel), (ppid, plabel) in (("*", "birthDate", ("P569", "date of birth"), ("P19", "place of birth")),
                                                      ("†", "deathDate", ("P570", "date of death"), ("P20", "place of death"))):
        when, gid, name = line.get(sym, (None, None, None))
        machine = machine_date((re.search(rf'<time content="([^"]*)" itemprop="{prop}"', record) or [None, ""])[1])
        if when or machine:
            if machine and shown_date(when or "") == machine:
                y, mo, day = machine
                if day and not n.valid_day(y, mo, day):
                    notes.append(f"{dlabel} {when} not a valid day")
                else:
                    f = s.date_fact(dpid, dlabel, y, mo, day, SWITZERLAND)
                    facts.append(f)
                    if dpid == "P569":
                        born = y
            else:
                notes.append(f"{dlabel} '{when}' not used: not an exact date, or not the one the record gives")
        if name:
            q = dodis_place(name, countries, plabel, notes)
            if q:
                facts.append(d.fact(ppid, d.item_value(q), plabel))
    act = re.search(r"Activity:(.*?)</div>", record, re.S)
    if act:
        words = [w.strip() for w in d.clean(act.group(1)).split("•") if w.strip()]
        known = oebl.checked(ACTIVITIES)
        for w in words:
            if w in known:
                facts.append(d.fact("P106", d.item_value(known[w][0]), "occupation"))
            else:
                notes.append(f"occupation '{w}' not mapped")
    facts = without_coarser(facts, "P701", ident, notes)      # not 'engineer' where the item has 'civil engineer'
    facts += relatives(record, subject, born, notes)
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
