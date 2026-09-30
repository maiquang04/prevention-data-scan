# Search prototype

Someone who does not know the scan's tags should still be able to type a question in
plain words and get the right sources back. This is a first attempt at that: a small
language model reads the question and picks tags the collection already uses, then those
tags do the searching, and the user sees which tags were chosen and why.

It searches the 80 sources in `../data/studies.json`, and changes nothing on the
published site.

## How it works

```
question ──► map_query.py ──► checked tags + reasoning ──► rank.py ──► top 10 sources
              (LM Studio,       (every value checked         (score by tag groups matched,
               Qwen, JSON        against the vocabulary)      BM25 breaks ties)
               schema)
```

A source scores a point for each tag group it matches, weighted, rather than being
filtered out for missing one. So sources carrying every tag come first and near misses
follow: all the tags, or if not all, the best match. BM25, plain word matching, breaks
ties and orders the list on its own if the model picks no tags.

The model never chooses a tag that does not exist. The reply is constrained by a JSON
schema built from the site's own vocabulary, and anything that gets through is checked
again in Python.

## Before running anything

1. In LM Studio, load **qwen/qwen3-4b-2507** with context length 4096 or more.
2. Open **Developer** and click **Start Server**. It listens on port 1234.

Python 3.13 and Node are already installed. Nothing here needs `pip install`; it is all
standard library.

## The search page

From the repository root, the folder above this one:

```
python search/serve.py
```

This opens **http://127.0.0.1:8765/ask.html** in the browser. Leave the window open while
using it; Ctrl+C stops it.

The page looks like part of the website because it is served together with it: every
page, style and data file of the site is served as it is on disk, and the search page
comes from `search/ui/`. Nothing elsewhere in the repository is changed. The site's own
pages gain an "Ask a question" link in their menu as they are served, so you can move
between the two.

On the page:

- Type a question, or press one of the example questions.
- The model's reasoning appears word by word while it is being written, then the tags
  it chose.
- Each tag has a ×. Removing one ranks the list again at once, without asking the model
  again, and "Put back the model's tags" undoes it.
- Each source says which of the chosen tags it carries (✓) and which it does not (✗).
- When some sources carry every tag, a link opens exactly those on the browse page.
- If LM Studio is not running, the page says so and ranks by the words of the question.

The server listens on this computer only, so nothing outside it can reach the page.

### Checking the page before a demo

With the server running, in a second window:

```
node search/ui_check.mjs
```

This drives a headless Chrome or Edge through the demo questions, saves a screenshot of
each step in `results/screens/`, and reports two things separately: **problems with the
page**, which mean something is broken, and **notes**, which mean the model answered
differently from what the demo expects. The second kind happens, see Known limits.

## Running it in the terminal

```
python search/search.py "what public data is available for walkability in this region"
python search/search.py --model qwen/qwen3-1.7b "your question"
python search/search.py --json "your question"
```

The link printed at the end opens the same filters on the live site.

## Checking how well it works

```
python search/evaluate.py --method keyword     # today's search box
python search/evaluate.py --method bm25        # word matching, no model
python search/evaluate.py --method llm --model qwen/qwen3-4b-2507
python search/evaluate.py --method llm --model qwen/qwen3-1.7b
python search/evaluate.py --summary            # results/summary.md
```

Only one model fits in 4 GB at a time, so unload one in LM Studio before loading the
other. A full run takes a while; it saves as it goes and picks up where it stopped.

Tests, which need no model:

```
python -m unittest discover -s search -p "test_*.py" -v
```

## The questions it is scored on

`data/testset.json` holds one question per source, written by the model from that
source's abstract or summary and deliberately kept vague. The source it came from is the
right answer. Once reviewed the file is marked `"frozen": true`, so every method is
scored on the same questions.

`data/handwritten.json` holds eight example questions for the demonstration, each with a
set of acceptable answers, because a real question usually has more than one good source.
The last one is about something the scan does not cover, to show what a user sees when
there is no good answer.

## Files

| File | What it does |
|---|---|
| `config.py` | paths, server address, model ids, ranking weights |
| `llm.py` | talks to LM Studio |
| `vocab.py` | the 101 tags, and how they are described to the model |
| `corpus.py` | the sources, their text, their tags, links back to the site |
| `bm25.py` | word matching |
| `map_query.py` | question to tags |
| `rank.py` | tags to ranked sources |
| `search.py` | the same search in the terminal |
| `serve.py` | the search page's server |
| `ui/` | the search page: `ask.html`, `assets/ask.js`, `assets/ask.css` |
| `ui_check.mjs` | walks through the page in a real browser, with screenshots |
| `fetch_abstracts.py` | abstracts from Europe PMC, for building test questions |
| `make_testset.py` | writes the test questions |
| `keyword_baseline.mjs` | runs the site's current search, for comparison |
| `evaluate.py` | scores everything |
| `test_prototype.py` | tests for the search |
| `test_server.py` | tests for the page's server, with the model replaced by a stand-in |

## Known limits

- It runs on a laptop, not on the published site. A static site cannot run a model or
  keep an API key secret, so where this would eventually run is still an open question.
- The site puts health focus and measures in one filter and matches either of them, while
  the ranking here treats them as two separate things the question asked for. When both
  are chosen, the link drops a focus that a chosen measure already implies, so the page
  shows the same sources. A focus unrelated to any chosen measure is kept, and then the
  page can show more than the ranking counted.
- Each test question counts only the source it came from as correct, so a genuinely good
  answer from another source is scored as a miss.
- The model does not always give the same answer to the same question, even at
  temperature 0. Asked the walkability example question six times in a row on
  29 September 2026, it chose `Small area` once and `LGA` five times, always adding
  `Queensland`. The first answer after the server restarts is the one most likely to
  differ.
- It reads "this region" as a place although the prompt tells it not to. On that same
  question no source carries every tag until `Queensland` and the small-area or council
  tag are removed; then the three public walkability sources do.
