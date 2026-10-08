"""Turn one live item plus its swisstopo feature into a single edit, following the approved rules.

Rules (Wikidata:Requests for permissions/Bot/OKA bot, after review):
- nothing is removed; existing labels, descriptions and classes are never changed;
- heights are whole metres; a matching height or position gets a reference;
- a missing height is added for summits and lakes (not passes, glaciers or reservoirs: a pass's swissNAMES3D
  point can sit beside the saddle, a glacier spans many heights, a reservoir's level changes);
- if an existing value's only source is a Wikipedia import and it is more than 5 m (height) or 50 m (position,
  summits only) off, swisstopo's value is added with preferred rank and "reason: best referenced value";
- default label (mul) when swisstopo has a single name, plus the label in the name's own language;
  names in other languages become labels, or aliases when a different label exists;
- official name (P1448) for swisstopo's official names; municipality (P131) for summits and passes without one;
- new items get labels, a "[type] in [canton], Switzerland" description, class, country, position, height
  (not for passes and glaciers), official name and municipality, every statement referenced.
"""
import copy
import re

from common import (CANTONS, GENERIC, METRE, OFFICIAL_TYPES, RADIUS, SWISSBOUNDARIES3D, SWISSNAMES3D, TYPE_WORDS,
                    cites, coord_claim, dist_m, height_claim, item_claim, lv95_to_wgs84, mono_claim, only_import_refs)


def _amount(c):
    return round(float(c["mainsnak"]["datavalue"]["value"]["amount"]))


def name_changes(raw, rec, ref):
    labels, aliases, claims, done = {}, {}, [], []
    have = {l: v["value"] for l, v in raw.get("labels", {}).items()}
    have_alias = {l: {a["value"].casefold() for a in al} for l, al in raw.get("aliases", {}).items()}
    official = rec["status"] == "offiziell" and rec["ntype"] in OFFICIAL_TYPES and rec["lang"]
    one_name = len({n["name"] for n in rec.get("names", [])} | {rec["name"]}) == 1
    if official and one_name and "mul" not in have:          # Help:Default values for labels and aliases
        labels["mul"] = {"language": "mul", "value": rec["name"]}
        done.append("default label")
    if official and rec["lang"] not in have:                  # kept: the Nearby feature does not use mul labels
        labels[rec["lang"]] = {"language": rec["lang"], "value": rec["name"]}
        done.append(f"{rec['lang']} label")
    added = []
    for n in rec.get("names", []):
        lang, name = n["lang"], n["name"]
        if name == rec["name"] or lang in labels:
            continue
        if lang not in have:
            labels[lang] = {"language": lang, "value": name}
            added.append(f"{lang} label")
        elif have[lang].casefold() != name.casefold() and name.casefold() not in have_alias.get(lang, set()):
            aliases.setdefault(lang, []).append({"language": lang, "value": name, "add": ""})
            added.append(f"{lang} alias")
    if added:
        done.append("names in other languages (" + ", ".join(added) + ")")
    existing = {(c["mainsnak"]["datavalue"]["value"]["text"], c["mainsnak"]["datavalue"]["value"]["language"])
                for c in raw.get("claims", {}).get("P1448", []) if c["mainsnak"]["snaktype"] == "value"}
    offs = [(rec["name"], rec["lang"])] if official else []
    offs += [(n["name"], n["lang"]) for n in rec.get("names", []) if n["status"] == "offiziell" and n["ntype"] in OFFICIAL_TYPES]
    new = [o for o in dict.fromkeys(offs) if o not in existing]
    claims += [mono_claim("P1448", text, lang, ref) for text, lang in new]
    if new:
        done.append("official name")
    return labels, aliases, claims, done


