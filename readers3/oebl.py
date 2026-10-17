"""Österreichisches Biographisches Lexikon 1815–1950 (ÖBL, P6194), on biographien.ac.at.

An entry's address, oebl_<ID>.xml, holds an XML record that the server hands out rendered as a page. The page keeps the
record's fields: the life years, places and occupations (DC.Description: '(1817-Kratzau, Böhmen (Chrastava,
Tschechien)1893)Stuttgart, Württemberg (Deutschland)Sänger und Komponist': each place as the ÖBL names it, with its
present name and country in brackets), the person's GND number (Weblinks) and the life-data line that opens the
article ('Geb. Kratzau, Böhmen (Chrastava, Tschechien), 30. 7. 1817; gest. Stuttgart, ..., 9. 6. 1893.'), which gives
the full dates. Relatives are taken only where the online edition links them to their own entry ('Vater von →Marian
Alois Franke'). One page every 3 seconds under the bot's name (robots.txt allows /oebl/ and sets no delay); the APIS
site, whose robots.txt bars bots, is not used. The German place helpers, the GND check and the occupation mapping here
serve wgw.py and ehlfl.py as well.
"""
import html
import json
import re

import dictionaries as d
import sources3 as s

WORK = "oebl"
LANGS = ("de", "en")
AUSTRIA = "Q40"

# ---------------------------------------------------------------- places named in German (ÖBL, WGW, eHLFL)

# Regions named after a place that are present-day states, Länder, regions or cantons: a town must lie inside them.
WITHIN = {"NÖ": "Q42497", "Niederösterreich": "Q42497", "OÖ": "Q41967", "Oberösterreich": "Q41967",
          "Stmk.": "Q41358", "Steiermark": "Q41358", "Sbg.": "Q43325", "Ktn.": "Q37985", "Kärnten": "Q37985",
          "Tirol": "Q42880", "Vbg.": "Q38981", "Vorarlberg": "Q38981", "Bgld.": "Q43210", "Burgenland": "Q43210",
          "Bayern": "Q980", "Baden-Württemberg": "Q985", "Sachsen": "Q1202", "Hessen": "Q1199",
          "Nordrhein-Westfalen": "Q1198", "Niedersachsen": "Q1197", "Rheinland-Pfalz": "Q1200", "Thüringen": "Q1205",
          "Toskana": "Q1273", "Lombardei": "Q1210", "Venetien": "Q1243", "Südtirol": "Q15124",
          "Trentino-Südtirol": "Q1237", "Friaul-Julisch Venetien": "Q1250", "Marken": "Q1279", "Apulien": "Q1447"}
WITHIN_COUNTRY = {**{k: "Q40" for k in ("NÖ", "Niederösterreich", "OÖ", "Oberösterreich", "Stmk.", "Steiermark", "Sbg.",
                                        "Ktn.", "Kärnten", "Tirol", "Vbg.", "Vorarlberg", "Bgld.", "Burgenland")},
                  **{k: "Q183" for k in ("Bayern", "Baden-Württemberg", "Sachsen", "Hessen", "Nordrhein-Westfalen",
                                         "Niedersachsen", "Rheinland-Pfalz", "Thüringen")},
                  **{k: "Q38" for k in ("Toskana", "Lombardei", "Venetien", "Südtirol", "Trentino-Südtirol",
                                        "Friaul-Julisch Venetien", "Marken", "Apulien")}}
# Countries written short, and historical lands that lie in one or a few present-day countries: a town of the name
# must then be the only one there.
COUNTRIES = {"A": ("Q40",), "D": ("Q183",), "BRD": ("Q183",), "CH": ("Q39",), "I": ("Q38",), "F": ("Q142",),
             "CZ": ("Q213",), "SK": ("Q214",), "H": ("Q28",), "SLO": ("Q215",), "HR": ("Q224",), "PL": ("Q36",),
             "UA": ("Q212",), "RO": ("Q218",), "FL": ("Q347",), "USA": ("Q30",), "Serbien und Montenegro": ("Q403", "Q236"),
             "Böhmen": ("Q213",), "Mähren": ("Q213",), "Krain": ("Q215",), "Dalmatien": ("Q224",), "Slawonien": ("Q224",),
             "Westfalen": ("Q183",), "Baden": ("Q183",), "Markgrafschaft Baden": ("Q183",), "Württemberg": ("Q183",),
             "Siebenbürgen": ("Q218",), "Lombardo-Venetien": ("Q38",), "Galizien": ("Q36", "Q212"),
             "Bukowina": ("Q218", "Q212"), "Banat": ("Q218", "Q403", "Q28"), "Batschka": ("Q403", "Q28")}
