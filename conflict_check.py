"""Tasks 2 and 3: check the values left alone because Wikidata says something else, where Wikidata's value has no
source (or only an import from a Wikipedia), under the rules of the OKA bot 3 request (16 October 2026):
  replace  no counted source supports Wikidata's value and other sources confirm the entry's: the entry's value is
           added; Wikidata's is removed (no reference) or deprecated as incorrect (only a Wikipedia import)
  prefer   no counted source supports Wikidata's value, and none confirms the entry's: Wikidata's value stays, the
           entry's value is added with preferred rank (P7452: best referenced value)
  both     counted sources support each value (one each is enough): the entry's value is added beside Wikidata's at
           the same rank, unless the model reads the sources as establishing Wikidata's value
  report   counted sources support Wikidata's value and none supports the entry's (other values found do not
           matter), or they establish Wikidata's value: nothing is added, the error is reported to the work
  person   a place name that fits both, only other values (nothing supports Wikidata's), or the model's reading
           contradicts the sources' count
Nothing is edited until the request is approved (jz, 16 October 2026).

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
MODEL = os.environ.get("CONFLICT_MODEL", "claude-sonnet-5-5")      # e.g. claude-haiku-5-5, to compare cost and quality
LEFT = ROOT / "dictionary_left_alone.csv"
LOG = ROOT / os.environ.get("CONFLICT_LOG", "conflict_checks.csv")
SAME_AS = os.environ.get("CONFLICT_SAME_AS")     # another log: check exactly its cases again (a model comparison)
LABELS = {"date of birth": "P569", "date of death": "P570", "place of birth": "P19", "place of death": "P20"}
WORK_NAMES = {"snl": "Store norske leksikon", "nkl": "Norsk kunstnerleksikon", "sml": "Store medisinske leksikon",
              "hds": "Historical Dictionary of Switzerland"}
URLS = {"snl": "https://snl.no/{}", "nkl": "https://nkl.snl.no/{}", "sml": "https://sml.snl.no/{}",
        "hds": "https://hls-dhs-dss.ch/de/articles/{}/"}
FIELDS = ["time", "work", "entry", "qid", "property", "wikidata_value", "wikidata_sources", "entry_value",
          "decision", "established", "evidence", "note", "model", "searches", "input_tokens", "output_tokens"]
NOT_EVIDENCE = re.compile(r"wikipedia\.org|wikidata\.org|wikimedia\.org|wikiwand\.|dbpedia\.|lex\.dk/.*Wikipedia|alchetron\."
                          r"|peoplepill\.|everybodywiki\.|wikishia\.|fandom\.com", re.I)    # Wikipedia, its copies, wikis
USER_EDITED = re.compile(r"geni\.com|wikitree\.com|familysearch\.org|myheritage\.|ancestry\.|findagrave\.com|billiongraves\."
                         r"|facebook\.com|reddit\.com|quora\.com|prabook\.com", re.I)   # family trees, forums: anyone edits
WORK_SITES = {"snl": r"//(www\.)?snl\.no/", "nkl": r"//nkl\.snl\.no/", "sml": r"//sml\.snl\.no/",
              "hds": r"hls-dhs-dss\.ch/"}   # the work itself (NBL, NKL and SML on snl.no are other works and count)


def call(path, body=None):
    """One request to the Claude API (the key comes from the environment and is never printed)."""
    key = os.environ["OKA_ANTHROPIC_API_KEY"]                 # set by jz on Toolforge
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
    """Disagreements on dates and places where Wikidata's value has no source or only a Wikipedia import, judged again
    under the bot's current rules (a place inside the other, the same day in the other calendar, a year beside the
    full date are no disagreements)."""
    done = {(r["work"], r["entry"], r["property"]) for r in csv.DictReader(open(LOG, encoding="utf-8"))} if LOG.exists() else set()
    only = {(r["work"], r["entry"], r["property"]) for r in csv.DictReader(open(ROOT / SAME_AS, encoding="utf-8"))} \
        if SAME_AS else None
    plans = {}
    for w in WORK_NAMES:
        p = d.WORK2 / f"full_plan_{w}.jsonl"
        if p.exists():
            plans.update({(w, e["id"]): e for e in map(json.loads, open(p, encoding="utf-8"))})
    keys = list(dict.fromkeys((r["work"], r["entry"]) for r in csv.DictReader(open(LEFT, encoding="utf-8"))
                              if "different value" in r["note"] and r["note"].split(":")[0] in LABELS
                              and (r["work"], r["entry"]) in plans
                              and (only is None or any(k[:2] == (r["work"], r["entry"]) for k in only))))
    today = datetime.datetime.now(datetime.timezone.utc).strftime("+%Y-%m-%dT00:00:00Z")
    for i in range(0, len(keys), 50):
        batch = [plans[k] for k in keys[i:i + 50]]
        live = d.items(sorted({e["qid"] for e in batch}))
        labels = d.place_labels(live, batch)
        for e in batch:
            ent = live.get(e["qid"], {})
            for note in d.build(ent, e, today, labels)[2] if ent else []:
                label = note.split(":")[0]
                if "different value" not in note or label not in LABELS or (e["work"], e["id"], label) in done \
                        or (only is not None and (e["work"], e["id"], label) not in only):
                    continue
                ours = [f["value"] for f in e["facts"] if f["pid"] == LABELS[label]]
                claims = [c for c in ent.get("claims", {}).get(LABELS[label], [])
                          if c["rank"] != "deprecated" and c["mainsnak"]["snaktype"] == "value"]
                if not ours or not claims or any(c.get("references") and not only_import_refs(c) for c in claims):
                    continue                          # a sourced value: a person reviews it, no search needed
                refs = "imported from a Wikipedia" if any(c.get("references") for c in claims) else "no source"
                yield {"work": e["work"], "entry": e["id"], "qid": e["qid"]}, label, ours[0], claims, refs, ent


def counted(evidence, work):
    """The sources that count: not Wikipedia, Wikidata or their copies, not sites anyone can edit, not the work itself."""
    return [x for x in evidence if not NOT_EVIDENCE.search(x.get("url", "")) and not USER_EDITED.search(x.get("url", ""))
            and not re.search(WORK_SITES[work], x.get("url", ""))]


def decide(evidence, established, work, label):
    """The request's rules, applied to the counted sources and to the model's reading of them ('established'). jz's
    rules (16 October 2026): when no counted source supports Wikidata's value, the entry's value is added even if no
    other source states the fact (the entry is a source; an unsourced value or a Wikipedia import is not). If other
    sources confirm the entry, Wikidata's value is wrong: removed without a reference, deprecated as incorrect when
    only imported from a Wikipedia (replace). If only the entry contradicts it, it stays, and the entry's value gets
    preferred rank as the best referenced value (prefer). The model's reading can stop an edit but never causes one."""
    says = {x.get("supports") for x in counted(evidence, work)}
    if label.startswith("place") and "both" in says:      # a name that fits either place (an old name, a district)
        return "person"
    if "wikidata" in says:
        if "entry" in says:                   # sources for each value: a real conflict, both at the same rank (jz),
            return "report" if established == "wikidata" else "both"   # unless they establish Wikidata's value
        return "report" if established in ("wikidata", "unclear") else "person"   # the entry's value unsupported
    if "other" in says:                       # nothing supports Wikidata's value, but sources give yet another one
        return "person"
    if established not in ("entry", "unclear"):   # other sources confirm the entry: Wikidata's value is wrong
        return "person"                           # (replace); only the entry: its value is preferred (prefer)
    return "replace" if "entry" in says and established == "entry" else "prefer"


