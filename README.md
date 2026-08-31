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

### The two controlled columns

Filtering needs clean categories, which the workbook's prose columns cannot give. Two
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

Two more facets, source type and region, are derived in `export_data.py` from the prose
columns rather than stored, so there is nothing extra to maintain.

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
  quadrants.json    hand-authored — keyed by topic id
build/
  export_data.py    workbook → JSON, then re-stamps the asset links
  add_topics_column.py  one-off: adds the Topics column to the workbook
  stamp_assets.py   adds ?v=<hash> to each stylesheet and script link
  check_publish.py  pre-push safety scan
  check_links.py    checks the outbound links still resolve
.nojekyll           stops GitHub Pages hiding files that start with an underscore
```

Plain HTML, CSS and JavaScript, no build step and no dependencies beyond `openpyxl` for the
export script. GitHub Pages serves the files as they are.

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

Working draft. One topic has the grid view; the other two are on the browse page while their
framing is settled. Where a source's data has not been checked yet, it is marked
`Not yet assessed` rather than guessed at.

Every page carries `<meta name="robots" content="noindex">`, so the site is reachable by
anyone with the link but will not turn up in a search. **Delete those four lines when the
draft is finished**, or the finished thing stays invisible too.

There is deliberately no `robots.txt` blocking crawlers: a blocked crawler never fetches the
page, so it never sees the `noindex` and can still list the bare URL. The meta tag on its own
is the stronger signal.
