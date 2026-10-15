# oka-wikidata-bot

Source code of [OKA bot](https://www.wikidata.org/wiki/User:OKA_bot) on Wikidata. It is run by the
[Open Knowledge Association](https://meta.wikimedia.org/wiki/OKA) (operator:
[User:7804j](https://www.wikidata.org/wiki/User:7804j)).
The same account uploads swisstopo photographs to Commons: [oka-commons-bot](https://github.com/Open-Knowledge-Association/oka-commons-bot).

## Tasks and data sources

| Task | Approval | Data | Log |
|---|---|---|---|
| 1. Geographic features from swisstopo | [OKA bot](https://www.wikidata.org/wiki/Wikidata:Requests_for_permissions/Bot/OKA_bot) (8 October 2026) | [swissNAMES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d) ([Q141593662](https://www.wikidata.org/wiki/Q141593662)), [swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d) ([Q141599379](https://www.wikidata.org/wiki/Q141599379)); approved for later batches: swissTLM3D, swissBATHY3D | [edits.csv](edits.csv), [corrections.csv](corrections.csv) |
| 2. Facts from reference works | [OKA bot 2](https://www.wikidata.org/wiki/Wikidata:Requests_for_permissions/Bot/OKA_bot_2) (14 October 2026) | [Historical Dictionary of Switzerland](https://hls-dhs-dss.ch/) (HDS, [Q642074](https://www.wikidata.org/wiki/Q642074), ID [P902](https://www.wikidata.org/wiki/Property:P902)); [Store norske leksikon](https://snl.no/) (SNL, [Q746368](https://www.wikidata.org/wiki/Q746368), [P4342](https://www.wikidata.org/wiki/Property:P4342)) and the other works on snl.no: [Norsk kunstnerleksikon](https://nkl.snl.no/) ([Q11992482](https://www.wikidata.org/wiki/Q11992482), [P5081](https://www.wikidata.org/wiki/Property:P5081)) and [Store medisinske leksikon](https://sml.snl.no/) ([Q19378370](https://www.wikidata.org/wiki/Q19378370), [P5082](https://www.wikidata.org/wiki/Property:P5082)) | [dictionary_edits.csv](dictionary_edits.csv), [dictionary_left_alone.csv](dictionary_left_alone.csv) |

Both tasks make one edit per item, at most 60 edits a minute (new items at most 20 a minute), with `maxlag=5`.
Every statement the bot adds or confirms cites its source (*stated in*, the entry's identifier where there is one,
and *retrieved*). The bot logs in with a [bot password](https://www.mediawiki.org/wiki/Manual:Bot_passwords) read
from the environment. Its [user page](https://www.wikidata.org/wiki/User:OKA_bot) shows what each task has done.

**What the bot changes.** It adds statements, sources, labels, aliases and new items. It does not remove other
editors' statements, and does not change existing labels, descriptions or classes. Existing statements are changed
only in these ways:

- a source is added to a statement that has the same value;
- a position saved long ago without a precision, which Wikidata no longer accepts, gets the precision its digits
  imply (the position itself is not changed);
- an identifier that its source now redirects to a new one keeps its value and gets deprecated rank with
  *reason for deprecated rank: redirect* ([Q45403344](https://www.wikidata.org/wiki/Q45403344)), the way Wikidata
  already marks such identifiers, and the current identifier is added;
- the bot's own mistakes are corrected, and each correction is logged. For example, 139 items it had created on the
  Swiss border got the neighbouring country and a "… on the border between Switzerland and X" description
  ([corrections.csv](corrections.csv)).

The code was written with the help of an AI coding assistant (Claude). The bot itself uses no AI: every edit
follows the fixed rules below.

## Task 1: geographic features from swisstopo

Data from the Swiss Federal Office of Topography, published under its
[terms of use for free geodata](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices)
("Open use. Must provide the source"):

- [swissNAMES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d)
  ([Q141593662](https://www.wikidata.org/wiki/Q141593662)): official names, their language, feature type, position and height;
- [swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d)
  ([Q141599379](https://www.wikidata.org/wiki/Q141599379)): municipality and canton of a point.

The approved request also covers swissTLM3D (lake and glacier areas, river lengths) and swissBATHY3D (lake depths)
for later batches.

**Batches** (22,285 edits by 12 October 2026: 11,585 new items, 10,700 edits to existing items):

| Batch | Items | Edits | New items |
|---|---|---|---|
| first | mountains, summits, hills, passes, lakes, reservoirs, glaciers | 5,538 | 3,546 |
| `natural` | valleys, ridges, waterfalls, caves, minor summits | 118 | 6,876 |
| `settlements` | localities and neighbourhoods | 2,125 | 1,163 |
| `p131` | buildings and other items with a position but no municipality | 2,919 | – |

**Matching.** An item is paired with a swissNAMES3D feature when one of its labels or aliases is exactly the
feature's name, the types are compatible, the feature lies within the type's distance (250 m for summits, passes,
waterfalls and caves; 1.5 km for localities, 1 km for neighbourhoods; 2 km for lakes, glaciers, valleys and ridges,
whose swissNAMES3D point is the centre of their outline), and the pairing is unique both ways.

**For a paired item**, in one edit, the bot:

- adds swisstopo as a source to a height that matches in whole metres, and to a position within 50 m;
- adds a missing height (summits and lakes only);
- where an existing value's only source is an import from a Wikipedia and it is more than 5 m (height) or 50 m
  (position, summits only) off, adds swisstopo's value with preferred rank and *reason for preferred rank: best
  referenced value*, keeping the old statement;
- adds the default label (`mul`) when swisstopo has a single name, the label in the name's own language, and
  swisstopo's names in other languages as labels (or aliases when a different label exists);
- adds the official name (P1448) and, for point features without one, the municipality (P131).

Pass heights are only sourced when they already match: swissNAMES3D gives the height of the point where the name is
placed, which for a pass can sit beside the saddle. If someone edits an item between reading and saving, the save is
refused and the item is skipped. If a new label clashes with another item's label and description, that language is
left out.

**New items.** Official features that have no item get one, with labels, a "[type] in the canton of X, Switzerland"
description in English, German, French and Italian, class, country, position, height (summits, hills and lakes),
official name and municipality. Before creating, a live search checks that no item of the same name exists within
2 km (and, for valleys, ridges and waterfalls, no item of the same kind within 10 km; summits and hills 5 km;
localities and neighbourhoods 3 km, as same-named villages far apart are normal). Since the border correction,
nothing is created whose point lies outside Swiss municipalities (swissNAMES3D also covers Liechtenstein and border
features).

- Caves are only edited, never created: swisstopo's type "Grotte, Hoehle" also covers mine entrances, Lourdes
  grottoes and natural arches.
- Localities get new items only from 100 to 9,999 inhabitants (swisstopo's population classes): larger ones all have
  items already, and 60,722 of swissNAMES3D's 68,508 localities have fewer than 100 inhabitants (55,156 fewer than
  20): mostly single farms and hamlets, which would become about 60,000 items with little more than a name and a
  point, many duplicating existing items for the farmhouse.
- The `p131` batch adds only the municipality: buildings, archaeological sites, fountains and similar items are not
  named features in swissNAMES3D, so the only fact swisstopo gives about them is the municipality their position lies
  in ([swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d)). Items that span
  municipalities (waters, glaciers, regions, roads, railway lines, bridges, tunnels) are left out.

**Running:**

```
python pipeline.py plan                 # download the latest data, build work/plan.jsonl (all local)
python pipeline.py preview --n 6000     # totals of what the run would change (read-only)
python pipeline.py run --batches 4 --batch-size 500 --rate 1
python pipeline.py verify
```

Add `--plan natural` (or `settlements`, `p131`) to `plan`, `preview`, `run`, `verify` and `status` for a later
batch. Each batch has its own plan (`work/plan.jsonl`, `work/plan_<batch>.jsonl`); in edits.csv the first batch's
rows are of kind `edit` or `create` and later ones `edit:<batch>` or `create:<batch>`, so an item done in one batch
is checked again in the next, and edited only if something is still missing. The runner checks each batch on
Wikidata after saving and stops on any problem; runs resume where they stopped.

## Task 2: facts from reference works

**Sources.** Only facts are taken; no text is copied or stored.

- HDS: the [open data](https://hls-dhs-dss.ch/de/opendata) (CC0: article lists with life years, and the HDS's links to
  GND and VIAF), and the facts on each article page (exact dates and places), read one page every 21 seconds as the
  site's robots.txt asks.
- SNL, NKL and SML: the metadata of each entry from snl.no's JSON API (dates, places, occupations, family,
  identifiers), which snl.no marks as free to reuse.

**Matching.** An entry and an item match through the entry's identifier on the item (P902, P4342, P5081, P5082).
Entries whose identifier sits on several items, or items with several identifiers of the same work, are left out.
HDS entries that no item carries yet are matched through the Wikipedia articles that cite them (for topics, only
matches checked by hand), and the bot adds their HDS ID.

**Facts.** For people: dates of birth and death (Julian calendar before 1583 or where the source says so; dates the
source marks as approximate or doubtful are not used; a baptism date becomes P1636), places of birth, death and
burial, work locations, occupations (from a fixed list of words), sex, pseudonyms, other names (as aliases),
relatives (when the source links their entry, or names them with life years that match exactly one item), and
identifiers the entry gives (KulturNav, historical population register; for the HDS, GND and VIAF when they are
checked to be current). For organisations: inception, dissolution, legal form, official name, organisation number
and headquarters. For places: municipality number, demonym, area, administrative centre and highest point.

**Places** are matched by exact name, with fixed rules:

- in Norway, a city, then a municipality, then a former municipality, then an urban area or settlement;
  "Kristiania (nå Oslo)" and old names such as Kristiania (an alias of Oslo) are understood;
- abroad, "Madrid, Spania" or "Lombard, Illinois, USA": a city, then any town of that country or inside that state
  or constituent country;
- a well-known city without a country: a city with 20 Wikipedia articles and three times the next candidate, or any
  town with 100;
- a place of death "samme sted" ("same place") is the place of birth.
  A name that is a country's or a region's (England, Wales) is never taken as a town's.

**Edits.** One per item. A fact is added only when the item has no value for it; an equal value gets the entry as a
source; a different value is left alone and the case is listed in
[dictionary_left_alone.csv](dictionary_left_alone.csv) for review. Where SNL has renamed an entry (or moved an NKL
entry to snl.no), its old address redirects: the entry is read at the new address, the item's old identifier is kept
with deprecated rank (*reason: redirect*) and the current one is added.

**Reports to the sources.** Errors found in the sources are listed in [source_issues.csv](source_issues.csv); the
HDS's links to GND and VIAF that are merged or deleted are in [hds_link_issues.csv](hds_link_issues.csv).

**Running:**

```
python dictionaries_run.py crawl-hds      # read HDS article pages (one every 21 s)
python dictionaries_run.py plan snl       # also nkl, sml, hds: work out the facts (read-only, resumable)
python dictionaries_run.py run            # edit; the works in turn, 50 entries per batch
```

## Licence

MIT, see [LICENSE](LICENSE). The data are not part of this repository; the scripts download them.