def build_edit(raw, row, refs):
    """Return (data for wbeditentity or None, list of changes or a reason it was skipped)."""
    rec, ref = row["rec"], refs[SWISSNAMES3D]
    if raw.get("redirects") or "missing" in raw:
        return None, ["skipped: redirect or deleted"]
    item_names = {v["value"] for v in raw.get("labels", {}).values()} | \
                 {a["value"] for al in raw.get("aliases", {}).values() for a in al}
    if rec["name"] not in item_names:
        return None, ["skipped: name no longer on item"]
    claims = raw.get("claims", {})
    lat, lon = lv95_to_wgs84(rec["e"], rec["n"])
    coords = [c for c in claims.get("P625", []) if c["mainsnak"]["snaktype"] == "value" and c["rank"] != "deprecated"]
    if not coords:
        return None, ["skipped: no coordinates"]
    cv = coords[0]["mainsnak"]["datavalue"]["value"]
    offset = dist_m(cv["latitude"], cv["longitude"], lat, lon)
    if offset > RADIUS[rec["group"]]:
        return None, ["skipped: coordinates moved"]

    out_claims, done, z, group = [], [], rec["z"], rec["group"]
    heights = [c for c in claims.get("P2044", []) if c["mainsnak"]["snaktype"] == "value"
               and c["mainsnak"]["datavalue"]["value"].get("unit") == METRE and c["rank"] != "deprecated"]
    best = [c for c in heights if c["rank"] == "preferred"] or heights
    height_ok = group in ("summit", "pass", "lake") and not row.get("reservoir")
    if height_ok:
        if not claims.get("P2044"):
            if group != "pass":
                out_claims.append(height_claim(z, ref))
                done.append("height")
        elif best:
            same = [c for c in best if _amount(c) == z]
            if same and not cites(same[0], SWISSNAMES3D):
                c = copy.deepcopy(same[0])
                c.setdefault("references", []).append(ref)
                out_claims.append(c)
                done.append("source for height")
            elif (not same and group != "pass" and min(abs(_amount(c) - z) for c in best) > 5
                  and not any(c["rank"] == "preferred" for c in best) and all(only_import_refs(c) for c in best)):
                out_claims.append(height_claim(z, ref, "preferred"))
                done.append("swisstopo height (preferred)")
    # an old coordinate without a precision can't be resubmitted unchanged, so it gets no reference
    if offset <= 50 and cv.get("precision") is not None and not cites(coords[0], SWISSNAMES3D):
        c = copy.deepcopy(coords[0])
        c.setdefault("references", []).append(ref)
        out_claims.append(c)
        done.append("source for position")
    elif (offset > 50 and group == "summit" and not any(c["rank"] == "preferred" for c in coords)
          and all(only_import_refs(c) for c in coords)):
        out_claims.append(coord_claim(lat, lon, ref, "preferred"))
        done.append("swisstopo position (preferred)")
    labels, aliases, name_claims, name_done = name_changes(raw, rec, ref)
    out_claims += name_claims
    done += name_done
    if row.get("munis") and not claims.get("P131"):
        out_claims += [item_claim("P131", m, refs[SWISSBOUNDARIES3D]) for m in row["munis"]]
        done.append("municipality")
    data = {k: v for k, v in (("claims", out_claims), ("labels", labels), ("aliases", aliases)) if v}
    return (data or None), done


def descriptions(cls, canton):
    if canton in CANTONS and cls in TYPE_WORDS:
        en, de, fr_prep, fr, it = CANTONS[canton]
        t_en, t_de, t_fr, t_it = TYPE_WORDS[cls]
        texts = (f"{t_en} in the canton of {en}, Switzerland", f"{t_de} im Kanton {de}, Schweiz",
                 f"{t_fr} du canton {fr_prep}{fr}, en Suisse", f"{t_it} del Canton {it}, Svizzera")
    else:
        texts = GENERIC[cls]
    return {l: {"language": l, "value": t} for l, t in zip(("en", "de", "fr", "it"), texts)}


def build_new(row, refs):
    rec, cls, ref = row["rec"], row["cls"], refs[SWISSNAMES3D]
    lat, lon = lv95_to_wgs84(rec["e"], rec["n"])
    claims = [item_claim("P31", cls, ref), item_claim("P17", "Q39", ref), coord_claim(lat, lon, ref)]
    if cls not in ("Q35666", "Q133056"):                     # no height for glaciers (a range) or passes (name point)
        claims.append(height_claim(rec["z"], ref))
    labels, aliases, name_claims, _ = name_changes({}, rec, ref)
    claims += name_claims
    claims += [item_claim("P131", m, refs[SWISSBOUNDARIES3D]) for m in row.get("munis", [])]
    data = {"labels": labels, "descriptions": descriptions(cls, row.get("canton")), "claims": claims}
    if aliases:
        data["aliases"] = aliases
    return data


def drop_language(data, done, lang):
    """After a label/description clash in one language, leave out what the edit adds in that language."""
    for k in ("labels", "descriptions"):
        data.get(k, {}).pop(lang, None)
    data = {k: v for k, v in data.items() if v}
    done = [d for d in done if d not in (f"{lang} label", "default label" if lang == "mul" else "")]
    done = [re.sub(rf"\b{lang} (label|alias)(, )?", "", d).replace(" ()", "").replace(", )", ")")
            if d.startswith("names in") else d for d in done]
    return data, [d for d in done if d != "names in other languages"] + [f"{lang} label left out (clash with another item)"]
