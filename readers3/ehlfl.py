"""Historisches Lexikon des Fürstentums Liechtenstein online (eHLFL, P13079: the article's page name), Liechtenstein-
Institut, historisches-lexikon.li (MediaWiki). Of each article only the first paragraph and the GND number under
'Normdaten' are used. robots.txt admits only named search engines and the site documents no API: the test run reads
only the 30 pages read on 2026-10-16 (one every 10 seconds under the bot's name, jz's rule for this work: a slow crawl
of at most about 30 pages); a page not yet read would be fetched the same way.

The first paragraph has a fixed form: 'Landtagsabgeordneter, Hauptmann und Landestechniker. *3.1.1831 Vaduz,
†19.10.1893 Vaduz, von Vaduz. Sohn des Rentmeisters [Johann Peter] und der Elisabeth Carigiet, zehn Geschwister, u.a.
Regierungssekretär [David] ... ⚭ 30.6.1863 Theresia Rheinberger (...), Tochter des «Löwen»-Wirts [Anton], ..., vier
Kinder, u.a. Schriftstellerin [Hermine] ...' ([...]: a link to the relative's own article).

Facts:
- dates of birth and death after '*' and '†' (the German helpers of readers3/ndb.py: exact dates only; 'um 1357' or
  '1640/50' are noted, not used), in the calendar of the place's country;
- places of birth and death with the German place helpers of readers3/oebl.py: 'Vaduz', 'Bendern (Gemeinde Gamprin)',
  'Vaduz (Spital)' (a town of Liechtenstein, else a well-known city: 'Wien'), 'Buchs (SG)', 'Schruns (Vorarlberg)',
  'Eichstätt (D)' (a town of the canton, land or country named). A town is used only when no other settlement of that
  name lies in the same region, unless it is the one city among them ('Ebersdorf (A)' is one of several in Austria:
  not used). 'X bei Y', castles and uncertain places ('Vaduz (?)') are noted, not used. A bare 'Feldkirch' is
  Feldkirch in Vorarlberg, next to Liechtenstein;
- religion ('katholisch'); occupations of the opening words through the checked mapping of readers3/ndb.py (offices
  such as 'Landtagsabgeordneter' are noted, not used; nor one the item already gives more precisely); the GND number
  under 'Normdaten';
- father and mother ('Sohn des [X] und der [Y]'), siblings (the list after 'zehn Geschwister', or 'Schwester des [X]'
  opening a sentence), spouses (a link right after '⚭') and children (the list after 'vier Kinder', or 'Vater des
  [X]' opening a sentence): only a link in that place, never one in the description of another relative ('und der
  Henriette, Tochter des [Alois II.]' is her father; after '⚭', 'Tochter des [Anton]' the spouse's). The linked
  article's ID must be on exactly one item, a human whose year of birth fits the relation."""
import functools
import html
import json
import re
import urllib.parse

import dictionaries as d
import sources3 as s
from readers3 import ndb as n
from readers3 import oebl
from readers3.sbi import without_coarser

WORK = "ehlfl"
URL = "https://historisches-lexikon.li/{}"
GAP = 10.0                                      # seconds between pages (robots.txt admits only search engines)
HOME = "Q347"                                   # Liechtenstein
FL_MUNICIPALITY = "Q203300"                     # municipality of Liechtenstein
LANGS = ("de", "en")
NEIGHBOURS = {"Feldkirch": ("Q183104", "Q40")}  # written bare: Feldkirch in Vorarlberg (not the ones in Alsace, Baden)
NUMBER = r"(?:ein|eine|einen|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf|\d+)"
SIBLINGS = re.compile(rf"(?:{NUMBER}\s+)?(?:Geschwister|Halbgeschwister|Brüder|Bruder|Halbbrüder|Halbbruder|"
                      r"Schwestern|Schwester|Halbschwestern|Halbschwester)")
CHILDREN = re.compile(rf"(?:{NUMBER}\s+)?(?:Kinder|Söhne|Sohn|Töchter|Tochter)")
LIST_ON = re.compile(r"(?:u\.\s*a\.|unter anderem|darunter|namentlich)\s+")
FAMILY_START = re.compile(r"(?:Sohn|Tochter|Kind|Bruder|Schwester|Halbbruder|Halbschwester|Vater|Mutter|Enkel\w*|"
                          r"Neffe|Nichte|Onkel|Tante|Cousin\w*|Vetter)\b")
