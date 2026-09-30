"""Ask a question in plain words, get the sources that answer it.

    python search/search.py "your question"

Prints the model's reasoning and the tags it chose before the results, so it is
clear what the search actually looked for. The link at the end opens the same
filters on the live site.
"""
import argparse
import json
import sys

from bm25 import build_index
from config import MODEL_MAIN, TOP_K
from corpus import browse_url, load_studies, study_index
from map_query import map_query
from rank import full_matches, rank
from vocab import GROUPS, LABELS, load_meta, load_vocab

REF_WIDTH = 78
LABEL_WIDTH = 17


def shorten(text, width=REF_WIDTH):
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 3].rstrip() + "..."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--model", default=MODEL_MAIN)
    parser.add_argument("--top", type=int, default=TOP_K)
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object instead of the report")
    args = parser.parse_args()

    meta = load_meta()
    vocab = load_vocab(meta)
    studies = load_studies()
    by_id = study_index(studies)
    index = build_index(studies)

    mapped = map_query(args.question, args.model, meta, vocab)
    if mapped.error:
        print(mapped.error, file=sys.stderr)
        return 1

    everything = rank(args.question, mapped.tags, studies, meta, index, k=None)
    full = full_matches(everything)
    results = everything[:args.top]
    chosen = mapped.chosen()
    # A link is only worth printing when the site would show the same thing: the
    # site filters strictly, so it shows the full matches and nothing else.
    url = browse_url(chosen, meta) if chosen and full else None

    if args.json:
        print(json.dumps({
            "question": args.question, "model": args.model,
            "seconds": round(mapped.seconds, 2), "reasoning": mapped.reasoning,
            "tags": chosen, "rejected": mapped.rejected, "full_matches": full,
            "url": url,
            "results": [{"id": r.id, "reference": by_id[r.id]["reference"],
                         "matched": r.matched, "asked": r.asked} for r in results],
        }, indent=2, ensure_ascii=False))
        return 0

    print("Question  " + args.question)
    print("Model     %s   (%.1f s)" % (args.model, mapped.seconds))

    if mapped.reasoning:
        print("\nWhy these tags")
        print("  " + mapped.reasoning)

    if chosen:
        print("\nTags chosen")
        for group in GROUPS:
            if chosen.get(group):
                print("  %s%s" % (LABELS[group].ljust(LABEL_WIDTH),
                                  ", ".join(chosen[group])))
    if mapped.rejected:
        print("\nIgnored (not in the tag lists): " + ", ".join(mapped.rejected))

    print()
    if not chosen:
        print("No tags chosen; ranked by the words in the question.")
    elif full:
        print("%d source%s carr%s every tag."
              % (full, "" if full == 1 else "s", "ies" if full == 1 else "y"))
    else:
        print("No source carries every tag; showing the closest matches.")

    print()
    for place, r in enumerate(results, 1):
        mark = "[%d/%d] " % (r.matched, r.asked) if r.asked else ""
        print("%2d. %s%s  %s" % (place, mark, r.id, shorten(by_id[r.id]["reference"])))
    if not results:
        print("Nothing matched.")

    if url:
        print("\nSee the sources with every tag on the site:")
        print(url)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
