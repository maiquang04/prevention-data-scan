# Prevention data scan

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

## Where the content comes from

The spreadsheet is the source of truth. Nothing in `data/studies.json` or `data/meta.json`
is edited by hand — regenerate them after changing the workbook:

```
python build/export_data.py
```

Close the workbook in Excel first, or openpyxl reads a stale copy.

That command also re-stamps the stylesheet and script links with a hash of each file
(`assets/common.js?v=cbf15586`). GitHub Pages caches assets for ten minutes and its CDN
caches them too, so without the stamp a visitor can hold the new copy of one file and the
old copy of another — which has already broken the live site once. If only the CSS or JS
changed and there is no reason to regenerate the data, run the stamping on its own:

```
python build/stamp_assets.py
```

```
lit-scan/lit-scan-draft.xlsx        source of truth, maintained in Excel
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
spreadsheet column.

### The three controlled columns

Filtering needs clean categories, which the workbook's prose columns cannot give. Three
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

`python build/add_column.py "Indicators" --width 44` adds an empty column to every sheet,
styled so the relevance highlighting stays unbroken across it — `priority_of()` reads those
cell fills back, so a white gap through a highlighted row is a real hazard, not a cosmetic
one. Fill the column, then add it to `FIELDS` in `export_data.py`; a heading listed in
`FIELDS` but missing from a sheet stops the export.

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
  studies.json      generated
  meta.json         generated
  topics.json       hand-authored — the taxonomy: id, title, heading, question, blurb
  indicators.json   hand-authored — the partner agency's 61 named measures
  quadrants.json    hand-authored — keyed by topic id
build/
  export_data.py    workbook → JSON, then re-stamps the asset links
  add_column.py     adds an empty controlled column to every sheet, styling and all
  add_topics_column.py  superseded by add_column.py; kept only as a record of the first migration
  stamp_assets.py   adds ?v=<hash> to each stylesheet and script link
  check_publish.py  pre-push safety scan
  check_links.py    checks the outbound links still resolve
.nojekyll           stops GitHub Pages hiding files that start with an underscore
```

Plain HTML, CSS and JavaScript, no build step and no dependencies beyond `openpyxl` for the
export script. GitHub Pages serves the files as they are.

## Which filters start open

Topic and **Kind of source** are drawn open. Geography, Health focus, **Access**, Region
and Relevance start collapsed and are marked `collapsed: true` in `FACETS`. Access was one
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

The year is not a column in the workbook. `publication_year()` in `export_data.py` reads
it out of the reference, and only from the bracketed form every reference uses:
`Masters R, ... (2017). Return on investment ...`. A bare four-digit number elsewhere in
the string is usually part of a title or a survey name and would date the source wrongly,
so it is ignored.

**41 of the 61 sources have a year. The other 20 do not, and that is correct.** They are
the ongoing collections - the National Health Survey, PLIDA, the Social Health Atlas,
AusPlay - which have no single publication year. Under either year order they sort to the
bottom rather than being handed a date, so none of them is on page 1 of the opening view.
Under A to Z they sort with everything else.

The workbook's own order used to be the default and is gone. It meant something to
whoever built the sheet and nothing to a reader. One consequence worth knowing: a
`?page=` link shared before this change now points at a different slice.

`tests/sorttest.mjs` covers all four orders across every page, the undated sources going
last, the default, and an unknown `?sort=` value falling back to it.

## Tags on a result row

Every checkbox group in the browse filter panel has a matching pill on each result, so a
reader can see why a row came back without opening it. `metaRow()` in `assets/common.js`
draws them, in this order: access, topic, health focus, geography, region, kind of source,
indicators. Each has its own pill style, told apart by fill and border rather than by colour
alone — they sit beside the access badges, which do use colour to mean something.

Relevance is the exception. It is the coloured dot beside the row number, not a pill.

Indicators come last because one source carries ten of them and they would otherwise push
the short, always-present tags off the first line. The busiest row has 17 pills.

`tests/facettags.mjs` states this rule as a test: add a facet later without a tag and it
fails there. A pill is not a button — the whole row sits inside a `<button>` on both the
browse list and the quadrant cards, and an `<a>` cannot nest in one. Tags are links only on
a source's own page, where nothing wraps them.

## Topics

The site groups sources by topic rather than by "Application N" - that numbering is internal
project shorthand and means nothing to an outside reader. The taxonomy lives in
`data/topics.json`; adding a topic there is enough, no code change needed. A source can belong
to more than one topic: add to its `Topics` cell in the workbook, semicolon-separated, e.g.
`Local government measurement; Health inequities`. A blank cell defaults to whichever topic
claims that sheet.

`?topic=<id>` is the current parameter on `application.html` and `browse.html`. The older
`?app=1` style links already sent out still resolve to the same place, so nothing already
shared breaks.

## Status

Working draft, 61 sources across six topics. One topic has the grid view; the other five are
on the browse page while their framing is settled — that is the existing behaviour for a topic
without a `quadrants.json` entry, not a fault. Where a source's data has not been checked yet,
it is marked `Not yet assessed` rather than guessed at, and where a claim could not be
confirmed it is left out rather than softened into something that reads as fact.

Every page carries `<meta name="robots" content="noindex">`, so the site is reachable by
anyone with the link but will not turn up in a search. **Delete those four lines when the
draft is finished**, or the finished thing stays invisible too.

There is deliberately no `robots.txt` blocking crawlers: a blocked crawler never fetches the
page, so it never sees the `noindex` and can still list the bare URL. The meta tag on its own
is the stronger signal.