LOCAL = {"Q213": ("cs",), "Q214": ("sk",), "Q28": ("hu",), "Q215": ("sl",), "Q224": ("hr",), "Q38": ("it",),
         "Q36": ("pl",), "Q212": ("uk",), "Q218": ("ro",), "Q403": ("sr",), "Q236": ("sr",), "Q142": ("fr",),
         "Q39": ("fr", "it"), "Q32": ("lb", "fr")}
NOT_TOWN = re.compile(r"\?|\d|[()\[\]]|\bbei\b|\bb\.\s|\bnahe\b|\bunweit\b|\bKrankenhaus\b|\bSpital\b|"
                      r"^(?:Schloss|Burg|Stift|Kloster|Gut|Hof|Sanatorium)\b", re.I)


def region_of(name):
    """A region or country as written after a place ('NÖ', 'Böhmen', 'GR', 'Brasilien'): what sources3.town() takes
    as its region, or None."""
    name = name.strip()
    if name in WITHIN:
        return ("within", WITHIN[name], (WITHIN_COUNTRY[name],))
    if name in d.CANTON_ITEMS:
        return ("within", d.CANTON_ITEMS[name], ("Q39",))
    if name in COUNTRIES:
        return ("country", COUNTRIES[name])
    return s.region_named(name, LANGS) if d.PLAIN.match(name) else None


def unabbreviated(name):
    """'Ried i. Innkreis' -> 'Ried im Innkreis', 'Frankenburg a. Hausruck' -> 'Frankenburg am Hausruck'."""
    name = re.sub(r"\ba\.\s*d\.\s*", "an der ", name)
    return re.sub(r"\bi\.\s*(?=[A-ZÄÖÜ])", "im ", re.sub(r"\ba\.\s*(?=[A-ZÄÖÜ])", "am ", name))


def resolve(name, region, given=True, langs=LANGS):
    """The one town so named in the region; 'Merano/Meran' when its spellings lead to one town. None when a name is
    not a plain town name ('Furth b. Göttweig', 'Schloss Trebnitz') or no single town fits."""
    names = [unabbreviated(n.strip()) for n in name.split("/") if n.strip()]
    if not names or any(NOT_TOWN.search(n) for n in names):
        return None
    countries = region[2] if region[0] == "within" else region[1]
    langs = langs + tuple(dict.fromkeys(l for c in countries for l in LOCAL.get(c, ()) if l not in langs))
    found = {s.town(n, langs, region, given) for n in names} - {None}
    return found.pop() if len(found) == 1 else None


def german_place(raw, home, langs=LANGS):
    """A place as the German-language works write it: 'Wien', 'Pilsen, Böhmen', 'Says (GR)', 'Türkenfeld (Bayern)',
    'Ebersdorf (A)', 'Bendern (Gemeinde Gamprin)': a town of the region or country named, else (none named) of the
    work's home country or a well-known city anywhere. Hospitals ('Vaduz (Spital)') count as their town; castles,
    'X bei Y' and places in historical lands that span several countries need a single match."""
    text = re.sub(r"\s*\((?:Spital|Krankenhaus)\)$", "", d.clean(raw))
    m = re.fullmatch(r"([^()]+?)\s*\(([^()]+)\)", text)
    if m:
        name, where = m.group(1).strip(), m.group(2).strip()
        region = ("country", (home,)) if re.fullmatch(r"Gemeinde\s+\S.*", where) else region_of(where)
        return resolve(name, region, True, langs) if region and "," not in name else None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 2:
        region = region_of(parts[1])
        return resolve(parts[0], region, True, langs) if region else None
    if len(parts) == 1 and parts[0]:
        return resolve(parts[0], ("country", (home,)), False, langs)
    return None


# ---------------------------------------------------------------- the GND number of a person (ÖBL, WGW, eHLFL)

GND_ID = re.compile(r"1[01234]?\d{7}[0-9X]|[47]\d{6}-\d|[1-9]\d{0,7}-[0-9X]|3\d{7}[0-9X]")   # P227's format


