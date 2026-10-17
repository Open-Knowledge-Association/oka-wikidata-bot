"""Sächsische Biografie (P1710), Institut für Sächsische Geschichte und Volkskunde: the core data of all its 13,000
people come from the table its own search page loads in one request (robots.txt bars only the CMS back end), read
once and cached; only the fields below are kept, never the article texts.

Facts: dates and places of birth and death as the table gives them, read with the Deutsche Biographie's German
helpers (readers3/ndb.py: exact dates only, '[20.06./ 22.06.]1787' and the like are not used; places by name, with
'Frankfurt/ Oder' read as 'Frankfurt (Oder)'); sex from the table's own field; occupations through the checked
mapping of readers3/ndb.py (other words are noted, not used)."""
import json
import re

import dictionaries as d
import sources3 as s
from readers3 import ndb as n

WORK = "saebi"
TABLE = "https://saebi.isgv.de/files/saebi/suche/scripts/server_processing.php"
KEEP = ("SNR", "Name", "Vorname", "Geburtsdatum", "Geburtsort", "Sterbedatum", "Sterbeort", "Beruf", "Status", "Geschlecht")
SEX = {"Männlich": n.MALE, "Weiblich": n.FEMALE}
EVENTS = (("Geburtsdatum", "Geburtsort", ("P569", "date of birth"), ("P19", "place of birth")),
          ("Sterbedatum", "Sterbeort", ("P570", "date of death"), ("P20", "place of death")))


def candidates(n_):
    return s.linked("P1710", n_)


def table():
    """The core data of all 13,000 people (the table the site's own search page loads in one request), by SNR; only
    these fields are kept, not the article texts the table also carries."""
    def fetch():
        rows = json.loads(s.polite_get(TABLE, gap=3.0))["data"]
        return json.dumps({str(r["SNR"]): {k: r.get(k) for k in KEEP} for r in rows}, ensure_ascii=False)
    return json.loads(s.cached(WORK, "table.json", fetch))


def read(ident):
    t = table().get(str(ident))
    if not t:
        raise LookupError(f"Sächsische Biografie {ident}: not in the table")
    title = re.sub(r"<[^>]+>", "", ", ".join(x for x in (t.get("Name"), t.get("Vorname")) if x)) or str(ident)
    # (the table underlines the name a person went by: '<u>Johannes</u> Fürchtegott')
    facts, notes = [], []
    for date_key, place_key, (pid_date, label_date), (pid_place, label_place) in EVENTS:
        if t.get(date_key):
            f = n.german_date(t[date_key], pid_date, label_date, notes)
            if f:
                facts.append(f)
        if t.get(place_key):
            q = n.german_place(t[place_key], label_place, notes)
            if q:
                facts.append(d.fact(pid_place, d.item_value(q), label_place))
    if t.get("Geschlecht") in SEX:
        facts.append(d.fact("P21", d.item_value(SEX[t["Geschlecht"]]), "sex or gender"))
    if t.get("Beruf"):
        facts += n.occupation_facts(re.split(r",\s*", t["Beruf"]), notes)
    return {"title": title, "kind": "person", "facts": facts, "notes": notes}
