"""Tasks 2 and 3: check the values left alone because Wikidata says something else, where Wikidata's value has no
source (or only an import from a Wikipedia), under the rules of the OKA bot 3 request (16 October 2026, changed
23 October 2026 after objections to removing values without a person's review):
  prefer   no counted source supports Wikidata's value: the entry's value is added with preferred rank (P7452: best
           referenced value); Wikidata's value stays as it is, never removed or deprecated, and the case is listed on
           User:OKA bot/Disagreement checks so that editors decide whether it should go
  both     counted sources support each value (one each is enough), nothing supports Wikidata's value while sources
           give yet other values (the fact is unclear), a place name fits both values, or the model's reading
           contradicts the sources: the entry's value is added beside Wikidata's at the same rank. Also every case
           where Wikidata's value cites another source (not searched: two sources disagree)
  report   counted sources support Wikidata's value and none supports the entry's (other values found do not
           matter), or they establish Wikidata's value: the entry's value is added with deprecated rank (P2241:
           Q29998666, error in referenced source), referenced to the entry, so that the item shows the work states
           otherwise (jz, 17 October 2026); the error is also reported to the work
  retry    no answer from the model: checked again later (no case goes to a person; jz, 17 October 2026)
Nothing is edited until the request is approved (jz, 16 October 2026).

Claude (with web search) does the search; every answer is logged with the sources it found, in conflict_checks.csv.
Nothing is edited here.

  python conflict_check.py models          which models this key can use, and whether they can search the web
  python conflict_check.py check [N]       check up to N open cases (default 20) and log the answers
  python conflict_check.py batch [N]       the same through the Message Batches API (half the price; answers within
                                           hours): submits, waits, then logs the answers
  python conflict_check.py collect ID      wait for a batch submitted earlier and log its answers
  python conflict_check.py sourced         the disagreements where Wikidata's value cites another source: no search,
                                           both values side by side (outcome "both"); logged in conflict_sourced.csv
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
from common import ROOT, WORK, only_import_refs

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


def call(path, body=None, raw=False):
    """One request to the Claude API (the key comes from the environment and is never printed); `raw` gives the
    answer's text (a batch's results are JSON lines)."""
    key = os.environ["OKA_ANTHROPIC_API_KEY"]                 # set by jz on Toolforge
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    url = path if path.startswith("https://") else f"{API}/{path}"
    req = urllib.request.Request(url, json.dumps(body).encode() if body else None, headers,
                                 method="POST" if body else "GET")
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                got = r.read()
                return got.decode("utf-8") if raw else json.loads(got)
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


def open_cases(sourced=False, log=None):
    """Disagreements on dates and places where Wikidata's value has no source or only a Wikipedia import (with
    `sourced`: where it cites another source), judged again under the bot's current rules (a place inside the other,
    the same day in the other calendar, a year beside the full date are no disagreements)."""
    log = log or LOG
    done = {(r["work"], r["entry"], r["property"]) for r in csv.DictReader(open(log, encoding="utf-8"))} if log.exists() else set()
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
                cited = any(c.get("references") and not only_import_refs(c) for c in claims)
                if not ours or not claims or cited != sourced:
                    continue      # a sourced value: two sources disagree, both side by side (no search needed)
                if label.startswith("place") and nested([c["mainsnak"]["datavalue"]["value"]["id"] for c in claims],
                                                        ours[0]["value"]["id"]):
                    continue      # one place inside the other (Cambridge House in London): no disagreement
                refs = "cites another source" if cited else \
                    "imported from a Wikipedia" if any(c.get("references") for c in claims) else "no source"
                yield {"work": e["work"], "entry": e["id"], "qid": e["qid"]}, label, ours[0], claims, refs, ent


def nested(theirs, ours):
    """Whether one of Wikidata's places lies inside the entry's place or the other way round, through located in,
    location or part of (Cambridge House in London, a village in its municipality): the values agree at different
    precision, so there is no disagreement and nothing is edited. The bot's build() only knows towns in
    municipalities; this also catches buildings, districts and former municipalities."""
    for a, b in [(t, ours) for t in theirs] + [(ours, t) for t in theirs]:
        if a != b and d.lookup(f"SELECT ?x WHERE {{ wd:{a} (wdt:P131|wdt:P276|wdt:P361)+ wd:{b} . BIND(1 AS ?x) }} LIMIT 1"):
            return True
    return False


def nested_row(r):
    """nested() for a logged case ('Cambridge House (Q5025454)' and 'London (Q84)')."""
    theirs = re.findall(r"\((Q\d+)\)", r["wikidata_value"])
    ours = re.findall(r"\((Q\d+)\)", r["entry_value"])
    return r["property"].startswith("place") and bool(theirs and ours) and nested(theirs, ours[0])


def counted(evidence, work):
    """The sources that count: not Wikipedia, Wikidata or their copies, not sites anyone can edit, not the work itself."""
    return [x for x in evidence if not NOT_EVIDENCE.search(x.get("url", "")) and not USER_EDITED.search(x.get("url", ""))
            and not re.search(WORK_SITES[work], x.get("url", ""))]


def decide(evidence, established, work, label):
    """The request's rules, applied to the counted sources and to the model's reading of them ('established'). jz's
    rules (16 October 2026): when no counted source supports Wikidata's value, the entry's value is added even if no
    other source states the fact (the entry is a source; an unsourced value or a Wikipedia import is not). Since
    23 October 2026 (jz) it gets preferred rank as the best referenced value and Wikidata's value stays, whether or
    not other sources confirm the entry: the bot never removes or deprecates others' values, the page lists the
    case for editors (prefer). The model's reading can stop an edit but never causes one."""
    says = {x.get("supports") for x in counted(evidence, work)}
    # No case goes to a person (jz, 17 October 2026): what is unclear gets both values side by side.
    if label.startswith("place") and "both" in says:      # a name that fits either place (an old name, a district)
        return "both"
    if "wikidata" in says:
        if "entry" in says:                   # sources for each value: a real conflict, both at the same rank (jz),
            return "report" if established == "wikidata" else "both"   # unless they establish Wikidata's value
        return "report" if established in ("wikidata", "unclear") else "both"   # the entry's value unsupported
    if "other" in says:     # nothing supports Wikidata's value, and sources give yet other ones: the fact is unclear,
        return "both"       # which both values side by side show (jz)
    if established not in ("entry", "unclear"):   # the model's reading contradicts the sources: both side by side
        return "both"
    return "prefer"         # no source supports Wikidata's value: it stays, the entry's value is preferred (jz)


