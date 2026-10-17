"""Occupation words of the reference works -> Wikidata occupation items (P106 values), from checked maps.

The maps are work/occupations/occupation_map_de.tsv (German: the Sächsische Biografie, the Deutsche Biographie's NDB
and ADB index, the task-3 German readers) and occupation_map_nb.tsv (Norwegian: Store norske leksikon and Norsk
kunstnerleksikon, task 2). Each row was checked against the item's labels and description: 'exact' rows are the one
occupation item that is widely used as P106 (at least 100 items) and has the word as its label or alias; 'checked'
rows were chosen by hand, the reason in the note. Words left out on purpose (offices, titles, ranks of nobility,
religious orders, ambiguous or vague words) are listed with the reason in unmapped_de.tsv and unmapped_nb.tsv; a word
that is in neither map is simply unknown.

  occupation_qid("evangelischer Theologe", "de")  -> "Q1234713" (theologian)
  occupation_qid("Malerin", "de")                 -> "Q1028181" (painter)
  occupation_qid("Bischof von Meißen", "de")      -> "Q29182" (bishop)
  occupation_qid("norskamerikansk teolog", "nb")  -> "Q1234713"
  occupation_qid("Bürgermeister", "de")           -> None (an office: left out)

  python occupations.py de "katholische Theologin"     (prints the item and the map word it was found under)
"""
import csv
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent / "occupation_maps"
LANGS = ("de", "nb")


def _load(lang):
    rows = {}
    path = HERE / f"occupation_map_{lang}.tsv"
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row.get("word") and re.fullmatch(r"Q\d+", row.get("qid") or ""):
                rows[row["word"]] = row["qid"]
    return rows


MAP = {lang: _load(lang) for lang in LANGS}
_INDEX = {lang: {unicodedata.normalize("NFC", w).casefold(): w for w in words} for lang, words in MAP.items()}

# ------------------------------------------------------------------ what a word may be reduced to

# Adjectives that only say which church, country, region, party or court a person belonged to (or describe the work
# without changing the occupation): 'evangelischer Theologe', 'preußischer General', 'k. k. Feldmarschall',
# 'freier Schriftsteller'. Stripped in any inflection. Not stripped: words that change the occupation, such as
# 'klassischer' (Klassischer Philologe is mapped itself), 'technischer' (technischer Zeichner), 'apostolischer'
# (apostolischer Vikar, a bishop), 'bildender', 'geheimer', 'wissenschaftlicher'.
DE_PREFIX_STEMS = (
    "evangelisch", "katholisch", "römisch-katholisch", "altkatholisch", "christkatholisch", "lutherisch",
    "evangelisch-lutherisch", "reformiert", "evangelisch-reformiert", "protestantisch", "jüdisch", "anglikanisch",
    "orthodox", "griechisch-orthodox", "russisch-orthodox", "pietistisch", "calvinistisch", "mennonitisch",
    "baptistisch", "methodistisch", "freikirchlich", "israelitisch", "herrnhutisch",
    "deutsch", "preußisch", "österreichisch", "bayerisch", "bayrisch", "sächsisch", "kursächsisch", "schweizerisch",
    "württembergisch", "niederländisch", "holländisch", "hessisch", "kurhessisch", "hannoverisch", "hannoversch",
    "hannöverisch", "brandenburgisch", "badisch", "siebenbürgisch", "braunschweigisch", "braunschweig-lüneburgisch",
    "mecklenburgisch", "dänisch", "schwedisch", "pommerisch", "pommersch", "schlesisch", "russisch", "französisch",
    "kurpfälzisch", "pfälzisch", "hamburgisch", "oldenburgisch", "nassauisch", "böhmisch", "weimarisch",
    "livländisch", "kurländisch", "estländisch", "baltisch", "deutschbaltisch", "elsässisch", "schwäbisch",
    "westfälisch", "friesisch", "ostfriesisch", "bernisch", "gothaisch", "kurkölnisch", "kurmainzisch", "kurtrierisch",
    "schleswig-holsteinisch", "thüringisch", "anhaltisch", "lippisch", "lübeckisch", "bremisch", "englisch",
    "italienisch", "polnisch", "ungarisch", "tschechisch", "amerikanisch", "deutsch-amerikanisch", "deutschamerikanisch",
    "spanisch", "portugiesisch", "belgisch", "luxemburgisch", "liechtensteinisch", "tirolisch", "steirisch",
    "kärntnerisch", "salzburgisch", "mährisch", "ostpreußisch", "westpreußisch", "rheinisch", "fränkisch",
    "norwegisch", "finnisch", "britisch", "schottisch", "irisch", "kanadisch", "australisch", "israelisch",
    "sowjetisch", "kroatisch", "slowenisch", "slowakisch", "rumänisch", "griechisch", "türkisch", "japanisch",
    "chinesisch", "indisch", "brasilianisch", "argentinisch", "chilenisch", "mexikanisch",
    "sozialdemokratisch", "liberal", "nationalliberal", "linksliberal", "konservativ", "deutschkonservativ",
    "freikonservativ", "sozialistisch", "kommunistisch", "nationalsozialistisch", "demokratisch",
    "christdemokratisch", "christlich-sozial", "deutschnational", "linksradikal",
    "kaiserlich", "königlich", "kurfürstlich", "herzoglich", "fürstlich", "landgräflich", "markgräflich", "gräflich",
    "freischaffend", "frei", "praktisch", "politisch", "religiös", "theologisch", "philosophisch", "historisch",
    "medizinisch", "landwirtschaftlich", "humanistisch", "gelehrt", "lateinisch", "neulateinisch", "geistlich",
    "weiblich", "ordentlich", "außerordentlich", "vergleichend", "bekannt", "fahrend",
)
DE_PREFIX_WORDS = ("k. k.", "k. u. k.", "k.u.k.", "k.k.", "Schweizer", "Tiroler", "Herrnhuter")
DE_PREFIX = re.compile(r"^(?:(?:" + "|".join(re.escape(s) for s in sorted(DE_PREFIX_STEMS, key=len, reverse=True))
                       + r")(?:er|e|es|en|em)?|" + "|".join(re.escape(w) for w in DE_PREFIX_WORDS) + r")\s+(?=\S)",
                       re.I)
