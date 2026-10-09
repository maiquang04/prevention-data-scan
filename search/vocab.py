"""The tags a source can carry, and the way they are described to the model.

Every value is read from the site's own meta.json, so when the workbook gains a
measure the prompt gains it too. The only wording written here is GLOSS, which
explains values the model could not guess from their name alone.
"""
import json
import sys

from config import META_JSON

GROUPS = ["topic", "domain", "indicator", "geo", "access", "fee", "type", "region"]

LABELS = {"topic": "Focus area", "domain": "Health focus", "indicator": "Measure",
          "geo": "Geography", "access": "Access", "fee": "Application fee",
          "type": "Kind of source", "region": "Region"}

# Access and fee wording is copied from ACCESS_MEANING and FEE_MEANING in
# prevention-data-scan/assets/common.js, so the model reads what a visitor reads.
# Keep them in step if that file changes.
GLOSS = {
    "geo": {"Address": "a specific address, site or street",
            "Small area": "small statistical areas, about suburb size",
            "LGA": "Local government area",
            "PHN": "Primary Health Network",
            "HHS": "Hospital and Health Service",
            "Remoteness": "broken down by city, regional and remote",
            "Individual": "records about individual people",
            "Any": "varies, or not tied to one geography"},
    "access": {"Public": "Ready to download.",
               "Public (aggregate only)": "Published tables, maps or summaries are available; record-level data is not.",
               "Restricted - application": "The data exists, but you need approval from the data holder.",
               "Not a dataset": "A paper, guide or method rather than data to download.",
               "Not yet assessed": "Access has not been checked yet."},
    "fee": {"Fee required": "Getting the data costs money on top of the application.",
            "No fee": "The application is free.",
            "Not yet assessed": "Whether a fee applies has not been checked yet."},
}


def load_meta(path=META_JSON):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_vocab(meta):
    """Every value the model may choose, per group, in the site's own order.

    "Not applicable" is left out of the fee list because the site's fee filter
    ignores it: it means there is no application to pay for, not a fee answer.
    """
    return {
        "topic": [t["title"] for t in meta["topics"]],
        "domain": list(meta["domains"]),
        "indicator": [i["name"] for i in meta["indicators"]],
        "geo": list(meta["geoTags"]),
        "access": list(meta["accessValues"]),
        "fee": [v for v in meta["applicationFeeValues"] if v != "Not applicable"],
        "type": list(meta["sourceGroups"]),
        "region": list(meta["regions"]),
    }


def topic_title_to_id(meta):
    return {t["title"]: t["id"] for t in meta["topics"]}


def topic_id_to_title(meta):
    return {t["id"]: t["title"] for t in meta["topics"]}


def indicator_domain(meta):
    """Measure name -> the health focus it sits under."""
    return {i["name"]: i["domain"] for i in meta["indicators"]}


def json_schema(vocab, groups=None, choices=None):
    """The shape the reply must take.

    Reasoning is the first property on purpose: the model writes why before it
    writes which, which is the reasoning step to show the user.

    `groups` narrows it to some of the eight, for a question asked about only part
    of the vocabulary. `choices` overrides the allowed values of a group, which is
    how the staged mapper offers only the measures under a focus already chosen,
    rather than all 61.
    """
    groups = list(GROUPS if groups is None else groups)
    choices = choices or {}
    properties = {"reasoning": {"type": "string"}}
    for group in groups:
        allowed = list(choices.get(group, vocab[group]))
        properties[group] = {"type": "array",
                             "items": {"type": "string", "enum": allowed}}
    return {"type": "object", "properties": properties,
            "required": ["reasoning"] + groups}


def indicators_under(meta, domains):
    """The measures filed under these health focuses, in the site's own order."""
    wanted = set(domains)
    return [i["name"] for i in meta["indicators"] if i["domain"] in wanted]


# What each kind of tag is about, for the first stage, where the model is asked
# which kinds the question says anything about at all. Deliberately about the
# question rather than about the data: that is what it is being asked to judge.
GROUP_MEANING = {
    "topic": "the broad area of work the question belongs to",
    "domain": "the health subject, such as activity, food or mental wellbeing",
    "geo": "how fine-grained the places need to be, such as a council or a suburb",
    "access": "whether the data has to be downloadable, or may need an application",
    "fee": "whether paying for the data is mentioned",
    "type": "a kind of source the user insists on, such as a peer-reviewed paper",
    "region": "a named part of the world, such as Queensland or Australia",
}


def _values_line(group, vocab):
    parts = []
    for value in vocab[group]:
        gloss = GLOSS.get(group, {}).get(value)
        parts.append("%s (%s)" % (value, gloss) if gloss else value)
    return "; ".join(parts)


def vocab_prompt_block(meta, vocab, groups=None):
    """The lists as the model reads them, at the end of the prompt.

    `groups` leaves out the ones not being asked about, so a stage that only needs
    geography and access does not carry all 101 values with it.
    """
    wanted = set(GROUPS if groups is None else groups)
    lines = []
    if "topic" in wanted:
        lines.append('Focus area (key "topic"):')
        for topic in meta["topics"]:
            lines.append("- %s: %s" % (topic["title"], topic.get("question", "")))

    if "domain" in wanted:
        lines.append('Health focus (key "domain"): ' + _values_line("domain", vocab))

    if "indicator" in wanted:
        lines.append('Measure (key "indicator"), grouped by health focus:')
        by_domain = {}
        for indicator in meta["indicators"]:
            by_domain.setdefault(indicator["domain"], []).append(indicator["name"])
        for domain in vocab["domain"]:
            if by_domain.get(domain):
                lines.append("- %s: %s" % (domain, "; ".join(by_domain[domain])))

    for group in ["geo", "access", "fee", "type", "region"]:
        if group in wanted:
            lines.append('%s (key "%s"): %s'
                         % (LABELS[group], group, _values_line(group, vocab)))
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    meta = load_meta()
    vocab = load_vocab(meta)
    print({g: len(vocab[g]) for g in GROUPS}, sum(len(v) for v in vocab.values()))
    print(vocab_prompt_block(meta, vocab))
