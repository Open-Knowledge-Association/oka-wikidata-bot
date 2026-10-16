"""Task 2: check the values left alone because Wikidata says something else, where Wikidata's value has no source
(or only an import from a Wikipedia). jz's rule (16 October 2026): search for the old value; if nothing supports it,
the entry's value replaces it; if a source supports it, a person reviews the case.

Claude (with web search) does the search; every answer is logged with the sources it found, in conflict_checks.csv.
Nothing is edited here.

  python conflict_check.py models          which models this key can use, and whether they can search the web
  python conflict_check.py check [N]       check up to N open cases (default 20) and log the answers
"""
import csv
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import dictionaries as d
from common import ROOT, only_import_refs

API = "https://api.anthropic.com/v1"
MODEL = "claude-sonnet-5-5"
LEFT, LOG = ROOT / "dictionary_left_alone.csv", ROOT / "conflict_checks.csv"
LABELS = {"date of birth": "P569", "date of death": "P570", "place of birth": "P19", "place of death": "P20"}
WORK_NAMES = {"snl": "Store norske leksikon", "nkl": "Norsk kunstnerleksikon", "sml": "Store medisinske leksikon",
              "hds": "Historical Dictionary of Switzerland"}
URLS = {"snl": "https://snl.no/{}", "nkl": "https://nkl.snl.no/{}", "sml": "https://sml.snl.no/{}",
        "hds": "https://hls-dhs-dss.ch/de/articles/{}/"}
FIELDS = ["time", "work", "entry", "qid", "property", "wikidata_value", "wikidata_sources", "entry_value",
          "wikidata_value_supported", "entry_value_supported", "evidence", "note", "model", "searches"]


def call(path, body=None):
    """One request to the Claude API (the key comes from the environment and is never printed)."""
    key = os.environ.get("OKA_ANTHROPIC_API_KEY") or os.environ["ANTHROPIC_API_KEY"]   # set by jz on Toolforge
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    req = urllib.request.Request(f"{API}/{path}", json.dumps(body).encode() if body else None, headers,
                                 method="POST" if body else "GET")
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 529) and attempt < 5:
                time.sleep(30 * attempt)
                continue
            raise RuntimeError(f"Claude API {e.code}: {text[:300]}")


def models():
    for m in call("models?limit=100").get("data", []):
        search = (m.get("capabilities", {}).get("server_tools", {}).get("web_search", {}) or {}).get("supported")
        print(m["id"], "| web search:", search)


def show(v):
    """A value as a person would write it: a date, or an item's label and ID."""
    if v.get("type") == "time":
        t, p = v["value"]["time"], v["value"]["precision"]
        return t[1:5] if p == 9 else t[1:8] if p == 10 else t[1:11]
    q = v["value"]["id"]
    e = d.items([q]).get(q, {})
    label = next((e["labels"][l]["value"] for l in ("en", "nb", "mul", "de", "fr") if l in e.get("labels", {})), q)
    return f"{label} ({q})"


def open_cases():
    """Disagreements on dates and places where Wikidata's value has no source or only a Wikipedia import."""
    done = {(r["work"], r["entry"], r["property"]) for r in csv.DictReader(open(LOG, encoding="utf-8"))} if LOG.exists() else set()
    plans = {}
    for w in WORK_NAMES:
        p = d.WORK2 / f"full_plan_{w}.jsonl"
        if p.exists():
            plans.update({(w, e["id"]): e for e in map(json.loads, open(p, encoding="utf-8"))})
    for r in csv.DictReader(open(LEFT, encoding="utf-8")):
        label = r["note"].split(":")[0]
        if "different value" not in r["note"] or label not in LABELS or (r["work"], r["entry"], label) in done:
            continue
        e = plans.get((r["work"], r["entry"]))
        ours = [f["value"] for f in (e or {}).get("facts", []) if f["pid"] == LABELS[label]]
        if not ours:
            continue
        claims = [c for c in d.items([r["qid"]]).get(r["qid"], {}).get("claims", {}).get(LABELS[label], [])
                  if c["rank"] != "deprecated" and c["mainsnak"]["snaktype"] == "value"]
        if not claims or any(c.get("references") and not only_import_refs(c) for c in claims):
            continue                                  # a sourced value: a person reviews it, no search needed
        refs = "imported from a Wikipedia" if any(c.get("references") for c in claims) else "no source"
        yield r, label, ours[0], claims, refs


def check(r, label, ours, claims, refs):
    e = d.items([r["qid"]]).get(r["qid"], {})
    name = next((e["labels"][l]["value"] for l in ("en", "nb", "mul", "de") if l in e.get("labels", {})), r["qid"])
    desc = next((e["descriptions"][l]["value"] for l in ("en", "nb") if l in e.get("descriptions", {})), "")
    theirs = "; ".join(show(c["mainsnak"]["datavalue"]) for c in claims)
    entry = show(ours)
    url = URLS[r["work"]].format(r["entry"])
    prompt = f"""You are checking one fact on Wikidata against reliable sources.

Person: {name} ({desc}), Wikidata item {r['qid']}.
Fact: {label}.
Wikidata says: {theirs} ({refs}).
{WORK_NAMES[r['work']]} ({url}) says: {entry}.

Search the web for reliable sources on this person's {label}: national biographies and encyclopedias, archives,
official registers, obituaries, museum or library authority records. Wikipedia, Wikidata and sites that copy them do
not count as evidence, since Wikidata's value may come from them. Report only what you actually read in the results.

End with one fenced JSON block, exactly:
```json
{{"wikidata_value_supported": true or false,
  "entry_value_supported": true or false,
  "evidence": [{{"url": "...", "says": "the value this source gives, quoted briefly"}}],
  "note": "one sentence"}}
```"""
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    searches, texts = 0, []
    for _ in range(6):                                # 'pause_turn': send the turn back until it ends
        resp = call("messages", {"model": MODEL, "max_tokens": 3000, "messages": messages, "tools": tools})
        searches += resp.get("usage", {}).get("server_tool_use", {}).get("web_search_requests", 0)
        texts += [b["text"] for b in resp["content"] if b.get("type") == "text"]
        if resp.get("stop_reason") != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": resp["content"]}]
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", "\n".join(texts), re.S)
    verdict = json.loads(blocks[-1]) if blocks else {"note": "no verdict returned"}
    return {"time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "work": r["work"], "entry": r["entry"], "qid": r["qid"], "property": label,
            "wikidata_value": theirs, "wikidata_sources": refs, "entry_value": entry,
            "wikidata_value_supported": verdict.get("wikidata_value_supported"),
            "entry_value_supported": verdict.get("entry_value_supported"),
            "evidence": json.dumps(verdict.get("evidence", []), ensure_ascii=False),
            "note": verdict.get("note", ""), "model": MODEL, "searches": searches}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "models":
        return models()
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    new = not LOG.exists()
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        for n, case in enumerate(open_cases(), 1):
            if n > limit:
                break
            row = check(*case)
            w.writerow(row)
            f.flush()
            print(n, row["entry"], row["property"], "| WD", row["wikidata_value"], "| entry", row["entry_value"],
                  "| WD supported:", row["wikidata_value_supported"], "| searches", row["searches"], flush=True)


if __name__ == "__main__":
    main()
