# Prevention data observatory

A small static site that presents a scan of published work on measuring prevention:
what has been measured, how, and whether the underlying data can actually be obtained.

Live at `https://maiquang04.github.io/prevention-data-scan/` once GitHub Pages is switched on.

## Running it locally

The pages read their content from `data/*.json` with `fetch()`, and browsers block that
when a file is opened straight off disk. Double-clicking `index.html` will show a "could
not load the data" message. Start a small server instead:

```
python -m http.server 8000
```

then open <http://localhost:8000>.

## Running with Docker

This runs the site, the search page and the language model on one machine. Docker is
the only thing to install; the model answers on that machine, so no question leaves it.

1. **Get the model file.** Download `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (about 2.5 GB,
   Apache 2.0 licence) from
   <https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF> into a
   folder called `models/` here. It is gitignored. If LM Studio already has it, point
   `MODEL_DIR` at LM Studio's folder instead of copying it (see Settings below).
2. **Choose where the model runs.** Create a file called `.env` beside
   `docker-compose.yml` containing one line:

   ```
   COMPOSE_PROFILES=gpu
   ```

   `gpu` runs the model on an NVIDIA graphics card, which needs the NVIDIA driver, and
   on Linux the NVIDIA Container Toolkit. Use `cpu` on a machine without one. It
   matters: on the laptop this was tested on, a question took 2 to 5 seconds on its
   4 GB graphics card and 18 to 60 seconds on its processor alone.
3. **Start it:**

   ```
   docker compose up -d
   ```

   The first start downloads the images. The model then takes a little while to load;
   until it has, the search page says the model is unavailable and ranks by words.
4. **Open** <http://127.0.0.1:8765/ask.html> for the search, or
   <http://127.0.0.1:8765/> for the site.

`docker compose logs -f search` shows each question as it is answered, and
`docker compose down` stops everything.

Without `COMPOSE_PROFILES`, `up` starts the search page but no model, and `down` leaves
a running model behind. Either set it in `.env` as above, or name the profile in every
command: `docker compose --profile gpu up -d`, `docker compose --profile gpu down`.

**After changing `data/sources.csv`**, rebuild the data and restart the search, which
reads the data once when it starts:

```
docker compose run --rm export
docker compose restart search
```

### Settings

Put any of these in a file called `.env` beside `docker-compose.yml` (gitignored), one
`NAME=value` per line.

| Setting | Default | What it is |
|---|---|---|
| `COMPOSE_PROFILES` | none | `gpu` or `cpu`: where the model runs |
| `MODEL_DIR` | `./models` | Folder holding the model file |
| `MODEL_FILE` | `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` | The model file's name |
| `MODEL_CONTEXT` | `4096` | Tokens the model can read at once. The prompt is about 1,500 |
| `SEARCH_PORT` | `8765` | Port the page is published on |
| `LLM_URL` | the model container | Any other OpenAI-compatible model server |
| `HOST_UID`, `HOST_GID` | `1000` | Linux only: who owns the files the export writes |

To use LM Studio on the same computer instead of the model container, set
`LLM_URL=http://host.docker.internal:1234/v1` and start only the search:
`docker compose up -d --no-deps search`.

### Not the same answers as LM Studio

Same model file, same prompt, temperature 0, but llama.cpp's server and LM Studio do
not choose identical tags. On the 80 test questions (11 November) the container gave the
same tags as the 9 October LM Studio run on 33 of them, and found the right source in the
top ten 69% of the time against 76%. One reply came back unreadable. Whether that is
llama.cpp's own settings or LM Studio having changed since October has not been checked;
the demo questions all behaved as expected (`node search/ui_check.mjs`).

### What this does not do yet

The page is published on `127.0.0.1` only, so it can be used on the machine running it
and nowhere else. Opening it to a network is a decision for whoever runs the server,
and needs a proper web server with a sign-in in front of it: `search/serve.py` is
Python's built-in web server, which is not built to face the internet.

## Where the content comes from

