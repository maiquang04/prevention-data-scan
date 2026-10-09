"""Turn a question into tags by deciding in stages, narrowing as it goes.

The flat mapper in map_query.py asks for all eight kinds of tag in one go, against
all 101 values. It answers well but picks too many: 4.44 tags per question, and on a
third of questions no single source carries all of them.

This asks three smaller questions instead:

  1. Which kinds of detail does the question actually give?
  2. For those kinds only, which values? Health stops at the focus here.
  3. Only if a health focus was chosen: does the question name a specific measure?
     Choosing none is a real answer, and means searching by the broader focus.

The point is stage 3. A question like "what helps people be more active" should stay
at Physical Activity rather than guessing one of its eighteen measures. Giving the
model a smaller list at each step, and an explicit way to stop, is what makes that
possible.

Costs one to three calls per question instead of one, so it is slower.
"""
import sys
import time
from dataclasses import dataclass, field

import llm
from config import MODEL_MAIN
from map_query import Mapping, _empty_tags, parse_and_validate
from vocab import (GROUPS, GROUP_MEANING, LABELS, indicators_under, json_schema,
                   load_meta, load_vocab, vocab_prompt_block)

# Stage 1 asks about every kind except the measures, which are only reachable
# through a health focus at stage 3.
KINDS = [g for g in GROUPS if g != "indicator"]

STAGE1 = """A question about public-health data has to be turned into search tags.

Before choosing any tag, decide which kinds of detail the question actually gives.

The kinds
%s

Rules
1. Name a kind only if the question says something about it. Most questions give two
   or three at most.
2. Do not name a kind because a useful source would have it anyway. Every source has
   a geography; that is not a reason to say the question mentions one.
3. A vague "this region", "our area" or "here" names no place, so it is not Region
   and not Geography.
4. If the question is about a subject the collection may not cover at all, name no
   kinds.
5. Write "reasoning" first: one plain sentence on what the user is asking for.

Reply with JSON only: {"reasoning": "...", "kinds": ["..."]}

Example
Question: Is there anything on what children eat?
Answer: {"reasoning": "The user wants data on children's diet, which is a health subject and nothing else.", "kinds": ["domain"]}

Example
Question: What can I get for Brisbane without filling in an application?
Answer: {"reasoning": "The user names a council area and wants data they can simply download, but names no subject.", "kinds": ["geo", "access"]}"""

STAGE2 = """A question about public-health data has to be turned into search tags.

Choose the values for the kinds listed below, and only those kinds.

Rules
1. Copy values exactly from the lists. Never invent or reword one.
2. **The kinds below were a first guess, and you may overrule them.** If nothing in a
   list genuinely fits the question, leave that list empty. An empty list is a real
   answer and a better one than a value that is merely nearby.
3. Choose one value per kind, unless the question genuinely combines two. A question
   about mental health at work is both Mental Wellbeing and Prosperity & Productivity,
   and a question about who misses out on something is that subject and Equity.
4. Health focus only here. Do not try to name a specific measure; that comes later.
5. Write "reasoning" first: one or two plain sentences on why each value fits.

Reply with JSON only, in exactly this shape:
%s

Example of overruling the guess
Question: what data is there on hospital car parking fees?
Answer: nothing in the lists is about car parking, so every list is empty, even the
kinds named below.

THE LISTS
%s"""

STAGE3 = """A question about public-health data has to be turned into search tags.

The health focus is already decided: %s.

Now decide whether the question names one specific measure within it, or whether it
is broader than any single measure.

Rules
1. Choose a measure only if the question is plainly about that exact thing.
2. **Choosing none is the right answer for a broad question.** "What helps people be
   more active" is about the whole focus, not one measure of it. An empty list means
   the search uses the focus itself, which is wider and safer than a wrong guess.
3. Never choose a measure merely because it is related to the subject.
4. Copy names exactly from the list.
5. Write "reasoning" first: one plain sentence saying whether the question is about a
   specific measure, and which.

Reply with JSON only: {"reasoning": "...", "indicator": []}

THE MEASURES
%s"""


@dataclass
class Stage:
    """One question put to the model, for showing the working."""
    number: int
    name: str
    reasoning: str = ""
    chose: list = field(default_factory=list)
    seconds: float = 0.0


def _kinds_schema():
    return {"type": "object",
            "properties": {"reasoning": {"type": "string"},
                           "kinds": {"type": "array",
                                     "items": {"type": "string", "enum": list(KINDS)}}},
            "required": ["reasoning", "kinds"]}


def _kinds_block():
    """The kinds as the model reads them.

    The key has to appear beside the label, because the key is what the schema
    makes it answer with. Without it the model could only guess, and "domain" is
    not guessable from "Health focus": in an early run it chose Access, Kind of
    source and Region for a question about physical activity, and never once chose
    the health subject.
    """
    return "\n".join('- %s (answer with "%s"): %s' % (LABELS[g], g, GROUP_MEANING[g])
                     for g in KINDS)


