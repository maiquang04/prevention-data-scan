"""Score each way of searching on the same questions.

Three methods:
  keyword  the site's search box as it works today, through its own browse.js
  bm25     word matching alone, no model
  llm      the prototype: map the question to tags, then rank by them

    python search/evaluate.py --method bm25
    python search/evaluate.py --method llm --model qwen/qwen3-1.7b
    python search/evaluate.py --summary
"""
import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from datetime import date

from bm25 import build_index
from config import (DATA, KEYWORD_MJS, MAPPER, MODEL_MAIN, RANKER, RESULTS, ROOT,
                    TOP_K)
from corpus import load_studies
from map_query import map_query
from map_staged import map_staged
from rank import full_matches, rank
from vocab import load_meta, load_vocab

TESTSET = DATA / "testset.json"
EXAMPLES = DATA / "examples.json"
# The question that nothing in the scan covers. Choosing no tags for it is right,
# and what each model does with it says more than any score.
OUT_OF_SCOPE = "e7"


def safe_name(text):
    return "".join(c if c.isalnum() or c in "-." else "-" for c in text).strip("-")


def questions_stamp(questions):
    """A short fingerprint of the exact questions being asked."""
    text = json.dumps([[q["qid"], q["question"]] for q in questions],
                      ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_keyword(questions):
    """The site's own search, run by node over the real browse.js."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    in_path, out_path = RESULTS / "_keyword_in.json", RESULTS / "_keyword_out.json"
    with open(in_path, "w", encoding="utf-8") as f:
        json.dump([{"qid": q["qid"], "question": q["question"]} for q in questions], f)
    subprocess.run(["node", str(KEYWORD_MJS), str(in_path), str(out_path)],
                   check=True, cwd=str(ROOT))
    with open(out_path, encoding="utf-8") as f:
        rows = json.load(f)
    in_path.unlink(missing_ok=True)
    out_path.unlink(missing_ok=True)
    return {r["qid"]: {"top": r["ids"]} for r in rows}


def run_bm25(questions, index):
    return {q["qid"]: {"top": [d for d, _ in index.rank(q["question"], TOP_K)]}
            for q in questions}


MAPPERS = {"flat": map_query, "staged": map_staged}


def run_llm(question, model, meta, vocab, studies, index, mapper="flat",
            ranker="tags-first"):
    mapped = MAPPERS[mapper](question, model, meta, vocab)
    everything = rank(question, mapped.tags, studies, meta, index, k=None,
                      strategy=ranker)
    return {
        "top": [r.id for r in everything[:TOP_K]],
        "tags": mapped.chosen(),
        "reasoning": mapped.reasoning,
        "rejected": mapped.rejected,
        "seconds": round(mapped.seconds, 2),
        "calls": mapped.calls(),
        "error": mapped.error,
        "full_matches": full_matches(everything),
    }


def rerank_saved(path, ranker, questions, studies, meta, index):
    """Rank again with a different ranker, reusing tags already paid for.

    The two rankers are only comparable on identical tags, and the model gives
    slightly different tags each run even at temperature 0. Re-ranking a saved run
    removes that variation, and takes seconds instead of twenty minutes.
    """
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    by_qid = {r["qid"]: r for r in saved["testset"] + saved.get("examples", [])}
    text = {q["qid"]: q["question"] for q in questions}

    answers = {}
    for qid, row in by_qid.items():
        if qid not in text:
            continue
        tags = {g: list(v) for g, v in (row.get("tags") or {}).items()}
        everything = rank(text[qid], tags, studies, meta, index, k=None,
                          strategy=ranker)
        answers[qid] = dict(row, top=[r.id for r in everything[:TOP_K]],
                            full_matches=full_matches(everything))
    return saved["meta"], answers


def share(count, total):
    return round(count / total, 3) if total else 0.0


def summarise(method, testset_rows):
    n = len(testset_rows)
    ranks = [r["rank_of_target"] for r in testset_rows]
    out = {
        "R@1": share(sum(1 for r in ranks if r == 1), n),
        "R@5": share(sum(1 for r in ranks if r and r <= 5), n),
        "R@10": share(sum(1 for r in ranks if r and r <= 10), n),
        "MRR@10": round(sum(1 / r for r in ranks if r) / n, 3) if n else 0.0,
        "empty": share(sum(1 for r in testset_rows if not r["top10"]), n),
    }
    if method == "llm":
        seconds = [r.get("seconds", 0) for r in testset_rows]
        tag_counts = [sum(len(v) for v in r.get("tags", {}).values())
                      for r in testset_rows]
        out.update({
            "sec": round(statistics.median(seconds), 2) if seconds else 0.0,
            "tags": round(sum(tag_counts) / n, 2) if n else 0.0,
            "no_tags": share(sum(1 for c in tag_counts if c == 0), n),
            "rejected": sum(len(r.get("rejected", [])) for r in testset_rows),
            "errors": sum(1 for r in testset_rows if r.get("error")),
            "too_narrow": share(sum(1 for r in testset_rows
                                    if r.get("tags") and not r.get("full_matches")), n),
            "calls": round(sum(r.get("calls", 1) for r in testset_rows) / n, 2) if n else 0.0,
        })
    return out


def evaluate(args):
    with open(TESTSET, encoding="utf-8") as f:
        testset = json.load(f)
    if not testset.get("frozen") and not args.allow_unfrozen:
        print("%s is not frozen yet.\nReview a sample and set \"frozen\": true first "
              "(plan Task 12), so every method is scored on the same questions.\n"
              "Use --allow-unfrozen for a throwaway check." % TESTSET)
        return 1
    # The example questions are run but not scored: they have no recorded right
    # answer. What they are for is the reasoning and tags each model produces,
    # which is what --models compares and what stands in for a live demonstration
    # if the model will not start.
    with open(EXAMPLES, encoding="utf-8") as f:
        examples = json.load(f)["examples"]

    items = testset["items"][:args.limit] if args.limit else testset["items"]
    questions = ([{"qid": i["qid"], "question": i["question"]} for i in items] +
                 [{"qid": i["qid"], "question": i["question"]} for i in examples])

    meta, vocab = load_meta(), load_vocab(load_meta())
    studies = load_studies()
    index = build_index(studies)

    name = "%s-%s" % (date.today().isoformat(), args.method)
    if args.method == "llm":
        # The mapper and ranker go in the name, or one variant would overwrite another.
        name += "-%s-%s-%s" % (safe_name(args.model), args.mapper, args.ranker)
    RESULTS.mkdir(parents=True, exist_ok=True)
    final, partial = RESULTS / (name + ".json"), RESULTS / (name + ".partial.json")

    if args.method == "keyword":
        answers = run_keyword(questions)
    elif args.method == "bm25":
        answers = run_bm25(questions, index)
    elif args.reuse_tags:
        source_meta, answers = rerank_saved(args.reuse_tags, args.ranker, questions,
                                            studies, meta, index)
        print("reused the tags from %s (%s, %s), ranked again with %s"
              % (args.reuse_tags, source_meta.get("mapper", "?"),
                 source_meta.get("ranker", "?"), args.ranker))
    else:
        # One model call per question, so save as we go and pick up where we left off.
        answers = {}
        # A saved part-finished run is only safe to continue if the questions have
        # not changed since. They do change: the test set gets reviewed and edited
        # before it is frozen, and a qid can end up on different words. Resuming
        # across that would score old answers against new questions without saying so.
        stamp = questions_stamp(questions)
        if partial.exists() and not args.overwrite:
            with open(partial, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("questions") == stamp:
                answers = saved["answers"]
                print("resuming: %d of %d already done" % (len(answers), len(questions)))
            else:
                print("the questions have changed since that part-finished run, "
                      "so it is being started again")
        for q in questions:
            if q["qid"] in answers:
                continue
            answers[q["qid"]] = run_llm(q["question"], args.model, meta, vocab,
                                        studies, index, args.mapper, args.ranker)
            with open(partial, "w", encoding="utf-8") as f:
                json.dump({"questions": stamp, "answers": answers}, f, indent=2,
                          ensure_ascii=False)
            got = answers[q["qid"]]
            print("%-5s %4.1fs  %s" % (q["qid"], got["seconds"],
                                       got["error"] or ", ".join(got["top"][:3])))

    testset_rows = []
    for item in items:
        got = answers.get(item["qid"], {"top": []})
        top = got["top"][:TOP_K]
        place = top.index(item["target"]) + 1 if item["target"] in top else None
        row = {"qid": item["qid"], "target": item["target"], "top10": top,
               "rank_of_target": place}
        for key in ("tags", "reasoning", "rejected", "seconds", "calls", "error",
                    "full_matches"):
            if key in got:
                row[key] = got[key]
        testset_rows.append(row)

    example_rows = []
    for item in examples:
        got = answers.get(item["qid"], {"top": []})
        row = {"qid": item["qid"], "question": item["question"],
               "top10": got["top"][:TOP_K]}
        for key in ("tags", "reasoning", "rejected", "seconds", "calls", "full_matches"):
            if key in got:
                row[key] = got[key]
        example_rows.append(row)

    payload = {
        "meta": {"method": args.method,
                 "model": args.model if args.method == "llm" else None,
                 "mapper": args.mapper if args.method == "llm" else None,
                 "ranker": args.ranker if args.method == "llm" else None,
                 "date": date.today().isoformat(),
                 "testset_items": len(items),
                 "testset_generator": testset.get("generator"),
                 "testset_frozen_on": testset.get("frozen_on"),
                 "schema_enum_honoured": args.enum_honoured},
        "summary": summarise(args.method, testset_rows),
        "testset": testset_rows,
        "examples": example_rows,
    }
    with open(final, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    partial.unlink(missing_ok=True)

    print("\n" + json.dumps(payload["summary"], indent=2))
    print("written to", final)
    return 0


def stale_runs(runs):
    """Runs made before the questions were last reviewed, so not comparable.

    Freezing the test set stamps the day it happened. A run from before that was
    scored on questions that have since been edited, and putting it in the same
    table as a later run would compare two different things.
    """
    try:
        with open(TESTSET, encoding="utf-8") as f:
            frozen_on = json.load(f).get("frozen_on")
    except (OSError, ValueError):
        return []
    if not frozen_on:
        return []
    return [r for r in runs if r["meta"]["date"] < frozen_on]


def warn_about_dates(runs, lines):
    """Add a line to the file, and print one, if the rows are not all comparable."""
    stale = stale_runs(runs)
    dates = sorted({r["meta"]["date"] for r in runs})
    notes = []
    if stale:
        notes.append("**These rows are not comparable.** " +
                     ", ".join(label(r) for r in stale) +
                     " ran before the questions were last reviewed. Run those again.")
    elif len(dates) > 1:
        notes.append("Rows were run on different days (%s), on the same frozen questions."
                     % ", ".join(dates))
    for note in notes:
        print("\n" + note)
        lines += ["", note]
    return bool(stale)


def write_summary():
    runs = load_runs()
    if not runs:
        print("No finished runs in", RESULTS)
        return 1
    runs.sort(key=lambda r: -r["summary"]["R@5"])

    lines = ["# Search results", "",
             "One row per method, the most recent run of each.", "",
             "| Method | Model | R@1 | R@5 | R@10 | MRR@10 | Empty | Sec | Run |",
             "|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        s, m = run["summary"], run["meta"]
        lines.append("| %s | %s | %.3f | %.3f | %.3f | %.3f | %.3f | %s | %s |"
                     % (m["method"], m.get("model") or "-", s["R@1"], s["R@5"], s["R@10"],
                        s["MRR@10"], s["empty"],
                        ("%.1f" % s["sec"]) if "sec" in s else "-", m["date"]))
    lines += ["",
              "R@5 means the right source was in the top five. MRR@10 rewards putting it",
              "higher up. Empty is the share of questions that returned nothing at all.",
              "Sec is the median time per question.", "",
              "Read these with the split in vague-questions.md. Every question here was",
              "written from its own source's text, which word matching also searches, so",
              "the overall figures flatter word matching."]
    warn_about_dates(runs, lines)
    lines.append("")
    out = RESULTS / "summary.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("written to", out)
    return 0


def load_runs(all_dates=False):
    """The finished runs, newest per method and model only.

    A result file is named by its date, so running a method again on another day
    leaves the earlier file in place. Keeping both would list that method twice in
    the tables, and the two rows would be scored on different questions whenever
    the test set was reviewed in between. Only the newest of each is kept, unless
    all_dates is asked for.
    """
    runs = []
    for path in sorted(RESULTS.glob("*.json")):
        if path.name.startswith("_") or path.name.endswith(".partial.json"):
            continue
        with open(path, encoding="utf-8") as f:
            try:
                run = json.load(f)
            except ValueError:
                print("skipping %s: not readable as JSON" % path.name)
                continue
        # Anything else that happens to be sitting in the folder is not a run.
        if not isinstance(run, dict) or "meta" not in run or "summary" not in run:
            print("skipping %s: not a results file" % path.name)
            continue
        runs.append(run)
    if all_dates:
        return runs

    newest, dropped = {}, []
    for run in runs:
        # Keyed by the whole variant, not just the method: a flat and a staged run of
        # the same model are different things and must not replace each other.
        key = (run["meta"]["method"], run["meta"].get("model"),
               run["meta"].get("mapper"), run["meta"].get("ranker"))
        older = newest.get(key)
        # Same method run twice on one day: the later file overwrote the earlier,
        # so ties keep whichever sorted last, which is the one still on disk.
        if older is None or run["meta"]["date"] >= older["meta"]["date"]:
            if older is not None:
                dropped.append(older)
            newest[key] = run
        else:
            dropped.append(run)
    for run in dropped:
        print("ignoring the older %s run of %s" % (run["meta"]["date"], label(run)))
    return list(newest.values())


def label(run):
    m = run["meta"]
    if not m.get("model"):
        return m["method"]
    bits = [m["model"].split("/")[-1]]
    if m.get("mapper"):
        bits.append(m["mapper"])
    if m.get("ranker"):
        bits.append(m["ranker"])
    return " ".join(bits)


def leak_bands():
    """The three reuse bands, and each question's reuse score."""
    with open(TESTSET, encoding="utf-8") as f:
        leak = {i["qid"]: i["leak"] for i in json.load(f)["items"]}
    bands = [("reuses its source's wording", lambda x: x >= 0.35),
             ("in between", lambda x: 0.20 <= x < 0.35),
             ("genuinely vague", lambda x: x < 0.20)]
    return leak, bands


def hit_at(run, k=5, among=None):
    """Share of test questions whose right answer landed in the top k."""
    rows = [r for r in run["testset"] if among is None or r["qid"] in among]
    if not rows:
        return None
    hit = sum(1 for r in rows if r["rank_of_target"] and r["rank_of_target"] <= k)
    return hit / len(rows)


def write_models():
    """The two models side by side, on everything that would decide between them.

    --summary gives one row per method, which answers "does the pipeline work".
    This answers a different question: which model to run it with. So it puts the
    models in columns and the measures in rows, and adds what the headline scores
    leave out, such as how many tags each one picks and what each does with a
    question the collection cannot answer.
    """
    runs = [r for r in load_runs() if r["meta"]["method"] == "llm"]
    if len(runs) < 2:
        print("Need a finished run for at least two models; found %d." % len(runs))
        return 1
    # Biggest model first, by its score on the questions that matter.
    leak, bands = leak_bands()
    vague = {q for q, v in leak.items() if v < 0.20}
    runs.sort(key=lambda r: -(hit_at(r, 5, vague) or 0))
    names = [r["meta"]["model"] for r in runs]
    cols = " | ".join(names)
    rule = "|---" * (len(runs) + 1) + "|"

    def row(name, values, fmt="%s"):
        return "| %s | %s |" % (name, " | ".join(
            "-" if v is None else fmt % v for v in values))

    def s(key, fmt="%.3f"):
        return [r["summary"].get(key) for r in runs]

    lines = ["# Which model to run the search with", "",
             "Both models were given the same questions and the same tag lists. The",
             "pipeline around them is identical, so every difference below is the model.", "",
             "## Finding the right source", "",
             "| | " + cols + " |", rule,
             row("Right answer first (R@1)", s("R@1"), "%.3f"),
             row("In the top five (R@5)", s("R@5"), "%.3f"),
             row("In the top ten (R@10)", s("R@10"), "%.3f"),
             row("MRR@10, rewards ranking it higher", s("MRR@10"), "%.3f"),
             ""]

    sizes = {n: sum(1 for v in leak.values() if t(v)) for n, t in bands}
    lines += ["### By how vague the question is", "",
              "The questions were written from each source's own text, so the ones that",
              "reuse that wording are easier than a question a person would really ask.",
              "The bottom row is the one that matters.", "",
              "| Test questions | " + cols + " |", rule]
    for name, test in bands:
        among = {q for q, v in leak.items() if test(v)}
        lines.append(row("%s (%d)" % (name, sizes[name]),
                         [hit_at(r, 5, among) for r in runs], "%.3f"))
    lines.append("")

    lines += ["## How it behaves", "",
              "| | " + cols + " |", rule,
              row("Tags chosen per question, mean", s("tags"), "%.2f"),
              row("Chose tags no single source carried", s("too_narrow"), "%.3f"),
              row("Chose no tags at all", s("no_tags"), "%.3f"),
              row("Invented a tag that does not exist", s("rejected"), "%d"),
              row("Failed to answer", s("errors"), "%d"),
              "",
              "Every extra tag makes a full match less likely, so picking more of them is",
              "not better. A model that invents no tag at all is the JSON schema working:",
              "it is held to the lists while it writes.", ""]

    seconds = [[q.get("seconds", 0) for q in r["testset"] if q.get("seconds")]
               for r in runs]
    lines += ["## Speed", "",
              "On one laptop with 4 GB of video memory. These times only mean something",
              "if just one model was loaded in LM Studio during each run: two at once is",
              "more than the memory holds, and the overflow runs on the processor instead,",
              "which can make the smaller model look slower than the larger one.", "",
              "| | " + cols + " |", rule,
              row("Median seconds per question", s("sec"), "%.1f"),
              row("Fastest", [min(x) if x else None for x in seconds], "%.1f"),
              row("Slowest", [max(x) if x else None for x in seconds], "%.1f"),
              ""]

    out_of_scope, asked = [], None
    for run in runs:
        found = [e for e in run.get("examples", []) if e["qid"] == OUT_OF_SCOPE]
        if not found:
            out_of_scope.append(None)
            continue
        asked = found[0].get("question") or asked
        picked = sum(len(v) for v in (found[0].get("tags") or {}).values())
        out_of_scope.append("chose nothing, correctly" if not picked
                            else "chose %d tag%s anyway" % (picked, "" if picked == 1 else "s"))
    if any(out_of_scope):
        lines += ["## A question the collection cannot answer", "",
                  "One example question asks about something nothing in the scan covers"
                  + (" (%s)" % asked.rstrip("?") if asked else "") + ".",
                  "Choosing no tags is the right answer, and the page then says the",
                  "results may not be relevant. A model that picks tags anyway is",
                  "inventing a connection that is not there.", "",
                  "| | " + cols + " |", rule,
                  row("Out-of-scope question", out_of_scope), ""]

    warn_about_dates(runs, lines)
    lines.append("")
    out = RESULTS / "models.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("written to", out)
    return 0


def write_variants():
    """Every way of building the search, side by side on the frozen questions.

    One row per run: the two baselines, and each pairing of a mapper with a ranker.
    The vague column is the one that decides anything, for the reason set out in
    vague-questions.md.
    """
    runs = load_runs()
    if not runs:
        print("No finished runs in", RESULTS)
        return 1
    leak, _ = leak_bands()
    vague = {q for q, v in leak.items() if v < 0.20}
    middling = {q for q, v in leak.items() if 0.20 <= v < 0.35}
    reusing = {q for q, v in leak.items() if v >= 0.35}
    runs.sort(key=lambda r: -(hit_at(r, 5, vague) or 0))

    lines = ["# Every version of the search, side by side", "",
             "All on the same frozen questions. **Read the vague column**: those are the",
             "questions that share least wording with their source, so they are the",
             "closest this test gets to one a person would really ask. The others can be",
             "answered by word matching almost for free.", "",
             "Mapper: how a question becomes tags, all at once or in three narrowing",
             "steps. Ranker: whether the tags score every source, or word matching",
             "shortlists first and the tags only reorder it.", "",
             "| Version | R@1 | R@5 | R@10 | R@5 reusing (%d) | R@5 middling (%d) | "
             "**R@5 vague (%d)** | Tags | No full match | Calls | Sec |"
             % (len(reusing), len(middling), len(vague)),
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        s = run["summary"]

        def band(among):
            got = hit_at(run, 5, among)
            return "-" if got is None else "%.3f" % got

        def num(key, fmt="%.2f"):
            return fmt % s[key] if key in s else "-"

        lines.append("| %s | %.3f | %.3f | %.3f | %s | %s | **%s** | %s | %s | %s | %s |"
                     % (label(run), s["R@1"], s["R@5"], s["R@10"],
                        band(reusing), band(middling), band(vague),
                        num("tags"), num("too_narrow", "%.3f"), num("calls", "%.1f"),
                        num("sec", "%.1f")))
    lines += ["",
              "Tags is how many the mapper chose per question, and No full match how often",
              "no single source carried all of them. Fewer tags and fewer empty full",
              "matches is better: an extra tag narrows the search without adding meaning.",
              "Calls is how many times the model was asked per question."]
    warn_about_dates(runs, lines)
    lines.append("")

    out = RESULTS / "variants.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("written to", out)
    return 0


def write_comparison():
    """Split the test questions by how much they reuse their source's wording.

    This matters more than the overall score. Each question was written from its
    own source's abstract or summary, and BM25 searches that same text, so a
    question that reuses its wording is one BM25 can match almost for free. Those
    questions are not the vague ones the search is meant to handle, and scoring
    them together hides which method is better at the job.
    """
    runs = load_runs()
    if not runs:
        print("No finished runs in", RESULTS)
        return 1
    leak, bands = leak_bands()
    sizes = {name: sum(1 for v in leak.values() if test(v)) for name, test in bands}

    lines = ["# Which method handles a vague question", "",
             "Every test question was written from one source's own abstract or summary,",
             "and word matching searches that same text. So a question that reuses its",
             "source's wording is one word matching can answer almost for free, and those",
             "are not the questions this search is for. Splitting the questions by how much",
             "wording they reuse separates the two cases.", "",
             "Share of questions with the right source in the top five.", "",
             "| Test questions | " + " | ".join(label(r) for r in runs) + " |",
             "|---|" + "---|" * len(runs)]
    for name, test in bands:
        cells = []
        for run in runs:
            rows = [r for r in run["testset"] if test(leak.get(r["qid"], 0))]
            hit = sum(1 for r in rows
                      if r["rank_of_target"] and r["rank_of_target"] <= 5)
            cells.append("%.3f" % (hit / len(rows)) if rows else "-")
        lines.append("| %s (%d) | %s |" % (name, sizes[name], " | ".join(cells)))

    lines += ["",
              "The bottom row is the one to read. A question sharing little wording with",
              "its source is the closest this test gets to one a person would really ask,",
              "and it is where word matching has least to work with."]
    warn_about_dates(runs, lines)
    lines.append("")

    out = RESULTS / "vague-questions.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("written to", out)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=["keyword", "bm25", "llm"])
    parser.add_argument("--model", default=MODEL_MAIN)
    parser.add_argument("--limit", type=int, help="only the first N test questions")
    parser.add_argument("--allow-unfrozen", action="store_true")
    parser.add_argument("--overwrite", action="store_true",
                        help="ignore a saved part-finished run")
    parser.add_argument("--enum-honoured", dest="enum_honoured",
                        choices=["true", "false"],
                        help="whether the schema held the model to the tag lists")
    parser.add_argument("--summary", action="store_true",
                        help="combine finished runs into results/summary.md")
    parser.add_argument("--compare", action="store_true",
                        help="split the questions by how much wording they reuse")
    parser.add_argument("--models", action="store_true",
                        help="the models side by side, into results/models.md")
    parser.add_argument("--mapper", choices=list(MAPPERS), default=MAPPER,
                        help="how a question becomes tags: all at once, or in steps")
    parser.add_argument("--ranker", choices=["tags-first", "bm25-first"], default=RANKER,
                        help="whether tags or word matching narrows the field first")
    parser.add_argument("--reuse-tags", dest="reuse_tags", metavar="RESULTS.json",
                        help="rank a finished run's tags again with another ranker, "
                             "without asking the model")
    parser.add_argument("--variants", action="store_true",
                        help="every mapper and ranker pair, into results/variants.md")
    args = parser.parse_args()
    args.enum_honoured = {"true": True, "false": False}.get(args.enum_honoured)

    if args.summary:
        return write_summary()
    if args.compare:
        return write_comparison()
    if args.models:
        return write_models()
    if args.variants:
        return write_variants()
    if args.reuse_tags:
        args.method = "llm"
    if not args.method:
        parser.error("choose --method or --summary")
    return evaluate(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