def lobid(gid):
    """What lobid-gnd, the GND's documented API, says of a record (a merged record answers as the one it went into)."""
    got = json.loads(s.polite_get(f"https://lobid.org/gnd/{gid}.json", gap=1.0))
    return {k: got.get(k) for k in ("gndIdentifier", "type", "preferredName", "dateOfBirth", "dateOfDeath")}


def gnd_fact(work, gid, ours, born, died, notes):
    """The person's GND number, when lobid-gnd knows it as a record of one person whose life years fit the entry's,
    and no other item carries it. A record merged into another is given as the one it went into (as in task 2)."""
    gid = (gid or "").strip()
    if not GND_ID.fullmatch(gid):
        notes.append(f"GND ID '{gid}' is not in the GND's format; not copied")
        return None
    try:
        rec = json.loads(s.cached(work, f"gnd-{gid}.json", lambda: json.dumps(lobid(gid), ensure_ascii=False)))
    except (ValueError, OSError):
        notes.append(f"GND ID {gid} could not be checked with lobid-gnd; not copied")
        return None
    now = rec.get("gndIdentifier") or ""
    if "DifferentiatedPerson" not in (rec.get("type") or []):
        notes.append(f"GND ID {gid} is no record of one person ({', '.join(rec.get('type') or [])}); not copied")
        return None
    for mine, theirs, what in ((born, rec.get("dateOfBirth"), "born"), (died, rec.get("dateOfDeath"), "died")):
        y = re.match(r"(\d{4})", (theirs or [""])[0])
        if mine and y and abs(int(y.group(1)) - mine) >= 2:
            notes.append(f"GND ID {gid} is of someone {what} {y.group(1)}, not {mine}; not copied")
            return None
    owners = {r[0] for r in d.lookup(f'SELECT ?i WHERE {{ ?i wdt:P227 "{now}" }}')}
    if owners - {ours}:
        notes.append(f"GND ID {now} is already on {', '.join(sorted(owners - {ours}))}; not copied")
        return None
    if now != gid:
        notes.append(f"GND ID {gid} was merged into {now}; {now} given")
    return d.fact("P227", d.string_value(now), "GND ID")


# ---------------------------------------------------------------- occupations named in German (ÖBL, WGW)

OCCUPATIONS = {                       # German word -> (item, its English label, checked on Wikidata 2026-10-16)
    "Schriftsteller": ("Q36180", "writer"), "Journalist": ("Q1930187", "journalist"), "Lehrer": ("Q37226", "teacher"),
    "Maler": ("Q1028181", "painter"), "Komponist": ("Q36834", "composer"), "Bildhauer": ("Q1281618", "sculptor"),
    "Architekt": ("Q42973", "architect"), "Politiker": ("Q82955", "politician"), "Arzt": ("Q39631", "physician"),
    "Ärztin": ("Q39631", "physician"), "Mediziner": ("Q39631", "physician"), "Schauspieler": ("Q33999", "actor"),
    "Sänger": ("Q177220", "singer"), "Opernsänger": ("Q2865819", "opera singer"), "Philosoph": ("Q4964182", "philosopher"),
    "Psychologe": ("Q212980", "psychologist"), "Literaturhistoriker": ("Q13570226", "literary historian"),
    "Historiker": ("Q201788", "historian"), "Archäologe": ("Q3621491", "archaeologist"),
    "Kunsthändler": ("Q173950", "art dealer"), "Ingenieur": ("Q81096", "engineer"), "Dramatiker": ("Q214917", "playwright"),
    "Drehbuchautor": ("Q28389", "screenwriter"), "Industrieller": ("Q6606110", "industrialist"),
    "Musiker": ("Q639669", "musician"), "Theologe": ("Q1234713", "theologian"), "Jurist": ("Q185351", "jurist"),
    "Mathematiker": ("Q170790", "mathematician"), "Maschinenbauingenieur": ("Q1906857", "mechanical engineer"),
    "Agronom": ("Q1781198", "agronomist"), "Ethnologe": ("Q1371378", "ethnologist"),
    "Kirchenhistoriker": ("Q1743122", "church historian"), "Landwirt": ("Q131512", "farmer"),
    "Übersetzer": ("Q333634", "translator"), "Fotograf": ("Q33231", "photographer"), "Internist": ("Q15924224", "internist"),
    "Naturwissenschaftler": ("Q18805", "naturalist"), "Designer": ("Q5322166", "designer"),
    "Grafiker": ("Q1925963", "graphic artist"), "Graphiker": ("Q1925963", "graphic artist"),
    "Beamter": ("Q212238", "civil servant"), "Botaniker": ("Q2374149", "botanist"),
    "Psychoanalytiker": ("Q3410028", "psychoanalyst"),
}
_checked = {}


