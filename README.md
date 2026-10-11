# oka-wikidata-bot

Source code of [OKA bot](https://www.wikidata.org/wiki/User:OKA_bot) on Wikidata. It is run by the
[Open Knowledge Association](https://meta.wikimedia.org/wiki/OKA) (operator:
[User:7804j](https://www.wikidata.org/wiki/User:7804j)); bot approval:
[Wikidata:Requests for permissions/Bot/OKA bot](https://www.wikidata.org/wiki/Wikidata:Requests_for_permissions/Bot/OKA_bot).
The same account uploads swisstopo photographs to Commons: [oka-commons-bot](https://github.com/Open-Knowledge-Association/oka-commons-bot).

## Task: geographic feature updates from swisstopo data

Data from the Swiss Federal Office of Topography, published under its
[terms of use for free geodata](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices)
("Open use. Must provide the source"); every statement the bot adds or confirms cites the dataset:

- [swissNAMES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d)
  ([Q141593662](https://www.wikidata.org/wiki/Q141593662)): official names, their language, feature type, position and height;
- [swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d)
  ([Q141599379](https://www.wikidata.org/wiki/Q141599379)): municipality and canton of a point.

Items covered: Swiss mountains, summits, hills, passes, lakes, reservoirs and glaciers (first batch), and valleys,
ridges, waterfalls and caves (second batch, `natural`). An item is paired with a swissNAMES3D feature when one of its
labels or aliases is exactly the feature's name, the types are compatible, the feature lies within 250 m (summits,
passes, waterfalls, caves) or 2 km (lakes, glaciers, valleys, ridges: their swissNAMES3D point is the centre of their
outline), and the pairing is unique both ways.

For a paired item, in one edit, the bot:

- adds swisstopo as a source to a height that matches in whole metres, and to a position within 50 m (if that
  position was saved long ago without a precision, which Wikidata no longer accepts, the bot fills in the precision
  its digits imply, for example to the arcsecond; the position itself is not changed);
- adds a missing height (summits and lakes only; heights of valleys, ridges, waterfalls and caves are left alone);
- where an existing value's only source is an import from a Wikipedia and it is more than 5 m (height) or 50 m
  (position, summits only) off, adds swisstopo's value with preferred rank and *reason for preferred rank: best
  referenced value*, keeping the old statement;
- adds the default label (`mul`) when swisstopo has a single name, the label in the name's own language,
  and swisstopo's names in other languages as labels (or aliases when a different label exists);
- adds the official name (P1448) and, for summits, passes, waterfalls and caves without one, the municipality (P131).

It never removes anything and never changes existing labels, descriptions or classes. Pass heights are only sourced
when they already match: swissNAMES3D gives the height of the point where the name is placed, which for a pass can
sit beside the saddle. If someone edits an item between reading and saving, the save is refused and the item is
skipped. If a new label clashes with another item's label and description, that language is left out.

Official features of these types that have no item (lakes, glaciers, passes, main summits and hills) get a new item
with labels, a "[type] in the canton of X, Switzerland" description in English, German, French and Italian, class,
country, position, height (not for passes and glaciers), official name and municipality. A live search first
checks that no item with the same name exists within 2 km.

The second batch creates valleys, ridges, waterfalls and minor summits, hills and rock heads (class summit or hill),
with a height for the summits and hills only and a municipality for all but valleys and ridges. It creates nothing
whose point lies outside Swiss municipalities (swissNAMES3D also covers Liechtenstein), and it also counts as a
duplicate an item of the same kind and name within 10 km (valleys, ridges, waterfalls) or 5 km (summits, hills),
unless that item is paired with another swisstopo feature: such items can sit far from swisstopo's point. Caves are
only edited, never created: swisstopo's cave type also covers mine entrances and Lourdes grottoes.

## Running

```
python pipeline.py plan                 # download the latest data, build work/plan.jsonl (all local)
python pipeline.py preview --n 6000     # totals of what the run would change (read-only)
python pipeline.py run --batches 4 --batch-size 500
python pipeline.py verify
```

Add `--plan natural` to `plan`, `preview`, `run`, `verify` and `status` for the second batch. Each batch has its own
plan (`work/plan.jsonl`, `work/plan_natural.jsonl`); in edits.csv the first batch's rows are of kind `edit` or
`create` and the second batch's `edit:natural` or `create:natural`, so an item done in one batch is checked again in
the next, and edited only if something is still missing.

The bot logs in with a [bot password](https://www.mediawiki.org/wiki/Manual:Bot_passwords) read from the user
environment, respects `maxlag=5`, makes at most 20 edits per minute (the approved rate), checks each batch on Wikidata after saving and
stops on any problem. Runs resume where they stopped. Every saved edit is listed in [edits.csv](edits.csv), and the
bot's [user page](https://www.wikidata.org/wiki/User:OKA_bot) is updated after each run.

The code was written with the help of an AI coding assistant (Claude). The bot itself uses no AI: every edit
follows the fixed rules above.

## Licence

MIT, see [LICENSE](LICENSE). The swisstopo data are not part of this repository; the scripts download them.