TOOLS = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]


def prompt_for(r, label, ours, claims, refs, e):
    """The case's prompt, and what the log keeps about the case."""
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
    return prompt, {"work": r["work"], "entry": r["entry"], "qid": r["qid"], "label": label, "theirs": theirs,
                    "refs": refs, "entry_value": entry}


def answer(prompt, first=None):
    """The model's texts and usage for one case: from `first` (a batch result) or a fresh request, sending the turn
    back while the search pauses it ('pause_turn')."""
    messages = [{"role": "user", "content": prompt}]
    searches, texts, tokens_in, tokens_out = 0, [], 0, 0
    resp = first
    for _ in range(6):
        if resp is None:
            resp = call("messages", {"model": MODEL, "max_tokens": 3000, "messages": messages, "tools": TOOLS})
        usage = resp.get("usage", {})
        searches += usage.get("server_tool_use", {}).get("web_search_requests", 0)
        tokens_in += usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        tokens_out += usage.get("output_tokens", 0)
        texts += [b["text"] for b in resp["content"] if b.get("type") == "text"]
        if resp.get("stop_reason") != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": resp["content"]}]
        resp = None
    return texts, searches, tokens_in, tokens_out


def logged_row(m, texts, searches, tokens_in, tokens_out):
    label = m["label"]
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", "\n".join(texts), re.S)
    try:
        verdict = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        verdict = None
    evidence = (verdict or {}).get("evidence", [])
    return {"time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "work": m["work"], "entry": m["entry"], "qid": m["qid"], "property": label,
            "wikidata_value": m["theirs"], "wikidata_sources": m["refs"], "entry_value": m["entry_value"],
            "decision": decide(evidence, verdict.get("established"), m["work"], label) if verdict else "retry",
            "established": (verdict or {}).get("established", ""),
            "evidence": json.dumps(evidence, ensure_ascii=False),
            "note": (verdict or {}).get("note", "no verdict returned"), "model": MODEL, "searches": searches,
            "input_tokens": tokens_in, "output_tokens": tokens_out}


def check(*case):
    prompt, meta = prompt_for(*case)
    return logged_row(meta, *answer(prompt))