def checked(mapping):
    """The mapping without the items whose English label is no longer the one checked (like task 2's
    check_occupations), read once per run."""
    key = id(mapping)
    if key not in _checked:
        got = d.items(sorted({q for q, _ in mapping.values()}))
        _checked[key] = {w: (q, label) for w, (q, label) in mapping.items()
                         if got.get(q, {}).get("labels", {}).get("en", {}).get("value") == label}
    return _checked[key]


def occupation_facts(words, notes, mapping=OCCUPATIONS):
    """P106 for each word of the work's occupation field the mapping knows; a feminine form counts as its masculine
    ('Politikerin', 'Psychologin'). Others are noted."""
    known, facts = checked(mapping), []
    for word in words:
        word = word.strip(" ;,.")
        if not word:
            continue
        forms = [word] + ([word[:-2], word[:-2] + "e"] if word.endswith("in") else [])
        hit = next((known[f][0] for f in forms if f in known), None)
        if hit:
            facts.append(d.fact("P106", d.item_value(hit), "occupation"))
        else:
            notes.append(f"occupation '{word}' not mapped")
    return facts


def split_words(text):
    """'Schriftsteller, Übersetzer und Abgeordneter' -> the three occupations."""
    return [w for w in re.split(r",\s*|\s+und\s+|;\s*", d.clean(text or "")) if w.strip()]


# ---------------------------------------------------------------- the ÖBL entry

URL = "https://www.biographien.ac.at/oebl/oebl_{}.xml"
DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")


def candidates(n):
    return s.linked("P6194", n)


def page(ident):
    def fetch():
        text = s.polite_get(URL.format(ident), gap=3.0)
        if 'id="Schlagwort"' not in text:
            raise ValueError(f"{ident}: no ÖBL entry at this address")
        return text
    return s.cached(WORK, ident + ".html", fetch)


def outer_brackets(spec):
    """'Meran (Südtirol) (Merano/Meran, Italien)' -> ('Meran (Südtirol)', ['Merano/Meran', 'Italien']); the parts
    of the last bracket, which holds the present name and country."""
    spec = spec.strip()
    if not spec.endswith(")"):
        return spec, []
    depth = 0
    for i in range(len(spec) - 1, -1, -1):
        depth += (spec[i] == ")") - (spec[i] == "(")
        if depth == 0:
            return spec[:i].strip(), [p.strip() for p in d.split_outside_parens(spec[i + 1:-1], ",")]
    return spec, []


def oebl_place(spec):
    """A place of the ÖBL's structured field: 'Kratzau, Böhmen (Chrastava, Tschechien)' is Chrastava in the Czech
    Republic; 'Filzmoos (?, Sbg.)' Filzmoos in Salzburg; 'Laxenburg (NÖ)' Laxenburg in Lower Austria; 'Budapest
    (Ungarn)' Budapest in Hungary; 'Wien' Vienna (a town of Austria, or a well-known city)."""
    old, now = outer_brackets(spec)
    inner = re.search(r"\(([^()]+)\)", old.split(",")[0])
    if inner and not region_of(inner.group(1)):         # 'Irig (Iriški Venac), ...': a place near or in the town
        return None
    if not now:                                         # 'Wien': no country named
        return resolve(old.split(",")[0], ("country", (AUSTRIA,)), False) if "," not in old else None
    if len(now) == 1:                                   # '(NÖ)', '(Ungarn)', '(Tschechien)': the region only
        region = region_of(now[0])
        return resolve(old.split(",")[0], region) if region else None
    name, region = now[0], region_of(now[-1])
    if name == "?" and now[-1] == "?":                 # 'Rippweiler (Luxemburg) (?, ?)': the name as the ÖBL gives it
        return oebl_place(old) if old.endswith(")") else None
    if name == "?":                                     # 'Fumane, Lombardo-Venetien (?, Italien)'
        name = old.split(",")[0]
    return resolve(name, region) if region else None