KIN = re.compile(r"\b(?:Sohn|Söhne|Tochter|Töchter|Kind|Kinder|Enkel\w*|Neffe|Nichte|Onkel|Tante|Cousin\w*|Vetter|"
                 r"Bruder|Brüder|Schwester\w*|Vater|Vaters|Mutter|Eltern|Witwe\w*|Ehefrau|Ehemann|Gattin|Gatte|Mann|"
                 r"Frau|Schwager|Schwägerin|Schwieger\w*|Stief\w*|Gro(?:ss|ß)\w*|Urgro(?:ss|ß)\w*|Pflege\w*|Adoptiv\w*|"
                 r"Verwandt\w*|Halb\w*|verheiratet|verwitwet)\b")
LINK = re.compile(r"⟦L(\d+)⟧")


def candidates(n_):
    return s.linked("P13079", n_)


def page(ident):
    def fetch():
        text = s.polite_get(URL.format(urllib.parse.quote(ident, safe="/,()_'")), gap=GAP)
        if 'id="mw-content-text"' not in text:
            raise ValueError(f"{ident}: no article at this address ({len(text)} bytes)")
        return text
    return s.cached(WORK, ident + ".html", fetch)


# ---------------------------------------------------------------- text of the first paragraph

class Para:
    """The article's first paragraph: `text` with each link to another article written ⟦L<i>⟧ and other markup
    dropped, `links` the links as [(page name, link text)]. Page names hold commas and brackets ('Rheinberger,_Alois_
    (1836–1901)'), so they are kept out of the text that is cut at commas, brackets and sentences."""

    def __init__(self, t):
        body = t[t.find('<div class="mw-parser-output">'):]
        m = re.search(r"<p>(.*?)</p>", body, re.S)
        self.links = []
        text = re.sub(r'<a href="/([^"#?]+)"[^>]*>(.*?)</a>', self._token, m.group(1), flags=re.S) if m else ""
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        self.text = re.sub(r"\s+([,.;)])", r"\1", re.sub(r"\(\s+", "(", text))

    def _token(self, a):
        self.links.append((urllib.parse.unquote(html.unescape(a.group(1))), d.clean(a.group(2))))
        return f" ⟦L{len(self.links) - 1}⟧ "

    def plain(self, text):
        return LINK.sub(lambda m: self.links[int(m.group(1))][1], text)

    def links_in(self, part, lead=""):
        """[(page name, link text)] of the links outside brackets in a part, each only when the words before it
        (since the part's opening words `lead`, or the previous link; brackets left out) hold no word of kinship."""
        found, start = [], 0
        for m in LINK.finditer(part):
            before = part[:m.start()]
            if before.count("(") > before.count(")"):
                continue                             # inside brackets: '(→ [Transportwesen])'
            words = self.plain(unbracketed(part[start:m.start()])).strip()
            if start == 0 and lead and words.startswith(lead):
                words = words[len(lead):]
            start = m.end()
            if not KIN.search(words):
                found.append(self.links[int(m.group(1))])
        return found

    def first_link(self, part, lead=""):
        """The first link outside brackets of the part, when no word of kinship comes before it, else None."""
        outside = LINK.findall(unbracketed(part))
        got = self.links_in(part, lead)[:1]
        return got[0] if got and outside and got[0] == self.links[int(outside[0])] else None


def unbracketed(text):
    for _ in range(3):
        text = re.sub(r"\([^()]*\)", " ", text)
    return text


