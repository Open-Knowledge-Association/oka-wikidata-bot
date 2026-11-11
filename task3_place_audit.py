"""For every place item a task 3 plan uses: is there a far better known settlement with exactly the same label
(at least 20 and five times as many sitelinks)? WGW matched a bare 'Passau' to the hamlet in Carinthia because Austria
is its home country (11 November 2026). Judge each flagged place by hand, then add it to REPLACE or DROP in
task3_place_fixes.py and apply that to the plan.

    python task3_place_audit.py places.json out_dir
places.json: {work: {place QID: uses}}, e.g. from the plan's P19/P20/P119/P551/P937 values. Writes out_dir/flags.json.
Queries go through curl (this PC's Python certificate store is out of date)."""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path
places = json.load(open(sys.argv[1], encoding="utf-8"))
HERE = Path(sys.argv[2])
HERE.mkdir(exist_ok=True)
qs = sorted(set().union(*places.values()), key=lambda q: int(q[1:]))
EP = "https://qlever.dev/api/wikidata"
Q = """PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
SELECT DISTINCT ?q ?l ?n ?other ?no WHERE {
  VALUES ?q { %s }
  ?q rdfs:label ?l . FILTER(LANG(?l) IN ("de", "da", "sv", "nb", "en"))
  OPTIONAL { ?q wikibase:sitelinks ?n0 } BIND(COALESCE(?n0, 0) AS ?n)
  ?other rdfs:label ?l . FILTER(?other != ?q)
  ?other wikibase:sitelinks ?no . FILTER(?no >= 20 && ?no > 5 * ?n)
  ?other wdt:P31/wdt:P279* wd:Q486972 .
}"""
rows = []
for i in range(0, len(qs), 250):
    import subprocess, tempfile
    qf = HERE / "q.rq"
    qf.write_text(Q % " ".join("wd:" + q for q in qs[i:i + 250]), encoding="utf-8")
    for attempt in range(5):
        r = subprocess.run(["curl", "-s", "--max-time", "300", "-H", "Accept: application/sparql-results+json",
                            "-A", "OKA-bot-place-audit/1.0", "--data-urlencode", f"query@{qf}", EP], capture_output=True)
        try:
            res = json.loads(r.stdout)
            break
        except Exception as e:
            print("retry", i, r.stdout[:200], file=sys.stderr); time.sleep(20)
    else:
        sys.exit("QLever failed")
    for b in res["results"]["bindings"]:
        rows.append({k: v["value"].rsplit("/", 1)[-1] for k, v in b.items()})
    print(i + 250, len(rows), file=sys.stderr)
json.dump(rows, open(HERE / "flags.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