`data/sources.csv` is the source of truth: one row per source, one column per field.
Nothing in `data/studies.json` or `data/meta.json` is edited by hand. Regenerate them after
changing the CSV:

```
python build/export_data.py
```

No packages to install; it is the standard library only.

**Editing the CSV in Excel** works, with two rules. Save it with Save As > **CSV UTF-8
(Comma delimited)**, because the plain "CSV" choice writes the computer's own code page and
the export stops rather than guess. And close it in Excel before running the export. Any
text editor works too.

**Each row's ID** (`app1-03`) is the address of that source's page, so it never changes
once published. A new source takes the next free number under the topic it belongs to
most, e.g. `app2-15` after `app2-14`. Removing a source means deleting its row; its old
link then shows "not found".

That command also re-stamps the stylesheet and script links with a hash of each file
(`assets/common.js?v=cbf15586`). GitHub Pages caches assets for ten minutes and its CDN
caches them too, so without the stamp a visitor can hold the new copy of one file and the
old copy of another — which has already broken the live site once. If only the CSS or JS
changed and there is no reason to regenerate the data, run the stamping on its own:

```
python build/stamp_assets.py
```

```
data/sources.csv                    source of truth, edited in Excel or a text editor
        │
        │  build/export_data.py
        ▼
data/studies.json, data/meta.json   generated — never hand-edit
        │
        │  fetch() at page load
        ▼
the pages
```

`data/quadrants.json` is the exception: it is written by hand, because deciding whether a
measure belongs under People or under Places is a judgement, not something to infer from a
column.

### Suggestions from contributors

People who should not edit `data/sources.csv` directly can suggest new sources in a
CSV template:

```
python build/source_templates.py           write it into templates/
python build/source_templates.py --check   check it without rewriting
```

`resource-entry-template.csv` is one row per new source, with the allowed tag values in
each column heading. It is built from `data/`, so regenerate it after an export.
Corrections to existing sources can simply be sent by email. A returned file is read by whoever maintains the site and copied into
`data/sources.csv` by hand; nothing imports it automatically, because duplicates,
conflicting tags and unchecked claims need a person to look at them.

### The three controlled columns

Filtering needs clean categories, which the CSV's prose columns cannot give. Three
columns exist purely to drive the filters, and `export_data.py` stops with an error if a
cell holds anything outside these lists.

**Access** — one value per row:

| Value | Means |
|---|---|
| `Public` | Can be downloaded now, at no cost |
| `Public (aggregate only)` | Published results and maps are free; the records behind them are not released |
| `Restricted - application` | The data exists but you have to apply to whoever holds it |
| `Not a dataset` | A review, guide or set of principles rather than data |
| `Not yet assessed` | We have not checked this one yet |

**Geo tags** — one or more, separated by semicolons:

`Address`, `Small area`, `LGA`, `PHN`, `HHS`, `Remoteness`, `State`, `National`,
`International`, `Individual`, `Any`

Three of those are abbreviations: `LGA` is a local government area, `PHN` a Primary Health
Network, `HHS` a Hospital and Health Service. The site keeps them short and shows the full
name on hover, from `TAG_TITLE` in `assets/common.js`. The browse page's "Geographic level"
filter is the exception and spells them out, because hover is no use on a phone. Adding
another abbreviation to this list means adding it there too.

`Small area` covers any sub-council statistical unit — Statistical Area Level 1, 2 or 3, the
older Statistical Local Area, a census tract. The exact unit each source uses is spelled out
in its own "Geographic level" text; the tag is only the coarse bucket used for filtering.

**Domain** — which health issue a source speaks to. None, one or several, separated by
semicolons:

`Physical Activity`, `Healthy Eating`, `Implementation`, `Social`, `Equity`,
`Prosperity & Productivity`, `Mental Wellbeing`

These are not our words. They are the seven domains from the partner agency's own measures
spreadsheet, kept in their wording and their order so a tag here means to them what it means
here. Unlike Access and Geo tags, **blank is allowed and is common** — 17 of the 61 sources
have no domain, because a text about how to build a composite index, or one about hospital
admission rates, is not about a health issue. Filling the column in for the sake of filling
it in would be worse than leaving it empty. A domain no source carries yet is hidden from the
filter rather than shown with a count of zero.

