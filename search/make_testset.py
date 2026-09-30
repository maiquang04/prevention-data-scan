"""Build the test questions, one per source.

The method: take what each source says about itself, have the model rewrite it as the
vague question someone would actually type, and remember which source it came from.
That source is the right answer, so the search can be scored without anyone writing
80 questions by hand.

    python search/make_testset.py            # build or resume
    python search/make_testset.py --sample 15 # read some, change nothing
"""
import argparse
import json
import random
import re
import sys
from datetime import date

import llm
from bm25 import tokenize
from config import DATA, MODEL_MAIN
from corpus import load_studies, study_index

OUT = DATA / "testset.json"
ABSTRACTS = DATA / "abstracts.json"

PROMPT = """Below is a description of one source in a collection of public-health research and data.

Write ONE question that a health policy officer might type into a search box, where this
source would be a useful answer.

Rules:
- Describe the practical need or situation in plain everyday words.
- One or two sentences, under 35 words.
- Do not name the source, its authors or organisation, any place, any dataset or survey
  name, any year, or any statistic.
- Do not copy distinctive phrases from the description. Use your own words.
- Mentioning the general subject (for example physical activity, food or hospital use)
  is fine.

An example of the style wanted:
"I want to find out whether local sport programs get more adults exercising. What data
could help?"

Description:
{description}

Reply with the question only."""

# Words too common to say anything about whether a question copied its source.
STOPWORDS = set("""the and for with that this from what how are can data could would
should about into which their there where when want need find use using""".split())


def content(text):
    """The words that carry meaning, for comparing a question with its source."""
    return {w for w in tokenize(text) if len(w) >= 3 and w not in STOPWORDS}


def leak_score(question, description):
    """How much of the question was lifted from the description, 0 to 1.

    The site's own search reads these descriptions, so a question that reuses their
    words would make plain word matching look better than it is, and would not be
    the vague question this is meant to test.
    """
    words = content(question)
    return len(words & content(description)) / max(len(words), 1)


def describe(study, abstracts):
    """What the generator is shown, and where it came from."""
    entry = abstracts.get(study["id"]) or {}
    if entry.get("abstract"):
        return entry["abstract"], "abstract"
    parts = [study.get(f) for f in ("task", "output", "metrics")]
    return ". ".join(p for p in parts if p), "summary"


def clean_question(text):
    text = " ".join((text or "").split())
    text = re.sub(r"^(question|answer)\s*:\s*", "", text, flags=re.I)
    return text.strip().strip('"').strip("'").strip()


def generate(description, model, attempts=3):
    """Ask for a question, retrying while it reads too much like its source."""
    best = None
    for attempt in range(1, attempts + 1):
        raw = llm.chat([{"role": "user",
                         "content": PROMPT.replace("{description}", description)}],
                       model=model, temperature=0.7, max_tokens=120)
        question = clean_question(raw)
        if not question:
            continue
        leak = leak_score(question, description)
        # A year or a count is exactly the specific detail these questions must not have.
        specific = bool(re.search(r"\b\d{4}\b", question))
        if best is None or leak < best[1]:
            best = (question, leak, attempt)
        if leak <= 0.5 and not specific:
            return question, leak, attempt
    if best is None:
        return "", 1.0, attempts
    return best


def load_existing():
    if not OUT.exists():
        return None
    with open(OUT, encoding="utf-8") as f:
        return json.load(f)


def save(payload):
    DATA.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def show_sample(count, seed):
    payload = load_existing()
    if not payload:
        print("No test set yet. Run without --sample first.")
        return 1
    by_id = study_index(load_studies())
    items = payload["items"]
    random.Random(seed).shuffle(items := list(items))
    for item in items[:count]:
        study = by_id.get(item["target"], {})
        print("%s  ->  %s  (%s, leak %.2f)"
              % (item["qid"], item["target"], item["basis"], item["leak"]))
        print("   Q: " + item["question"])
        print("   A: " + (study.get("reference") or "?")[:110])
        print()
    print("%d items in total; %s"
          % (len(payload["items"]),
             "frozen" if payload.get("frozen") else "not frozen yet"))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL_MAIN)
    parser.add_argument("--sample", type=int, metavar="N",
                        help="print N existing questions and stop")
    parser.add_argument("--seed", type=int, default=7, help="which sample to print")
    parser.add_argument("--overwrite", action="store_true",
                        help="start again instead of resuming")
    parser.add_argument("--limit", type=int, help="only do the first N sources")
    args = parser.parse_args()

    if args.sample:
        return show_sample(args.sample, args.seed)

    payload = None if args.overwrite else load_existing()
    if payload and payload.get("frozen"):
        print("%s is frozen, so it will not be changed.\n"
              "Freezing is what keeps every method scored on the same questions.\n"
              "Use --overwrite only if you really mean to throw it away." % OUT)
        return 1
    if payload is None:
        payload = {"frozen": False, "generator": args.model,
                   "generated": date.today().isoformat(), "items": []}

    abstracts = {}
    if ABSTRACTS.exists():
        with open(ABSTRACTS, encoding="utf-8") as f:
            abstracts = json.load(f)
    else:
        print("No abstracts.json, so every question comes from the site summary.")

    studies = load_studies()
    if args.limit:
        studies = studies[:args.limit]
    done = {item["target"] for item in payload["items"]}

    for study in studies:
        if study["id"] in done:
            continue
        description, basis = describe(study, abstracts)
        question, leak, attempts = generate(description, args.model)
        payload["items"].append({
            "qid": "t%03d" % (len(payload["items"]) + 1), "target": study["id"],
            "question": question, "basis": basis, "leak": round(leak, 3),
            "attempts": attempts})
        save(payload)
        print("%-9s %s  (%s, leak %.2f, %d attempt%s)"
              % (study["id"], question[:80], basis, leak, attempts,
                 "" if attempts == 1 else "s"))

    items = payload["items"]
    mean_leak = sum(i["leak"] for i in items) / max(len(items), 1)
    retried = sum(1 for i in items if i["attempts"] > 1)
    from_abstract = sum(1 for i in items if i["basis"] == "abstract")
    print("\n%d questions, %d from an abstract and %d from the site summary"
          % (len(items), from_abstract, len(items) - from_abstract))
    print("mean leak %.3f, %d needed more than one attempt" % (mean_leak, retried))
    print("written to", OUT)
    print("\nNext: read a sample and freeze it (plan Task 12).")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