NB_PREFIX_WORDS = (
    "norsk", "dansk", "svensk", "islandsk", "tysk", "finsk", "samisk", "færøysk", "orknøysk", "nordnorsk", "østnorsk",
    "vestnorsk", "amerikansk", "britisk", "engelsk", "skotsk", "irsk", "fransk", "italiensk", "spansk", "nederlandsk",
    "russisk", "polsk", "østerriksk", "sveitsisk", "kanadisk", "norskamerikansk", "norsk-amerikansk", "dansk-norsk",
    "svensk-norsk", "norsk-svensk", "tysk-norsk", "dansk-norsk-svensk", "italiensk-norsk", "polsk-norsk",
    "norsk-samisk", "norsk-brasiliansk", "norsk-ungarsk", "norsk-kanadisk", "luxembourgsk-norsk", "danskfødt",
    "norskfødt", "tyskfødt", "svenskfødt",
    "katolsk", "romersk-katolsk", "luthersk", "evangelisk-luthersk", "pietistisk", "baptistisk", "metodistisk",
    "jødisk",
    "teologisk", "topografisk", "juridisk", "pedagogisk", "faglitterær", "religiøs", "matematisk", "fysisk",
    "botanisk", "kirkelig",
)
NB_PREFIX = re.compile(r"^(?:" + "|".join(re.escape(w) for w in sorted(NB_PREFIX_WORDS, key=len, reverse=True))
                       + r")\s+(?=\S)")

# a qualifier after the occupation ('Bischof von Meißen', 'Professor der Medizin', 'lærer i sjamanisme') or a
# hyphenated one before it ('SPD-Politiker', 'ns-politiker') is dropped, except for words whose meaning it may change:
# military ranks and officers ('Hauptmann von Köpenick', 'Jesuiten-General', 'offiser i Frelsesarmeen'), the
# intendant ('Intendant der Armee') and the secretary ('Sekretär des Zentralkomitees')
QUALIFIER = {"de": re.compile(r"^(.+?)\s+(?:von|vom|des|der|zu|zum|zur|in|im|am|an|bei|beim|für|aus|auf)\s+\S", re.I),
             "nb": re.compile(r"^(.+?)\s+(?:i|ved|av|for|hos|på)\s+\S")}
HYPHENATED = re.compile(r"^\S+-(\S{3,})$")
NOT_QUALIFIED_ITEMS = {"Q189290", "Q10669499", "Q47064"}          # military officer, naval officer, military personnel
NOT_QUALIFIED_WORDS = {"intendant", "sekretär", "kapitän", "offiser", "marineoffiser", "sjøoffiser"}