**Indicators** — which of the agency's named measures a source actually uses. None, one or
several, separated by semicolons. The 61 allowed values live in `data/indicators.json`, not
in the export script, because the agency revises the list and updating it should be an edit
to a data file rather than a code change. Add to that file first: the export stops on any
value it does not recognise.

The rule for filling it in:

> Assign an indicator only if the source explicitly names, computes, validates, or directly
> provides the data needed for that exact measure.

"Validates" is why the WHO-5 paper carries one. "Directly provides the data needed" is why
the datasets do: an agency does not need a source to publish the finished measure, it needs
to know where the raw material is. Belonging to the same health focus is **not** enough — a
source can be about physical activity and match no specific measure.

Blank is the usual answer, and is right. 18 of the 61 sources carry an indicator and 43 do
not. Deciding whether a paper loosely counts as "Community belonging" is a judgement, and a
guess here puts wrong information in front of a government agency. The agency offered to
fill the rest in themselves, so a blank cell is a question for them and a wrong tag is
something they have to catch. `tests/fill_indicators.py` quotes the evidence beside every
tag, and lists the sources deliberately left untagged with the reason.

**In the filter panel the measures are a sub-level of Health focus**, not a group of their
own. Each of the seven domains carries a fold reading "18 measures", and opening it shows
that domain's measures as nested checkboxes. The agency's spreadsheet is built the same way,
a Domain column with an Indicator column inside it, so a reader who knows the source
document finds them where they expect. It also keeps 61 checkboxes from becoming an eighth
group taller than the other seven together.

Mechanically the indicator facet stays in `FACETS` — that is what gives it a state key, a
share of the URL and its counts — but carries `nested: true` so `renderFilters()` skips
drawing it, and the domain facet carries `childKey` and `children()` to draw it underneath.
The folds are shut by default and open when something inside them is ticked, so arriving on
a shared `?indicator=` link never shows a closed panel with no sign of what is filtering.

Ticking a domain ticks every measure under it, which is what a nested checkbox tree is
expected to do and has one consequence worth knowing: `?domain=Physical Activity` returns
18 sources, one more than the 17 carrying that domain tag. The extra is `app2-07`
(Queensland Globe), which carries no domain but does carry two Physical Activity measures,
and it belongs in that filter. `tests/domainfacet.mjs` prints both counts side by side.

This is the one filter that keeps its zeros. Everywhere else an option reading zero is a
dead end and gets hidden, but here 34 measures with no source is the finding, and it is a
list the agency asked for. Ticking one gives *"No source identified for this measure in the
current scan: <name>"* rather than the generic empty message. Health focus therefore also
stops hiding a domain with no sources, because hiding the domain would take its measures
with it.

Indicators are also in `matchesSearch()`, so typing a measure name into the search box
finds the sources that use it.

Two more facets, source type and region, are derived in `export_data.py` from the prose
columns rather than stored, so there is nothing extra to maintain. They are still checked:
a Source type whose wording matches no rule in `SOURCE_GROUP_RULES`, and a blank
Country/system, both stop the export. They used to fall back silently — anything unrecognised
became an academic report from overseas, which is the kind of wrong answer that looks exactly
like a right one.

### Adding a controlled column

Add the column to `data/sources.csv` and fill it, then add its heading to `FIELDS` in
`export_data.py`. A heading listed in `FIELDS` but missing from the CSV stops the export,
and a column in the CSV that is not in `FIELDS` is ignored, so nothing reaches the site by
accident.

## Before pushing

```
python build/check_publish.py
```

This repository is public and the wider project holds material that is not. The script
scans every file for classification markings, the partner agency's name, individual names,
suburb-level detail from unpublished slides, and document types (`.xlsx`, `.docx`, `.eml`,
`.pdf`) that should never be here. It also checks that every study id referenced in
`quadrants.json` exists, and that no page still points at a stale asset hash. It exits
non-zero if anything looks wrong.