def check(r, label, ours, claims, refs, e):
    name = next((e["labels"][l]["value"] for l in ("en", "nb", "mul", "de") if l in e.get("labels", {})), r["qid"])
    desc = next((e["descriptions"][l]["value"] for l in ("en", "nb") if l in e.get("descriptions", {})), "")
    theirs = "; ".join(show(c["mainsnak"]["datavalue"]) for c in claims)
    entry = show(ours)
    url = URLS[r["work"]].format(r["entry"])
    work = WORK_NAMES[r["work"]]
    prompt = f"""You are checking one fact on Wikidata against reliable sources.

Person: {name} ({desc}), Wikidata item {r['qid']}.
Fact: {label}.
Wikidata says: {theirs} ({refs}).
{work} ({url}) says: {entry}.

Search the web for this person's {label}: national biographies and encyclopedias, archives, official registers,
obituaries, museum or library authority records. These do not count; leave them out of your list and your answer:
Wikipedia, Wikidata and sites that copy them (Wikidata's value may come from them); {work} itself, which we already
have; sites anyone can edit, such as family trees (Geni, WikiTree, FamilySearch, MyHeritage, Ancestry), Find a Grave,
forums and social media.

List every other source you read that states this fact, whatever value it gives, and say which value it supports:
"wikidata" ({theirs}), "entry" ({entry}), "both" (its wording fits both: one place inside the other, the same place
under another name, or the same date in another calendar) or "other" (a different value). A source that only names
the country or the region does not state the fact; leave it out. Quote the page's own words; do not infer.

Then say what these sources establish: "wikidata" (they establish {theirs}), "entry" (they establish {entry}),
"split" (reliable sources genuinely disagree) or "unclear" (too little or too weak evidence, or another value).

End with one fenced JSON block, exactly:
```json
{{"evidence": [{{"url": "...", "says": "the page's words, quoted briefly", "supports": "wikidata|entry|both|other"}}],
  "established": "wikidata|entry|split|unclear", "note": "one sentence"}}
```"""
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    searches, texts, tokens_in, tokens_out = 0, [], 0, 0
    for _ in range(6):                                # 'pause_turn': send the turn back until it ends
        resp = call("messages", {"model": MODEL, "max_tokens": 3000, "messages": messages, "tools": tools})
        usage = resp.get("usage", {})
        searches += usage.get("server_tool_use", {}).get("web_search_requests", 0)
        tokens_in += usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        tokens_out += usage.get("output_tokens", 0)
        texts += [b["text"] for b in resp["content"] if b.get("type") == "text"]
        if resp.get("stop_reason") != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": resp["content"]}]
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", "\n".join(texts), re.S)
    try:
        verdict = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        verdict = None
    evidence = (verdict or {}).get("evidence", [])
    return {"time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "work": r["work"], "entry": r["entry"], "qid": r["qid"], "property": label,
            "wikidata_value": theirs, "wikidata_sources": refs, "entry_value": entry,
            "decision": decide(evidence, verdict.get("established"), r["work"], label) if verdict else "person",
            "established": (verdict or {}).get("established", ""),
            "evidence": json.dumps(evidence, ensure_ascii=False),
            "note": (verdict or {}).get("note", "no verdict returned"), "model": MODEL, "searches": searches,
            "input_tokens": tokens_in, "output_tokens": tokens_out}


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
                  "|", row["decision"], "| searches", row["searches"], "| tokens", row["input_tokens"],
                  row["output_tokens"], flush=True)


if __name__ == "__main__":
    main()