# German adjectival nouns, whose feminine drops the -r ('die Abgeordnete', 'die Landtagsabgeordnete', 'die Gelehrte')
DE_ADJECTIVAL = ("abgeordneter", "beamter", "gelehrter", "gesandter", "geistlicher", "industrieller", "reisender",
                 "bibliophiler", "angestellter", "vorsitzender", "delegierter", "bevollmächtigter")
DOUBT = re.compile(r"\?|\b(?:vermutl|wahrscheinl|angebl|vielleicht|unsicher|fraglich|zweifelhaft|trolig|kanskje|"
                   r"usikker|antakelig|antagelig)", re.I)


def clean(word, lang):
    """Composed Unicode (part of the NDB index writes 'Pädagoge' with a combining diaeresis), single spaces, no
    surrounding punctuation, a closing remark in brackets dropped ('Sänger (Bass)', 'Syndikus <Hamburg>'); Norwegian in
    lower case, as SNL's own split gives it. Empty when the word is marked uncertain ('Maler?')."""
    w = unicodedata.normalize("NFC", word or "").replace("​", "").replace("­", "")
    w = " ".join(w.split()).strip(" .;,:")
    if DOUBT.search(w):
        return ""
    m = re.fullmatch(r"(.+?)\s*(?:\(([^()]*)\)|<([^<>]*)>)", w)
    if m:
        w = m.group(1).strip(" .;,:")
    return w.lower() if lang == "nb" else w


def strip_prefixes(w, lang):
    """'evangelischer Theologe' -> 'Theologe'; 'norskamerikansk teolog' -> 'teolog' (repeatedly)."""
    pattern = DE_PREFIX if lang == "de" else NB_PREFIX
    while True:
        m = pattern.match(w)
        if not m or not w[m.end():]:
            return w
        w = w[m.end():]


def masculine_forms(w, lang):
    """What a feminine form may stand for: 'Malerin' -> 'Maler', 'Psychologin' -> 'Psychologe', 'Ärztin' -> 'Arzt',
    'Bäuerin' -> 'Bauer', 'Beamtin' -> 'Beamter', 'Kauffrau' -> 'Kaufmann', 'Abgeordnete' -> 'Abgeordneter';
    'organisasjonskvinne' -> 'organisasjonsmann', 'malerinne' -> 'maler'."""
    out = []
    if lang == "de":
        if w.endswith("in") and len(w) > 4:
            base = w[:-2]
            plain = base.translate(str.maketrans("äöüÄÖÜ", "aouAOU"))
            out += [base, base + "e", plain, plain + "e", base + "er"]
        if w.endswith("frau") and len(w) > 6:
            out.append(w[:-4] + "mann")
        if w.endswith("e") and (w + "r").casefold().endswith(DE_ADJECTIVAL):
            out.append(w + "r")
    else:
        if w.endswith("kvinne") and len(w) > 8:
            out.append(w[:-6] + "mann")
        if w.endswith("inne") and len(w) > 6:
            out.append(w[:-4])
    return out


def _find(w, lang):
    """The map word for w itself or one of its masculine forms."""
    index = _INDEX[lang]
    for form in [w] + masculine_forms(w, lang):
        key = index.get(form.casefold())
        if key:
            return key
    return None


def _qualifiable(key, lang):
    return MAP[lang][key] not in NOT_QUALIFIED_ITEMS and key.casefold() not in NOT_QUALIFIED_WORDS


def lookup(word, lang):
    """(map word, item) for an occupation word as a work writes it, or (None, None)."""
    if lang not in MAP:
        raise ValueError(f"no occupation map for language '{lang}'")
    w = clean(word, lang)
    if not w:
        return None, None
    key = _find(w, lang)
    if not key:
        w = strip_prefixes(w, lang)
        key = _find(w, lang)
    if not key:
        m = QUALIFIER[lang].match(w)
        head = strip_prefixes(m.group(1), lang) if m else None
        found = _find(head, lang) if head else None
        if found and _qualifiable(found, lang):
            key = found
    if not key:
        m = HYPHENATED.match(w)
        found = _find(m.group(1), lang) if m else None
        if found and _qualifiable(found, lang):
            key = found
    return (key, MAP[lang][key]) if key else (None, None)


def occupation_qid(word, lang):
    """The occupation item (QID) for a word of the work's occupation field in that language ('de' or 'nb'), or
    None: unknown, left out on purpose (see the unmapped_<lang>.tsv files), or marked uncertain."""
    return lookup(word, lang)[1]


if __name__ == "__main__":
    lang_, *words_ = sys.argv[1:]
    for word_ in words_:
        print(f"{word_!r}: {lookup(word_, lang_)}")