Nothing on the site should be anything other than a citation of published work, a link to a
public source, or a summary written from scratch.

Then check the outbound links still resolve — the scan has already produced links that were
dead or pointed at the wrong record:

```
python build/check_links.py
```

A `403` usually means the publisher blocks automated requests rather than that the link is
broken; the script reports those separately from real `404`s.

## Files

```
index.html          overview, counts, what the badges mean
application.html    ?topic=<id> — the Actions/Measures by People/Places grid for one topic
browse.html         every source, filterable; filter state lives in the query string
study.html          ?id=app1-03 — one source in full
assets/
  style.css         the whole stylesheet
  common.js         data loading, badges, the topic registry, shared formatting
  browse.js         filtering
  quadrants.js      the grid
data/
  sources.csv       the source list: every page comes from this, edit it then export
  studies.json      generated
  meta.json         generated
  topics.json       hand-authored — the taxonomy: id, title, heading, question, blurb
  indicators.json   hand-authored — the partner agency's 61 named measures
  quadrants.json    hand-authored — keyed by topic id
build/
  export_data.py    data/sources.csv → JSON, then re-stamps the asset links
  stamp_assets.py   adds ?v=<hash> to each stylesheet and script link
  check_publish.py  pre-push safety scan
  check_links.py    checks the outbound links still resolve
  source_templates.py  writes the CSV template for suggesting sources into templates/
search/             a prototype, not part of the published pages — see below
Dockerfile          the Python image the search and the export run in
docker-compose.yml  the site, search and model together; see Running with Docker
.nojekyll           stops GitHub Pages hiding files that start with an underscore
```

Plain HTML, CSS and JavaScript, no build step, and the scripts use only Python's standard
library. GitHub Pages serves the files as they are.

### search/

A prototype of asking for sources in plain words rather than by ticking filters. A small
language model reads the question, picks tags the scan already uses, and those tags do the
searching; the reader sees which tags were chosen, why, and can remove any of them.

It is **not part of the published site** and nothing else here depends on it. The model
runs on a laptop through LM Studio, or in Docker beside its own model container, which
a static site cannot do, so the page is served beside the real site by
`search/serve.py`. Where this would eventually run is still open. `search/README.md` says how to run it and what it cannot do.

## Which filters start open

Focus area and **Kind of source** are drawn open. Geography, Health focus, **Access** and
Region start collapsed and are marked `collapsed: true` in `FACETS`. Access was one
of the open two until September 2026 and Kind of source took its place: whether a source
is a paper, a government report or a dataset is the cut most readers make first, and a
source's access shows on its row as a coloured badge whether or not that filter is open,
so collapsing it hides the checkboxes and none of the information.

A collapsed group opens by itself when something inside it is ticked, so a shared link
never lands on a shut panel with no sign of what is filtering the list.

## Order of the results

The dropdown in the result bar offers four orders. **Newest first** is the default.

| Order | Sorts on |
|---|---|
| Newest first | Publication year, most recent first |
| Oldest first | Publication year, earliest first |
| A to Z | The reference line, so author surname for a paper and agency name for a dataset |
| Z to A | The same, reversed |

The year is not a column in the CSV. `publication_year()` in `export_data.py` reads
it out of the reference, and only from the bracketed form every reference uses:
`Masters R, ... (2017). Return on investment ...`. A bare four-digit number elsewhere in
the string is usually part of a title or a survey name and would date the source wrongly,
so it is ignored.

**41 of the 61 sources have a year. The other 20 do not, and that is correct.** They are
the ongoing collections - the National Health Survey, PLIDA, the Social Health Atlas,
AusPlay - which have no single publication year. Under either year order they sort to the
bottom rather than being handed a date, so none of them is on page 1 of the opening view.
Under A to Z they sort with everything else.

The source list's own order used to be the default and is gone. It meant something to
whoever built the list and nothing to a reader. One consequence worth knowing: a
`?page=` link shared before this change now points at a different slice.

