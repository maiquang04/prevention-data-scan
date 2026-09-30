"""Turn a plain question into tags the collection already uses.

The design: give the model the list of attributes the scan already uses, let it reason
about which ones a useful source would carry, and search on those. Whatever comes back
is checked against the vocabulary here in Python, so a value the model invents can
never reach the search.
"""
import json
import re
import sys
import time
from dataclasses import dataclass, field

import llm
from config import MODEL_MAIN
from vocab import GROUPS, json_schema, load_meta, load_vocab, vocab_prompt_block

SYSTEM = """You turn a question about public-health data into search tags.

The collection holds research papers, government reports and datasets about preventing
ill health in Queensland and Australia. Every source carries tags from the fixed lists at
the end of this message. Choose the tags that a useful source for this question would
carry.

Rules
1. Copy values exactly from the lists. Never invent or reword a value.
2. Pick a tag only when the question clearly calls for it. Leave a group as an empty
   list when the question says nothing about it. A few sure tags are better than many
   guesses, because every extra tag makes a full match less likely.
3. Measures are the most specific tags. Pick a measure only when the question is about
   that exact thing. Otherwise pick its Health focus instead.
4. Access: "public", "free to download" or "open" point to Public. Application fee:
   only a question about paying to get the data points to a fee value.
5. Geography: "council" points to LGA; "suburb" or "neighbourhood" to Small area.
   Region: pick one only when the question names Queensland, Australia or overseas.
   A vague "this region" or "my area" is not enough for either.
6. Kind of source: pick one only if the user insists on one kind.
7. Write "reasoning" first: one or two plain sentences on what the user needs and why
   each chosen tag fits.

Reply with JSON only, in exactly this shape:
{"reasoning": "...", "topic": [], "domain": [], "indicator": [], "geo": [], "access": [], "fee": [], "type": [], "region": []}

Example
Question: I need Queensland survey data on fruit and vegetable intake that I can
download for free.
Answer: {"reasoning": "The user wants free, downloadable Queensland data on fruit and vegetable intake, which are named measures.", "topic": [], "domain": [], "indicator": ["Fruit intake", "Vegetable intake"], "geo": [], "access": ["Public"], "fee": [], "type": [], "region": ["Queensland"]}

Example
Question: What research shows prevention can reduce hospital admissions?
Answer: {"reasoning": "The user wants evidence linking prevention to hospital demand, which is the Health system impact focus area.", "topic": ["Health system impact"], "domain": [], "indicator": [], "geo": [], "access": [], "fee": [], "type": [], "region": []}

THE LISTS
{lists}"""


@dataclass
class Mapping:
    question: str
    model: str
    tags: dict
    reasoning: str
    rejected: list = field(default_factory=list)
    seconds: float = 0.0
    used_schema: bool = True
    error: str = ""
    raw: str = ""

    def chosen(self):
        """Only the groups that got a tag, for printing."""
        return {g: v for g, v in self.tags.items() if v}


def build_messages(question, meta, vocab):
    # .replace, not .format: the prompt is full of literal braces.
    system = SYSTEM.replace("{lists}", vocab_prompt_block(meta, vocab))
    return [{"role": "system", "content": system},
            {"role": "user", "content": "Question: " + question}]


def _empty_tags():
    return {g: [] for g in GROUPS}


def parse_and_validate(raw, vocab):
    """Read the reply, keep only values that really are tags.

    Returns (tags, reasoning, rejected). A value the model made up goes into
    rejected as "group:value" so the run can count how often that happens.
    """
    data = None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Models sometimes wrap the JSON in a sentence or a code fence.
        start, end = (raw or "").find("{"), (raw or "").rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        return _empty_tags(), "", ["<unparseable>"]

    tags, rejected = {}, []
    for group in GROUPS:
        value = data.get(group)
        if isinstance(value, str):
            value = [value]
        elif not isinstance(value, list):
            value = []
        kept = []
        for item in value:
            if item in vocab[group]:
                if item not in kept:
                    kept.append(item)
            else:
                rejected.append("%s:%s" % (group, item))
        tags[group] = kept

    reasoning = data.get("reasoning")
    return tags, reasoning if isinstance(reasoning, str) else "", rejected


_REASONING_SO_FAR = re.compile(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)', re.S)


def partial_reasoning(raw):
    """The reasoning sentence out of a reply that is still being written.

    The schema puts "reasoning" first, so a streamed reply starts
    {"reasoning": "The user wants... and the rest is not there yet. This pulls out
    whatever of that string has arrived. A piece can end half-way through an escape
    such as \\" or \\u00e9, which is not valid on its own, so up to six characters
    are trimmed off the end until what is left reads cleanly.
    """
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    if "<think>" in text:
        return ""               # a hybrid model still thinking; nothing to show yet
    found = _REASONING_SO_FAR.search(text)
    if not found:
        return ""
    body = found.group(1)
    for cut in range(0, 7):
        try:
            return json.loads('"' + body[:len(body) - cut] + '"')
        except json.JSONDecodeError:
            continue
    return ""


def map_query(question, model=MODEL_MAIN, meta=None, vocab=None, use_schema=True,
              on_text=None):
    """Ask the model for tags. on_text, if given, is passed to llm.chat to stream."""
    meta = load_meta() if meta is None else meta
    vocab = load_vocab(meta) if vocab is None else vocab
    messages = build_messages(question, meta, vocab)
    schema = json_schema(vocab) if use_schema else None

    started = time.perf_counter()
    used_schema = use_schema
    try:
        raw = llm.chat(messages, model=model, temperature=0.0, max_tokens=400,
                       schema=schema, on_text=on_text)
    except llm.LLMError as err:
        if schema is not None and "response_format" in str(err):
            # This model or server build cannot constrain the reply. Ask plainly
            # and rely on the check below instead.
            used_schema = False
            try:
                raw = llm.chat(messages, model=model, temperature=0.0, max_tokens=400,
                               on_text=on_text)
            except llm.LLMError as err2:
                return Mapping(question, model, _empty_tags(), "",
                               seconds=time.perf_counter() - started,
                               used_schema=False, error=str(err2))
        else:
            return Mapping(question, model, _empty_tags(), "",
                           seconds=time.perf_counter() - started,
                           used_schema=used_schema, error=str(err))
    seconds = time.perf_counter() - started

    tags, reasoning, rejected = parse_and_validate(raw, vocab)
    return Mapping(question, model, tags, reasoning, rejected, seconds, used_schema,
                   raw=raw)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    meta = load_meta()
    vocab = load_vocab(meta)
    question = sys.argv[1] if len(sys.argv) > 1 else (
        "what public data is available for walkability in this region")
    result = map_query(question, MODEL_MAIN, meta, vocab)
    print(result.error or result.reasoning)
    print(result.chosen())
    print("rejected:", result.rejected, "schema:", result.used_schema,
          "secs:", round(result.seconds, 1))