BATCHES = WORK / "conflicts"
SOURCED = ROOT / "conflict_sourced.csv"


def cited_as(claims):
    """Where Wikidata's values say they come from: the stated-in works and reference URLs, as evidence entries."""
    found = []
    for c in claims:
        for ref in c.get("references", []):
            snaks = ref.get("snaks", {})
            works = [s["datavalue"]["value"]["id"] for s in snaks.get("P248", []) if s.get("snaktype") == "value"]
            urls = [s["datavalue"]["value"] for s in snaks.get("P854", []) if s.get("snaktype") == "value"]
            if works or urls:
                found.append({"url": urls[0] if urls else f"https://www.wikidata.org/wiki/{works[0]}",
                              "says": "stated in " + ", ".join(show({"value": {"id": w}}) for w in works) if works
                              else "reference URL", "supports": "wikidata"})
    return found


def sourced():
    """The 'sourced' outcome of the request: Wikidata's value cites another source, so two sources disagree and the
    entry's value goes beside it at the same rank. Nothing is searched; each case is logged with Wikidata's sources."""
    new = not SOURCED.exists()
    n = 0
    with open(SOURCED, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        for r, label, ours, claims, refs, e in open_cases(sourced=True, log=SOURCED):
            w.writerow({"time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "work": r["work"], "entry": r["entry"], "qid": r["qid"], "property": label,
                        "wikidata_value": "; ".join(show(c["mainsnak"]["datavalue"]) for c in claims),
                        "wikidata_sources": refs, "entry_value": show(ours), "decision": "both", "established": "",
                        "evidence": json.dumps(cited_as(claims), ensure_ascii=False),
                        "note": "Wikidata's value cites another source: two sources disagree, so both values stand "
                                "side by side (no search)", "model": "none", "searches": 0,
                        "input_tokens": 0, "output_tokens": 0})
            n += 1
            if n % 500 == 0:
                f.flush()
                print(n, "sourced disagreements logged", flush=True)
    print(n, "sourced disagreements logged in", SOURCED.name)


def submit(limit):
    """Up to `limit` open cases as one batch; the prompts and case data are kept in work/conflicts/<batch>.json."""
    cases = {}
    for n, case in enumerate(open_cases(), 1):
        if n > limit:
            break
        prompt, meta = prompt_for(*case)
        cases[f"case-{n}"] = {"prompt": prompt, "meta": meta}
    b = call("messages/batches", {"requests": [
        {"custom_id": cid, "params": {"model": MODEL, "max_tokens": 3000, "tools": TOOLS,
                                      "messages": [{"role": "user", "content": c["prompt"]}]}}
        for cid, c in cases.items()]})
    BATCHES.mkdir(parents=True, exist_ok=True)
    (BATCHES / f"{b['id']}.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print(f"batch {b['id']}: {len(cases)} cases submitted", flush=True)
    return b["id"]


def collect(batch_id):
    """Wait for the batch to end, then log each case's answer (a paused search is continued by a plain request)."""
    cases = json.loads((BATCHES / f"{batch_id}.json").read_text(encoding="utf-8"))
    while True:
        b = call(f"messages/batches/{batch_id}")
        if b["processing_status"] == "ended":
            break
        print(f"batch {batch_id}: {b['request_counts']}", flush=True)
        time.sleep(300)
    new = not LOG.exists()
    with open(LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        for line in call(b["results_url"], raw=True).splitlines():
            if not line.strip():
                continue
            res = json.loads(line)
            c = cases[res["custom_id"]]
            if res["result"]["type"] == "succeeded":
                row = logged_row(c["meta"], *answer(c["prompt"], res["result"]["message"]))
            else:                                   # errored or expired: checked again later
                row = logged_row(c["meta"], [], 0, 0, 0)
                row["note"] = f"batch result {res['result']['type']}"
            w.writerow(row)
            f.flush()
            print(row["entry"], row["property"], "| WD", row["wikidata_value"], "| entry", row["entry_value"],
                  "|", row["decision"], "| searches", row["searches"], "| tokens", row["input_tokens"],
                  row["output_tokens"], flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "models":
        return models()
    if mode == "batch":
        return collect(submit(int(sys.argv[2]) if len(sys.argv) > 2 else 20))
    if mode == "collect":
        return collect(sys.argv[2])
    if mode == "sourced":
        return sourced()
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