`tests/sorttest.mjs` covers all four orders across every page, the undated sources going
last, the default, and an unknown `?sort=` value falling back to it.

## Tags on a result row

Every checkbox group in the browse filter panel has a matching pill on each result, so a
reader can see why a row came back without opening it. `metaRow()` in `assets/common.js`
draws them, in this order: access, focus area, health focus, geography, region, kind of
source, indicators. Each has its own pill style, told apart by fill and border rather than
by colour alone — they sit beside the access badges, which do use colour to mean something.

Indicators come last because one source carries ten of them and they would otherwise push
the short, always-present tags off the first line. The busiest row has 17 pills.

`tests/facettags.mjs` states this rule as a test: add a facet later without a tag and it
fails there. A pill is not a button — the whole row sits inside a `<button>` on both the
browse list and the quadrant cards, and an `<a>` cannot nest in one. Tags are links only on
a source's own page, where nothing wraps them.

## Focus areas

The site groups sources by focus area rather than by "Application N" - that numbering is
internal project shorthand and means nothing to an outside reader. The taxonomy lives in
`data/topics.json`; adding one there is enough, no code change needed. A source can belong to
more than one: list them in its `Topics` cell, semicolon-separated, e.g.
`Local government measurement; Health inequities`. Every source names at least one.

**Removing one does not remove any source.** The export then stops with `topic '<name>' is not
in topics.json` for every source that carried it, until those cells are pointed somewhere
else. That is deliberate: it forces a decision rather than leaving sources silently untagged.

The word on screen is "focus area", asked for by the supervisors in September 2026. Everything
underneath still says topic - the `?topic=` parameter, the `topics` key in `meta.json`, the
`topics.json` filename, the `Topics` column in the CSV. Same split as `e4cc3d1` made for
the source field labels: display text moved, keys did not, so no link already sent out breaks.

`?topic=<id>` is the current parameter on `application.html` and `browse.html`. The older
`?app=1` style links already sent out still resolve to the same place, so nothing already
shared breaks.

## The structured view

`application.html` draws one focus area as an Actions/Measures by People/Places grid from
`data/quadrants.json`. Health system impact is the only one with a grid; the rest fall back
to the browse page.

**Each card is titled by the reference of every source it cites**, the same line the browse
page uses as a result's title. A card also carries `name` in the JSON, an editorial summary
of what the card argues, and that used to be the title. A summary sitting where a title sits
reads as though it were the name of a source, which is what the supervisors asked about in
September 2026, so `name` is no longer drawn. `note` still says what the card is about.

A source can title more than one card - `app1-05` and `app1-03` each appear on three - but
never twice in the same quadrant, so the People/Places and Actions/Measures heading tells
them apart. `tests/cardtags.mjs` checks all 14 titles against `studies.json`.

## Relevance marks

Relevance used to be marked by filling cells in the old workbook, yellow for relevant and
red for highly relevant, and the export read those fills into a `priority` field.

**They are gone.** Only 17 sources were ever marked, before the scan grew to 80, and a
source can sit in several focus areas, so a mark made against one no longer meant what it
looked like. The dot and the Relevance filter went in September 2026, and the field itself
went in November 2026 when the source list moved from the workbook to `data/sources.csv`,
which has no cell colours to read.

A `?priority=` link shared before this is not an error and does not filter; it is ignored
and the reader sees the whole list.

## Status

80 sources across six focus areas. One has the grid view; the other five are
on the browse page while their framing is settled — that is the existing behaviour for a
focus area without a `quadrants.json` entry, not a fault. Where a source's data has not been
checked yet, it is marked `Not yet assessed` rather than guessed at, and where a claim
could not be confirmed it is left out rather than softened into something that reads as fact.

Every page carries `<meta name="robots" content="noindex">`, so the site is reachable by
anyone with the link but will not turn up in a search. **Delete those four lines when the
draft is finished**, or the finished thing stays invisible too.

There is deliberately no `robots.txt` blocking crawlers: a blocked crawler never fetches the
page, so it never sees the `noindex` and can still list the bare URL. The meta tag on its own
is the stronger signal.