def life_date(segment):
    """The date that ends a part of the life-data line ('Kratzau, Böhmen (Chrastava, Tschechien), 30. 7. 1817'):
    (y, m, d), (y, None, None), or the date's words when they are not a plain date ('16. oder 17. 12. 1820')."""
    text = segment.split(";")[0][:240]
    for _ in range(2):
        text = re.sub(r"\([^()]*\)", "", text)
    year = re.search(r"\d{4}", text)
    if not year:
        return None
    head = text[:year.end()]
    words = re.split(r",(?![^()]*\))", head)[-1].strip()
    m = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", words)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    if re.fullmatch(r"\d{4}", words):
        return int(words), None, None
    return words


def life_line(first):
    """(birth, death, baptism) from the article's first lines: '* Schönau, ..., 28. 7. 1838; † Meran (Südtirol),
    3. 4. 1921.' or 'Geb. ..., 30. 7. 1817; gest. ..., 9. 6. 1893.' (lower-case 'geb.' is a maiden name)."""
    found = {}
    for key, rx in (("birth", r"(?:^|\s)(?:\*|Geb\.)\s+"), ("baptism", r"(?:^|\s)(?:Get\.|Getauft)\s+"),
                    ("death", r"(?:^|[;\s])\s*(?:†|[Gg]est\.)\s+")):
        m = re.search(rx, first)
        if m:
            found[key] = life_date(first[m.end():])
    return found.get("birth"), found.get("death"), found.get("baptism")


KIN = [("parent", re.compile(r"\b(?:Sohn|Tochter|Kind)\s+(?:des|der|von|eines|einer)\b")),
       ("child", re.compile(r"\b(?:Vater|Mutter)\s+(?:u\.\s*a\.\s+)?(?:des|der|von|zweier|dreier|mehrerer)\b")),
       ("sibling", re.compile(r"\b(?:Bruder|Schwester|Halbbruder|Halbschwester)\s+(?:u\.\s*a\.\s+)?(?:des|der|von)\b")),
       ("spouse", re.compile(r"\b(?:verheiratet|verehelicht|verh\.)\s+mit\b|\bab\s+\d{4}\s+(?:verheiratet\s+)?mit\b")),
       ("other", re.compile(r"\b(?:Enkel(?:in)?|Neffe|Nichte|Onkel|Tante|Cousin(?:e)?|Vetter|Schwager|Schwägerin|"
                            r"Schwieger\w*|Gro(?:ß|ss)\w*|Urgro(?:ß|ss)\w*|Urenkel\w*|Stief\w*|Pflege\w*|Adoptiv\w*|"
                            r"Schüler(?:in)?|Lehrer(?:in)?|Freund(?:in)?|Kolleg\w*)\b"))]
FAMILY_NOUN = re.compile(r"\b(?:Vaters?|Mutter|Bruders?|Schwester|Sohn(?:es)?|Tochter|Mann(?:es)?|Frau|Gatt(?:e|en|in)|"
                         r"Witwe[rn]?|Familie|Eltern)\b")   # 'Bruder des Vaters →X': X is no sibling
REL_PIDS = {"father": "P22", "mother": "P25", "spouse": "P26", "child": "P40", "sibling": "P3373"}


