"""Corrections to task 3 plans for places matched to the wrong namesake (11 November 2026).

The German, Danish and Finnish readers match a bare place name to "the one town of the home country so named, else a
well-known city anywhere". When the home country has a hamlet of that name, the hamlet wins: WGW's 'Passau' became a
locality in Carinthia, Lex's 'Paris', 'Berlin' and 'Rom' Danish villages, 'Krakau' the Styrian municipality.

Found by comparing every place the plans use with settlements of exactly the same label that have at least 20 and
five times as many sitelinks (QLever), then judged one by one. REPLACE: the bare name clearly means the well-known
place. DROP: it could be either, so the fact is left out (listed in task3_place_fixes.csv). Everything else
the comparison flagged was kept: Swedish parishes and 'Stockholms stad' are what SBL writes, a village core beside its
municipality is the same place, and many namesakes abroad are simply the right place.

The run's same-name rule already kept these from changing items that had the right place (the source went to the
item's own value); only items without a place would have got the namesake. Before this fix, 1,346 entries were
saved and none of them added one of these places.

    python task3_place_fixes.py <plan.jsonl> ...           what would change
    python task3_place_fixes.py --save <plan.jsonl> ...    rewrite the plans (a copy is kept as <plan>.before-fixes)
"""
import json
import shutil
import sys
from pathlib import Path

PLACE_PIDS = ("P19", "P20", "P119", "P551", "P937")

REPLACE = {
    "Q1628773": "Q90",        # Paris, Denmark -> Paris
    "Q12303254": "Q64",       # Berlin, village in Middelfart -> Berlin
    "Q134605518": "Q220",     # Rom, village in West Jutland -> Rome
    "Q25552248": "Q10704",    # a second Tórshavn item -> Tórshavn
    "Q18628307": "Q31487",    # Krakau, Styria -> Kraków
    "Q133445745": "Q4190",    # Passau, Hermagor District -> Passau
    "Q96463162": "Q70",       # Bern, Ried District -> Bern
    "Q94699533": "Q1757",     # Helsinki, village in Taivassalo -> Helsinki
    "Q113026926": "Q641",     # Venedig, suburb of Nabburg -> Venice
    "Q11929681": "Q900",      # Kasan, Fergana Valley -> Kazan
    "Q109315677": "Q3974",    # Landshut, Freistadt District -> Landshut
    "Q54877306": "Q3802",     # Hanau, Melk District -> Hanau
    "Q98096658": "Q61936",    # Starnberg, Rohrbach District -> Starnberg
    "Q118372094": "Q3936",    # Bamberg, Braunau District -> Bamberg
    "Q60441938": "Q697557",   # Meidling, Krems District -> Meidling (Vienna)
    "Q1803363": "Q492366",    # Norden, the former district -> Norden, the town
    "Q1803305": "Q4185",      # Lingen, the former district -> Lingen, the town
    "Q21560196": "Q54352",    # Landskrona district -> Landskrona
    "Q21567701": "Q54759",    # Vänersborg district -> Vänersborg
    "Q21667301": "Q3588743",  # Norrtälje district -> Norrtälje
    "Q21667335": "Q3183470",  # Sigtuna district -> Sigtuna
    "Q21684694": "Q297790",   # Öregrund district -> Öregrund
}
DROP = {
    "Q660945": "Straßburg: the Carinthian town or Strasbourg",
    "Q668125": "Münster: a village in Valais or one of the German towns",
    "Q18439541": "Pola: a settlement in Italy or Pula",
    "Q109658472": "Oberhausen: a locality in Lower Austria or the city in the Ruhr",
    "Q96024586": "Freiberg: a settlement in Styria, Freiberg in Saxony or Příbor",
    "Q134548127": "Friedrichsdorf: a village in Upper Austria or the town in Hesse",
    "Q135635545": "Wolfsbach: a locality in Bavaria or the municipality in Lower Austria",
    "Q997476": "Sachsenhausen: a quarter of Wertheim or the concentration camp",
    "Q897884": "Brandenburg: a Slavic gord or the town",
    "Q56091033": "Brandenburg: which one",
    "Q1801952": "Landau: in der Pfalz or an der Isar",
    "Q32760937": "Winsen: (Luhe) or (Aller)",
    "Q56399153": "Pajala: a district of Järvenpää or Pajala in Sweden",
    "Q11902173": "Västervik: a district of Vaasa or Västervik in Sweden",
    "Q2446296": "Brooklyn: the town in Connecticut or the borough of New York",
    "Q5179337": "Covington: which one",
    "Q2678207": "Lund: the Danish town or Lund in Scania",
    "Q2457304": "Visby: the Danish village or Visby on Gotland",
    "Q12317203": "Horn: the Danish village or another Horn",
    "Q667757": "Biel: the village in Valais or Biel/Bienne",
    "Q1311946": "Montgomery: in Wales or in Alabama",
    "Q65430768": "Spring Grove: which one",
    "Q126375530": "Hopfgarten: a hamlet in Untertilliach or Hopfgarten im Brixental/in Defereggen",
    "Q126375532": "Kirchberg: a hamlet in Untertilliach or another Kirchberg",
    "Q111240573": "Stockach: the Tyrolean village or the town on Lake Constance",
    "Q3642328": "Frankfort: South Africa or Kentucky",
    "Q3806782": "Jamestown: which one",
    "Q10293788": "Žilina: a part of Nový Jičín or the Slovak city",
    "Q27645049": "Liebenau: which one",
    "Q27658520": "Solca: the abandoned Silesian village or the town in Bukovina",
    "Q27660008": "Altenberg: an abandoned hamlet or another Altenberg",
    "Q60820596": "Schneeberg: a locality in Zwettl District or the town in Saxony",
    "Q14551006": "Neukirchen: which one",
    "Q135854961": "Velden: an abandoned village or Velden am Wörthersee",
    "Q133446935": "Sankt Lambrecht: a locality in Villach-Land or the Styrian market town",
}


def item(q):
    return {"type": "wikibase-entityid", "value": {"entity-type": "item", "numeric-id": int(q[1:]), "id": q}}


def fix(entry):
    """(entry, list of what changed)."""
    facts, changed = [], []
    for f in entry["facts"]:
        q = f["value"]["value"].get("id") if f["pid"] in PLACE_PIDS and f["value"]["type"] == "wikibase-entityid" else None
        if q in REPLACE:
            f = {**f, "value": item(REPLACE[q])}
            changed.append(f"{f['label']} {q} -> {REPLACE[q]}")
        elif q in DROP:
            changed.append(f"{f['label']} {q} left out ({DROP[q]})")
            continue
        facts.append(f)
    return {**entry, "facts": facts}, changed


def main():
    save = "--save" in sys.argv
    log = []
    for name in [a for a in sys.argv[1:] if a != "--save"]:
        path = Path(name)
        lines = path.read_text(encoding="utf-8").splitlines()
        out, n = [], 0
        for line in lines:
            e, changed = fix(json.loads(line))
            n += bool(changed)
            for c in changed:
                print(f"{path.name} {e['id']} {e['qid']}: {c}")
                log.append((e["work"], e["id"], e["qid"], c))
            out.append(json.dumps(e, ensure_ascii=False))
        print(f"{path.name}: {n} entries changed")
        if save and n:
            backup = path.with_name(path.name + ".before-fixes")
            if not backup.exists():
                shutil.copy2(path, backup)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            tmp.replace(path)
    if save and log:
        import csv
        csv_path = Path(__file__).with_name("task3_place_fixes.csv")
        new = not csv_path.exists()
        with open(csv_path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["work", "entry", "item", "change"])
            w.writerows(log)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