def _shape(groups):
    """The reply shape written out, so the model sees the keys it must use."""
    return '{"reasoning": "...", %s}' % ", ".join('"%s": []' % g for g in groups)


def _ask(messages, model, schema, on_text):
    """One call. Returns the raw reply and how long it took."""
    started = time.perf_counter()
    raw = llm.chat(messages, model=model, temperature=0.0, max_tokens=400,
                   schema=schema, on_text=on_text)
    return raw, time.perf_counter() - started


def map_staged(question, model=MODEL_MAIN, meta=None, vocab=None, on_text=None,
               on_stage=None):
    """Ask in stages. Returns a Mapping, with the stages attached for display.

    `on_stage(stage)` is called as each one finishes, so a page can show the working
    as it happens. `on_text` streams within the current stage, as in map_query.
    """
    meta = load_meta() if meta is None else meta
    vocab = load_vocab(meta) if vocab is None else vocab
    tags, rejected, stages = _empty_tags(), [], []
    started = time.perf_counter()

    def fail(err):
        return Mapping(question, model, _empty_tags(), "", rejected,
                       time.perf_counter() - started, True, str(err))

    # Stage 1: which kinds of detail does the question give?
    try:
        raw, secs = _ask([{"role": "system", "content": STAGE1 % _kinds_block()},
                          {"role": "user", "content": "Question: " + question}],
                         model, _kinds_schema(), on_text)
    except llm.LLMError as err:
        return fail(err)
    parsed, reasoning, bad = parse_and_validate(raw, {"kinds": KINDS})
    kinds = [g for g in KINDS if g in parsed.get("kinds", [])]
    rejected += bad
    stage = Stage(1, "Which kinds of detail the question gives", reasoning,
                  [LABELS[g] for g in kinds], round(secs, 2))
    stages.append(stage)
    if on_stage:
        on_stage(stage)

    if not kinds:
        # Nothing to search by. rank() then falls back to the words of the question,
        # and the page says so rather than inventing tags.
        return Mapping(question, model, tags, reasoning, rejected,
                       time.perf_counter() - started, True, "", stages=stages)

    # Stage 2: the values, with health stopping at the focus.
    try:
        raw, secs = _ask(
            [{"role": "system",
              "content": STAGE2 % (_shape(kinds), vocab_prompt_block(meta, vocab, kinds))},
             {"role": "user", "content": "Question: " + question}],
            model, json_schema(vocab, groups=kinds), on_text)
    except llm.LLMError as err:
        return fail(err)
    parsed, reasoning, bad = parse_and_validate(raw, {g: vocab[g] for g in kinds})
    rejected += bad
    for group in kinds:
        tags[group] = parsed.get(group, [])
    chose = ["%s: %s" % (LABELS[g], ", ".join(tags[g])) for g in kinds if tags[g]]
    stage = Stage(2, "Which values", reasoning, chose, round(secs, 2))
    stages.append(stage)
    if on_stage:
        on_stage(stage)

    # Stage 3: a specific measure, or stay at the focus.
    focuses = tags.get("domain") or []
    measures = indicators_under(meta, focuses) if focuses else []
    if measures:
        try:
            raw, secs = _ask(
                [{"role": "system",
                  "content": STAGE3 % (" and ".join(focuses), "; ".join(measures))},
                 {"role": "user", "content": "Question: " + question}],
                model, json_schema(vocab, groups=["indicator"],
                                   choices={"indicator": measures}), on_text)
        except llm.LLMError as err:
            return fail(err)
        parsed, reasoning, bad = parse_and_validate(raw, {"indicator": measures})
        rejected += bad
        tags["indicator"] = parsed.get("indicator", [])
        stage = Stage(3, "Whether one measure is named", reasoning,
                      tags["indicator"] or ["none, so searching by the focus"],
                      round(secs, 2))
        stages.append(stage)
        if on_stage:
            on_stage(stage)

    # The reasoning shown to a reader is the last stage that explained a choice.
    why = next((s.reasoning for s in reversed(stages) if s.reasoning), "")
    return Mapping(question, model, tags, why, rejected,
                   time.perf_counter() - started, True, "", stages=stages)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    meta = load_meta()
    vocab = load_vocab(meta)
    asked = sys.argv[1] if len(sys.argv) > 1 else (
        "What helps people be more physically active day to day?")
    result = map_staged(asked, MODEL_MAIN, meta, vocab)
    print("Question:", asked)
    if result.error:
        print("ERROR:", result.error)
    else:
        for stage in result.stages:
            print("\nStage %d, %s  (%.1fs)" % (stage.number, stage.name, stage.seconds))
            print("  why  :", stage.reasoning)
            print("  chose:", "; ".join(stage.chose) or "nothing")
        print("\ntags    :", {g: v for g, v in result.tags.items() if v})
        print("rejected:", result.rejected)
        print("total   : %.1fs over %d call(s)" % (result.seconds, len(result.stages)))