def top_level(text, seps):
    """The text cut at these characters where they stand outside brackets."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if ch in seps and depth <= 0:
            out.append(text[start:i])
            start = i + 1
    return [p.strip() for p in out + [text[start:]]]


def no_kin_before_link(p, part):
    """A list item that names no other kind of relative before its first link."""
    return not KIN.search(p.plain(unbracketed(part.split("⟦")[0])))


# ---------------------------------------------------------------- life data and places

def life(text):
    """(birth, death, parts) of the opening sentences (plain text): the words after the first '*' and '†' outside
    brackets, up to the next comma or sentence ('3.1.1831 Vaduz'), and the comma parts of that sentence."""
    found = {}
    for sym in "*†":
        depth = 0
        for i, ch in enumerate(text):
            depth += (ch == "(") - (ch == ")")
            if ch == sym and depth == 0:
                seg = top_level(text[i + 1:], ",;")[0]
                found[sym] = re.split(r"\s+[*†]|\.\s+(?=[A-ZÄÖÜ])", seg)[0].strip(" .")
                break
    at = min([i for i in (text.find("*"), text.find("†")) if i >= 0] or [0])
    sentence = re.split(r"\.\s+(?=[A-ZÄÖÜ])", text[at:])[0]
    return found.get("*"), found.get("†"), [p.strip(" .") for p in top_level(sentence, ",;")]


def split_date(raw):
    """'3.1.1831 Vaduz' -> ('3.1.1831', 'Vaduz'); '2.12. 1936 Vaduz'; 'um 1357 Vaduz (?)'; '1640/50 Eichstätt (D)'."""
    m = re.match(r"\s*(.*?\d{3,4}(?:/\d{1,4})?\??)(?=\s|$)\s*(.*)$", raw or "")
    return (m.group(1).strip(), m.group(2).strip(" .")) if m else (None, (raw or "").strip(" ."))


@functools.lru_cache(maxsize=None)
def namesakes(name, countries):
    """The settlements of these countries labelled with this name, with their classes."""
    if not countries or not d.PLAIN.match(name):
        return {}
    langs = LANGS + tuple(dict.fromkeys(l for c in countries for l in oebl.LOCAL.get(c, ())))
    lits = " ".join(f"{json.dumps(name, ensure_ascii=False)}@{lang}" for lang in langs + ("mul",))
    ks = " ".join(f"wd:{c}" for c in countries)
    rows = d.lookup(f"SELECT DISTINCT ?i ?c WHERE {{ VALUES ?l {{ {lits} }} VALUES ?k {{ {ks} }} "
                    "?i rdfs:label ?l ; wdt:P31 ?c ; wdt:P17 ?k . }")
    found = {}
    for r in rows:
        if len(r) == 2:
            found.setdefault(r[0], set()).add(r[1])
    return {q: cs for q, cs in found.items() if cs & d.settlement_classes()}


@functools.lru_cache(maxsize=None)
def coordinates(q):
    """The item's coordinates [(latitude, longitude)] (QLever writes 'POINT(', the Query Service 'Point(')."""
    found = []
    for r in d.lookup(f"SELECT ?c WHERE {{ wd:{q} wdt:P625 ?c }}"):
        m = re.search(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", r[0] if r else "", re.I)
        if m:
            found.append((float(m.group(2)), float(m.group(1))))
    return tuple(found)


def same_spot(a, b, km=3.0):
    """Two items of one name within 3 km of each other: one place (a GeoNames copy of a village, say)."""
    pa, pb = coordinates(a), coordinates(b)
    return bool(pa and pb) and min(n.km(x, y) for x in pa for y in pb) <= km


def only_one(name, q, countries, within=None):
    """q, unless another settlement of that name lies in the same country (or canton, land), neither lying in the
    other nor within 3 km of it, and q is not the one city among them: 'Ebersdorf (A)' names one of several
    Ebersdorfs in Austria, 'Türkenfeld (Bayern)' one of two in Bavaria."""
    found = namesakes(name, tuple(countries))
    others = [o for o in found if o != q]
    if not others or FL_MUNICIPALITY in found.get(q, set()):
        return q                    # one of Liechtenstein's 11 municipalities ('Triesen'; the other 'Triesen' items
                                    # there are bare copies with no coordinates and no place they lie in)
    d.place_containers([q] + others + ([within] if within else []))
    rivals = [o for o in others if (not within or d.lies_in(o, within) or not d._up.get(o))
              and not (d.lies_in(o, q) or d.lies_in(q, o)) and not same_spot(o, q)]
    city = lambda o: bool(found.get(o, set()) & set(d.CITY_CLASSES))
    return q if not rivals or (city(q) and not any(city(o) for o in rivals)) else None


def place(raw, label, notes):
    """(item or None, the country whose calendar applies) for a place as the eHLFL writes it."""
    text = re.sub(r"\s*\((?:Spital|Krankenhaus)\)$", "", d.clean(raw)).strip(" .,")
    text = re.sub(r"^(?:Spital|Krankenhaus)\s+", "", text)          # 'Spital Grabs (SG)': born in Grabs
    if not text or text.lower() in ("unbekannt", "?"):
        return None, HOME
    if "?" in text:
        notes.append(f"{label} '{text}' uncertain; not used")
        return None, HOME
    if text in NEIGHBOURS:
        return NEIGHBOURS[text]
    m = re.fullmatch(r"([^()]+?)\s*\(([^()]+)\)", text)
    parts = [p.strip() for p in text.split(",")]
    if m:
        name, where, given = m.group(1).strip(), m.group(2).strip(), True
        region = ("country", (HOME,)) if re.fullmatch(r"Gemeinde\s+\S.*", where) else oebl.region_of(where)
    elif len(parts) == 2:
        name, region, given = parts[0], oebl.region_of(parts[1]), True
    else:
        name, region, given = text, (("country", (HOME,)) if len(parts) == 1 else None), False
    countries = tuple((region[2] if region[0] == "within" else region[1]) if region else ())
    calendar = countries[0] if len(countries) == 1 else HOME
    q = oebl.resolve(name, region, given, LANGS) if region and not re.search(r"[(),]", name) else None
    if q and not given and q not in namesakes(name, countries):     # a well-known city outside Liechtenstein
        rows = d.lookup(f"SELECT ?k WHERE {{ wd:{q} wdt:P17 ?k }}")
        calendar = rows[0][0] if len(rows) == 1 else HOME
    elif q:
        q = only_one(name, q, countries, region[1] if region[0] == "within" else None)
    if not q:
        notes.append(f"{label} '{text}' not matched to one place item")
    return q, calendar


# ---------------------------------------------------------------- relatives

def family_links(p):
    """[(relation, page name, link text)] of the relatives the first paragraph links, as the docstring says."""
    found = []
    text = p.text
    marriage = text.find("⚭")
    before, after = (text[:marriage], text[marriage:]) if marriage >= 0 else (text, "")
    block = []                  # the family sentences, which follow the occupation and life data: a later sentence
    for i, sentence in enumerate(d.sentences(before)):      # of the account ('Sohn [X] übernahm ...') is not read
        if FAMILY_START.match(sentence.strip()) and (block or i <= 3):
            block.append(sentence.strip())
        elif block:
            break
    for sentence in block:
        parts = top_level(top_level(sentence, ";")[0], ",")
        head = parts[0]
        if re.match(r"(?:Sohn|Tochter|Kind)\b", head):
            found += parents_and_siblings(p, parts)
        sib = re.match(r"(?:Bruder|Schwester|Halbbruder|Halbschwester)\s+(?:des|der|von)\b", head)
        if sib:                                       # 'Schwester der Margaretha und des [Johann]'
            found += [("sibling", *x) for x in p.links_in(head, sib.group(0))]
        par = re.match(r"(?:Vater|Mutter)\s+(?:des|der|von)\b", head)
        if par:                                       # 'Vater des [X] und der [Y]'
            found += [("child", *x) for x in p.links_in(head, par.group(0))]
    for segment in re.split(r"(?=⚭)", after):
        if segment.strip():
            found += spouses_and_children(p, d.sentences(segment.strip())[0])
    return list(dict.fromkeys(found))


def parents_and_siblings(p, parts):
    """'Sohn des [Vater] und der [Mutter], geb. X, zehn Geschwister, u.a. [A], [B] und [C]'."""
    found, state = [], "parents"
    head = parts[0]
    lead = re.match(r"(?:Sohn|Tochter|Kind)\b(?:\s+(?:des|der|eines|einer|von))?", head).group(0)
    split = re.search(r"\s+und\s+(?:der|von)\s+", head)
    father, mother = (head[:split.start()], head[split.end():]) if split else (head, "")
    for kind, part, opening in (("father", father, lead), ("mother", mother, "")):
        got = p.first_link(part, opening)
        if got:
            found.append((kind, *got))
    for part in parts[1:]:
        opening = re.match(r"und\s+(?:der|von)\s+", part)
        if state == "parents" and opening:            # 'Sohn des A, von B, und der C'
            got = p.first_link(part, opening.group(0).strip())
            if got:
                found.append(("mother", *got))
        elif SIBLINGS.fullmatch(p.plain(part)):
            state = "siblings"
        elif state == "siblings" and (LIST_ON.match(part) or "⟦" in part) and no_kin_before_link(p, LIST_ON.sub("", part)):
            found += [("sibling", *x) for x in p.links_in(LIST_ON.sub("", part))]
        elif "⟦" in part or KIN.search(p.plain(part)):
            state = None                              # another relative's description ('Tochter des [Alois]')
    return found


def spouses_and_children(p, sentence):
    """'⚭ 1) 15.7.1850 [X] (...), sechs Kinder, 2) ... ', 'vier Kinder, u.a. [A] sowie [B]'; the in-laws described
    after the spouse ('Tochter des [Anton]') are not used, nor anything after a ';'."""
    found, state = [], None
    for i, part in enumerate(top_level(top_level(sentence, ";")[0], ",")):
        if i == 0 or re.match(r"\d\)\s", part):       # a marriage: '⚭ [1)] date [spouse]', '2) date [spouse]'
            opening = re.match(r"(?:⚭\s*)?(?:\d\)\s*)?(?:\d{1,2}\.\s*\d{1,2}\.\s*)?(?:\d{4}\s*)?", part).group(0).strip()
            got = p.first_link(part, opening)
            if got:
                found.append(("spouse", *got))
            state = "spouse"
        elif CHILDREN.fullmatch(p.plain(part)):
            state = "children"
        elif state == "children" and (LIST_ON.match(part) or "⟦" in part) and no_kin_before_link(p, LIST_ON.sub("", part)):
            found += [("child", *x) for x in p.links_in(LIST_ON.sub("", part))]
        elif "⟦" in part or KIN.search(p.plain(part)):
            state = None                              # the spouse's parents or siblings
    return found


def year_born(q):
    years = {int(m.group(1)) for r in d.lookup(f"SELECT ?b WHERE {{ wd:{q} wdt:P569 ?b }}")
             if r and (m := re.match(r"(\d{4})-", r[0]))}
    return years.pop() if len(years) == 1 else None


def is_human(q):
    return bool(d.lookup(f"SELECT ?c WHERE {{ wd:{q} wdt:P31 wd:Q5 . BIND(1 AS ?c) }}"))


def relatives(p, born, notes):
    facts, seen = [], set()
    for kind, target, name in family_links(p):
        q = s.item_with("P13079", target)
        if not q:
            notes.append(f"{kind} '{name}': article {target} is on no single item; not used")
            continue
        if (kind, q) in seen:
            continue
        seen.add((kind, q))
        if not is_human(q):
            notes.append(f"{kind} '{name}': the item of article {target} ({q}) is not a human; not used")
            continue
        if not n.years_fit(kind, born, year_born(q)):
            notes.append(f"{kind} '{name}' ({q}): the years of birth do not fit; not used")
            continue
        f = n.relative_fact(kind, q, notes, name)
        if f:
            facts.append(f)
    return facts


# ---------------------------------------------------------------- the entry

def title_of(t, ident):
    m = re.search(r'<span class="mw-page-title-main">(.*?)</span>', t)
    name = d.clean(m.group(1)) if m else ident.replace("_", " ")
    name = re.sub(r"\s*\([^()]*\d[^()]*\)$", "", name)                # 'Liechtenstein, Alfred von (1875–1930)'
    last, sep, first = name.partition(",")
    return f"{first.strip()} {last.strip()}" if sep and first.strip() else name


def gnd_numbers(t):
    m = re.search(r'id="Normdaten".*?</h2>(.*?)(?:<h2|<div class="printfooter")', t, re.S)
    return sorted(set(re.findall(r"d-nb\.info/gnd/([0-9X-]+)", m.group(1)))) if m else []


def read(ident):
    t = page(ident)
    title = title_of(t, ident)
    p = Para(t)
    lead = p.plain(p.text)
    if not re.search(r"(?:^|[.\s])(?:\*\s*(?:um\s+|ca\.\s*)?\d|†\s*\d|Erwähnt\b|Sohn\s|Tochter\s)|\.\s+Von\s+[A-ZÄÖÜ]", lead):
        raise LookupError(f"eHLFL {ident}: the article does not open as a person's ('{lead[:80]}')")
    facts, notes, born = [], [], None
    family_at = min([i for i in (lead.find("Sohn "), lead.find("Tochter "), lead.find("⚭")) if i >= 0] or [len(lead)])
    birth, death, parts = life(lead[:family_at])
    for raw, (dpid, dlabel), (ppid, plabel) in ((birth, ("P569", "date of birth"), ("P19", "place of birth")),
                                                (death, ("P570", "date of death"), ("P20", "place of death"))):
        if not raw:
            continue
        when, where = split_date(raw)
        q, calendar = place(where, plabel, notes) if where else (None, HOME)
        if when:
            f = n.german_date(when, dpid, dlabel, notes, country=calendar)
            if f:
                facts.append(f)
                if f["pid"] in ("P569", "P1636"):
                    born = int(f["value"]["value"]["time"][1:5])
        if q:
            facts.append(d.fact(ppid, d.item_value(q), plabel))
    for word in parts:
        if word in n.FAITHS:
            facts.append(d.fact("P140", d.item_value(n.FAITHS[word]), "religion"))
    first = re.split(r"\.\s+|\s+(?=[*†]\s*(?:um\s+)?\d)", lead, maxsplit=1)[0].strip(" .")
    if first and not first.startswith(("*", "†")) and first != lead.strip(" ."):
        facts += n.occupation_facts([w for w in re.split(r",\s*|\s+und\s+", first) if w.strip()], notes)
        facts = without_coarser(facts, "P13079", ident, notes)    # not 'teacher' where the item has a finer one
    gnds = gnd_numbers(t)
    if len(gnds) == 1:
        f = n.gnd_fact(gnds[0], notes)
        if f:
            facts.append(f)
    elif gnds:
        notes.append(f"several GND IDs ({', '.join(gnds)}); none used")
    facts += relatives(p, born, notes)
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
