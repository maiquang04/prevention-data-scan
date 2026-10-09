"""Put the two tag mappers side by side on the development questions.

    python search/compare_mappers.py

The flat mapper (map_query.py) asks for all eight kinds of tag at once. The staged
one (map_staged.py) asks in three narrowing steps. This runs both over
data/dev_queries.json, shows what each chose and what each found, and writes
results/dev-check.md.

This is the judging-by-hand step: the development questions carry a note saying what
shape of answer is right, and nothing here is scored automatically. The questions in
data/testset.json are deliberately not used, so that prompts can be refined here
without quietly tuning against the numbers that get reported.
"""
import argparse
import json
import sys

from bm25 import build_index
from config import DATA, MODEL_MAIN, RESULTS
from corpus import load_studies, study_index
from map_query import map_query
from map_staged import map_staged
from rank import full_matches, rank
from vocab import LABELS, load_meta, load_vocab

QUERIES = DATA / "dev_queries.json"


def tags_text(tags):
    parts = ["%s: %s" % (LABELS[g], ", ".join(v)) for g, v in tags.items() if v]
    return "; ".join(parts) or "none"


def run_one(question, mapper, model, meta, vocab, studies, index, by_id):
    mapped = mapper(question, model, meta, vocab)
    if mapped.error:
        return {"error": mapped.error}
    everything = rank(question, mapped.tags, studies, meta, index, k=None)
    return {
        "tags": mapped.chosen(),
        "count": sum(len(v) for v in mapped.tags.values()),
        "full": full_matches(everything),
        "top": [(r.id, by_id[r.id]["reference"][:58]) for r in everything[:3]],
        "seconds": round(mapped.seconds, 1),
        "calls": mapped.calls(),
        "reasoning": mapped.reasoning,
        "rejected": mapped.rejected,
        "error": "",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL_MAIN)
    parser.add_argument("--only", help="one qid, for a quick look")
    args = parser.parse_args()

    with open(QUERIES, encoding="utf-8") as f:
        queries = json.load(f)["queries"]
    if args.only:
        queries = [q for q in queries if q["qid"] == args.only]

    meta = load_meta()
    vocab = load_vocab(meta)
    studies = load_studies()
    index = build_index(studies)
    by_id = study_index(studies)

    lines = ["# The two tag mappers, side by side", "",
             "Development questions, judged by hand. Nothing here is scored. Each question",
             "carries a note saying what shape of answer is right; read the two columns",
             "against it.", "",
             "Model: `%s`." % args.model, ""]
    totals = {"flat": [0, 0, 0], "staged": [0, 0, 0]}   # tags, full matches, calls

    for q in queries:
        print("%s %s" % (q["qid"], q["question"]), flush=True)
        lines += ["## %s. %s" % (q["qid"], q["question"]), "",
                  "*Right answer looks like:* %s" % q["expect"], "",
                  "| | Flat, one question | Staged, in steps |", "|---|---|---|"]
        got = {}
        for name, mapper in (("flat", map_query), ("staged", map_staged)):
            got[name] = run_one(q["question"], mapper, args.model, meta, vocab,
                                studies, index, by_id)
            r = got[name]
            if not r["error"]:
                totals[name][0] += r["count"]
                totals[name][1] += 1 if r["full"] else 0
                totals[name][2] += r["calls"]
            print("   %-6s %s" % (name, r["error"] or tags_text(r["tags"])), flush=True)

        def cell(key, fmt="%s"):
            out = []
            for name in ("flat", "staged"):
                r = got[name]
                out.append("error" if r["error"] else fmt % r[key])
            return out

        tag_cells = [got[n]["error"] or tags_text(got[n]["tags"]) for n in ("flat", "staged")]
        lines.append("| Tags chosen | %s | %s |" % tuple(tag_cells))
        lines.append("| How many | %s | %s |" % tuple(cell("count", "%d")))
        lines.append("| Sources carrying all of them | %s | %s |" % tuple(cell("full", "%d")))
        lines.append("| Time, calls | %s | %s |"
                     % tuple("%ss, %d" % (got[n].get("seconds", 0), got[n].get("calls", 0))
                             if not got[n]["error"] else "error" for n in ("flat", "staged")))
        for name, label in (("flat", "Flat"), ("staged", "Staged")):
            r = got[name]
            if r["error"]:
                lines += ["", "%s: **%s**" % (label, r["error"])]
                continue
            tops = "; ".join("`%s` %s" % (i, ref) for i, ref in r["top"]) or "nothing"
            lines += ["", "%s found: %s" % (label, tops)]
            if r["rejected"]:
                lines.append("")
                lines.append("%s invented: %s" % (label, ", ".join(r["rejected"])))
        lines.append("")

    n = len(queries)
    lines += ["## Across all %d questions" % n, "",
              "| | Flat | Staged |", "|---|---|---|",
              "| Tags per question | %.2f | %.2f |"
              % (totals["flat"][0] / n, totals["staged"][0] / n),
              "| Questions where some source carried every tag | %d of %d | %d of %d |"
              % (totals["flat"][1], n, totals["staged"][1], n),
              "| Calls to the model per question | %.1f | %.1f |"
              % (totals["flat"][2] / n, totals["staged"][2] / n), ""]

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "dev-check.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\nwritten to", out)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