def linked_relatives(lemma_html):
    """[(relation, item)] for relatives the family lines link to their own ÖBL entry: 'Sohn des →A und der →B',
    'Vater von →C', 'Bruder des →D', 'verheiratet mit →E'. A link counts only in the same clause as its word of
    kinship, outside brackets and before any other relation ('Schüler →Edmund Eyslers') or relative clause."""
    text = re.sub(r'<a [^>]*href="[^"]*/oebl/oebl_([^"]+?)\.xml"[^>]*>(.*?)</a>',
                  lambda m: f"⟦{m.group(1)}⟧", lemma_html, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.split(r"\s[–-]\s", text, maxsplit=1)[0]                 # the family lines end where the account begins
    found = []
    for unit in d.split_outside_parens(text, ";"):
        for clause in re.split(r",(?![^()]*\))\s*(?=(?:der|die|das|dessen|deren|welche[rs]?|wo|als)\b)", unit)[:1]:
            marks = sorted((m.start(), m.end(), kind) for kind, rx in KIN for m in rx.finditer(clause))
            depth = 0
            for i, ch in enumerate(clause):
                depth += (ch == "(") - (ch == ")")
                if ch != "⟦" or depth > 0:
                    continue
                start, end, kind = next(((a, b, k) for a, b, k in reversed(marks) if a < i), (0, 0, None))
                ident = clause[i + 1:clause.index("⟧", i)]
                if kind in (None, "other") or FAMILY_NOUN.search(clause[end:i]):
                    continue
                q = s.item_with("P6194", ident)
                if not q:
                    continue
                if kind == "parent":
                    kind = {"Q6581097": "father", "Q6581072": "mother"}.get(d.sex(q))
                if kind:
                    found.append((kind, q))
    return list(dict.fromkeys(found))


def read(ident):
    t = page(ident)
    meta = lambda name: html.unescape((re.search(rf'name="{name}" content="([^"]*)"', t) or [None, ""])[1])
    title = (meta("DC.Title") or ident).split(";")[0].strip()          # 'Szántó, Simon; Ps. Dr. Unbefangen, ...'
    desc = meta("DC.Description")
    head = d.clean(" ".join(re.findall(r'<span id="Schlagwort"[^>]*>(.*?)</span>', t, re.S)))
    lemma = re.search(r'<span class="lemmatext">(.*?)</span>', t, re.S)
    first = d.clean(lemma.group(1)) if lemma else ""
    facts, notes = [], []
    years = re.search(r"_(\d{4})_(\d{4})$", ident)
    born_id, died_id = (int(years.group(1)), int(years.group(2))) if years else (None, None)

    # the structured field: '(1838-Schönau, Baden (...)1921)Meran (Südtirol) (Merano/Meran, Italien)Industrieller'
    occupation = (re.search(r"\(\s*[^)]*\d{4}\s*\)\s*,\s*(.+)$", head) or [None, ""])[1].strip()
    m = re.fullmatch(r"\((\d{4})?\s*[-–]\s*(.*?)(\d{4})\)(.*)", desc, re.S)
    birth_spec = death_spec = ""
    born_desc = died_desc = None
    if m:
        born_desc, birth_spec = m.group(1) and int(m.group(1)), m.group(2).strip()
        died_desc, rest = m.group(3) and int(m.group(3)), m.group(4).strip()
        death_spec = rest[:-len(occupation)].strip() if occupation and rest.endswith(occupation) else ""
        if rest and not death_spec and not (occupation and rest == occupation):
            notes.append(f"place of death not told apart from the occupations in '{rest}'")
    else:
        notes.append("the life data field is not in the usual form; places not read")

    birth, death, baptism = life_line(first)
    for value, pid, label, check in ((birth, "P569", "date of birth", (born_desc, born_id)),
                                     (baptism, "P1636", "date of baptism", (born_desc, born_id)),
                                     (death, "P570", "date of death", (died_desc, died_id))):
        if value is None:
            continue
        if isinstance(value, str):
            notes.append(f"{label} '{value}' not used: not a plain date")
            continue
        if any(y and y != value[0] for y in check):
            notes.append(f"{label} {value[0]} not used: the entry's life years say {'/'.join(str(y) for y in check if y)}")
            continue
        facts.append(s.date_fact(pid, label, *value, country=AUSTRIA))

    for spec, pid, label in ((birth_spec, "P19", "place of birth"), (death_spec, "P20", "place of death")):
        if spec and spec != "?":
            q = oebl_place(spec)
            if q:
                facts.append(d.fact(pid, d.item_value(q), label))
            else:
                notes.append(f"{label} '{spec}' not matched to one place item")

    facts += occupation_facts(split_words(occupation), notes)

    gnds = sorted(set(re.findall(r'href="https?://d-nb\.info/gnd/([0-9X-]+)"', t)))
    if len(gnds) == 1:
        f = gnd_fact(WORK, gnds[0], s.item_with("P6194", ident), born_desc or born_id, died_desc or died_id, notes)
        if f:
            facts.append(f)
    elif len(gnds) > 1:
        notes.append(f"several GND IDs ({', '.join(gnds)}); none copied")

    if lemma:
        whole = re.search(r'<div id="Langtext">(.*?)<div id="Werke"', t, re.S)
        for rel, q in linked_relatives(whole.group(1) if whole else lemma.group(1)):
            facts.append(d.fact(REL_PIDS[rel], d.item_value(q), rel))
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
