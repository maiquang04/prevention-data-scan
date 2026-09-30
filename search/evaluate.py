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
from config import DATA, KEYWORD_MJS, MODEL_MAIN, RESULTS, ROOT, TOP_K
from corpus import load_studies
from map_query import map_query
from rank import full_matches, rank
from vocab import load_meta, load_vocab

TESTSET = DATA / "testset.json"
HANDWRITTEN = DATA / "handwritten.json"


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


def run_llm(question, model, meta, vocab, studies, index):
    mapped = map_query(question, model, meta, vocab)
    everything = rank(question, mapped.tags, studies, meta, index, k=None)
    return {
        "top": [r.id for r in everything[:TOP_K]],
        "tags": mapped.chosen(),
        "reasoning": mapped.reasoning,
        "rejected": mapped.rejected,
        "seconds": round(mapped.seconds, 2),
        "error": mapped.error,
        "full_matches": full_matches(everything),
    }


def share(count, total):
    return round(count / total, 3) if total else 0.0


def summarise(method, testset_rows, handwritten_rows):
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
        })
    scored = [r for r in handwritten_rows if r["qid"] != "h8"]
    out["handwritten_hit@5"] = "%d/%d" % (sum(1 for r in scored if r["hit@5"]),
                                          len(scored))
    return out


def evaluate(args):
    with open(TESTSET, encoding="utf-8") as f:
        testset = json.load(f)
    if not testset.get("frozen") and not args.allow_unfrozen:
        print("%s is not frozen yet.\nReview a sample and set \"frozen\": true first "
              "(plan Task 12), so every method is scored on the same questions.\n"
              "Use --allow-unfrozen for a throwaway check." % TESTSET)
        return 1
    with open(HANDWRITTEN, encoding="utf-8") as f:
        handwritten = json.load(f)
    if not handwritten.get("confirmed"):
        print("Note: handwritten.json is not confirmed yet, so its right answers are "
              "still a draft.")

    items = testset["items"][:args.limit] if args.limit else testset["items"]
    hand = handwritten["items"]
    questions = ([{"qid": i["qid"], "question": i["question"]} for i in items] +
                 [{"qid": i["qid"], "question": i["question"]} for i in hand])

    meta, vocab = load_meta(), load_vocab(load_meta())
    studies = load_studies()
    index = build_index(studies)

    name = "%s-%s" % (date.today().isoformat(), args.method)
    if args.method == "llm":
        name += "-" + safe_name(args.model)
    RESULTS.mkdir(parents=True, exist_ok=True)
    final, partial = RESULTS / (name + ".json"), RESULTS / (name + ".partial.json")

    if args.method == "keyword":
        answers = run_keyword(questions)
    elif args.method == "bm25":
        answers = run_bm25(questions, index)
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
                                        studies, index)
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
        for key in ("tags", "reasoning", "rejected", "seconds", "error", "full_matches"):
            if key in got:
                row[key] = got[key]
        testset_rows.append(row)

    handwritten_rows = []
    for item in hand:
        got = answers.get(item["qid"], {"top": []})
        top = got["top"][:TOP_K]
        acceptable = set(item["acceptable"])
        row = {"qid": item["qid"], "question": item["question"], "top10": top,
               "hit@5": bool(acceptable & set(top[:5])),
               "acceptable": sorted(acceptable)}
        for key in ("tags", "reasoning", "rejected", "seconds", "full_matches"):
            if key in got:
                row[key] = got[key]
        handwritten_rows.append(row)

    payload = {
        "meta": {"method": args.method,
                 "model": args.model if args.method == "llm" else None,
                 "date": date.today().isoformat(),
                 "testset_items": len(items),
                 "testset_generator": testset.get("generator"),
                 "handwritten_confirmed": handwritten.get("confirmed", False),
                 "schema_enum_honoured": args.enum_honoured},
        "summary": summarise(args.method, testset_rows, handwritten_rows),
        "testset": testset_rows,
        "handwritten": handwritten_rows,
    }
    with open(final, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    partial.unlink(missing_ok=True)

    print("\n" + json.dumps(payload["summary"], indent=2))
    print("written to", final)
    return 0


def write_summary():
    runs = load_runs()
    if not runs:
        print("No finished runs in", RESULTS)
        return 1
    runs.sort(key=lambda r: -r["summary"]["R@5"])

    lines = ["# Search results", "",
             "One row per run. All scored on the same questions.", "",
             "| Method | Model | R@1 | R@5 | R@10 | MRR@10 | Empty | Sec | Hand-written hit@5 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        s, m = run["summary"], run["meta"]
        lines.append("| %s | %s | %.3f | %.3f | %.3f | %.3f | %.3f | %s | %s |"
                     % (m["method"], m["model"] or "-", s["R@1"], s["R@5"], s["R@10"],
                        s["MRR@10"], s["empty"],
                        ("%.1f" % s["sec"]) if "sec" in s else "-",
                        s["handwritten_hit@5"]))
    lines += ["",
              "R@5 means the right source was in the top five. MRR@10 rewards putting it",
              "higher up. Empty is the share of questions that returned nothing at all.",
              "Sec is the median time per question. The hand-written questions are the",
              "seven with a known good answer; the eighth is deliberately out of scope and",
              "is not scored.", ""]
    out = RESULTS / "summary.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("written to", out)
    return 0


def load_runs():
    runs = []
    for path in sorted(RESULTS.glob("*.json")):
        if path.name.startswith("_") or path.name.endswith(".partial.json"):
            continue
        with open(path, encoding="utf-8") as f:
            runs.append(json.load(f))
    return runs


def label(run):
    return run["meta"]["method"] + (
        " " + run["meta"]["model"].split("/")[-1] if run["meta"]["model"] else "")


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
    with open(TESTSET, encoding="utf-8") as f:
        testset = json.load(f)
    leak = {i["qid"]: i["leak"] for i in testset["items"]}

    bands = [("reuses its source's wording", lambda x: x >= 0.35),
             ("in between", lambda x: 0.20 <= x < 0.35),
             ("genuinely vague", lambda x: x < 0.20)]
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

    # h8 is the out-of-scope question and has no right answer, so it is not scored.
    hand, scored = [], 0
    for run in runs:
        rows = [r for r in run["handwritten"] if r["qid"] != "h8"]
        scored = max(scored, len(rows))
        hand.append("%d/%d" % (sum(1 for r in rows if r["hit@5"]), len(rows)))
    lines.append("| hand-written, written independently (%d) | %s |"
                 % (scored, " | ".join(hand)))
    lines += ["",
              "The hand-written questions were not made from any source's text, so they",
              "reuse nothing. They are the closest thing here to a real question.", ""]

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
    args = parser.parse_args()
    args.enum_honoured = {"true": True, "false": False}.get(args.enum_honoured)

    if args.summary:
        return write_summary()
    if args.compare:
        return write_comparison()
    if not args.method:
        parser.error("choose --method or --summary")
    return evaluate(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
