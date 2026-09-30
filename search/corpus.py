"""The sources being searched: their text, their tags, and links back to the site."""
import json
import sys
import urllib.parse

from config import SITE_URL, STUDIES_JSON
from vocab import (GROUPS, indicator_domain, load_meta, load_vocab,
                   topic_id_to_title, topic_title_to_id)

# Everything a person might type a word from. Left out: the tag fields themselves,
# which are added separately below, and ids.
TEXT_FIELDS = ["reference", "task", "metrics", "inputData", "output", "dataSources",
               "accessNote", "scale", "keyAttributes", "geoLevel", "country"]


def load_studies(path=STUDIES_JSON):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def study_index(studies):
    return {s["id"]: s for s in studies}


def source_text(study):
    """Everything about a source as one string, for word matching."""
    parts = [str(study.get(f) or "") for f in TEXT_FIELDS]
    parts.extend(study.get("indicators") or [])
    parts.extend(study.get("domains") or [])
    return " ".join(parts)


def source_tags(study, meta):
    """The tags this source carries, per group, worded as the model sees them."""
    id_to_title = topic_id_to_title(meta)
    fee = study.get("applicationFee")
    return {
        # studies.json stores topic ids; the model is shown titles.
        "topic": {id_to_title[t] for t in (study.get("topics") or []) if t in id_to_title},
        "domain": set(study.get("domains") or []),
        "indicator": set(study.get("indicators") or []),
        "geo": set(study.get("geoTags") or []),
        "access": {study["access"]} if study.get("access") else set(),
        # "Not applicable" means there is no application to pay for, so no fee tag.
        "fee": set() if not fee or fee == "Not applicable" else {fee},
        "type": {study["sourceGroup"]} if study.get("sourceGroup") else set(),
        "region": set(study.get("regions") or []),
    }


def link_tags(tags, meta):
    """The tags as the site's filters would have to be set to show the same sources.

    The site puts health focus and measures in one filter and matches either of
    them (matchesHealthTree in assets/browse.js), while the ranking here treats
    them as two separate things the question asked for. So sending both would
    widen the page rather than narrow it: asking for Physical Activity and the
    Walkability Index shows 11 sources on the site but only 3 carry both.

    A measure already implies its health focus, so the focus is dropped from the
    link whenever a measure under it was chosen. Any focus not covered that way is
    kept, and then the page can show more than the ranking counted.
    """
    measures = tags.get("indicator") or []
    if not measures or not (tags.get("domain") or []):
        return dict(tags)
    parents = {indicator_domain(meta).get(name) for name in measures}
    out = dict(tags)
    out["domain"] = [d for d in tags["domain"] if d not in parents]
    return out


def browse_url(tags, meta, base=SITE_URL):
    """The same filters, opened on the live site.

    browse.js reads one parameter per group and splits it on commas, so a group
    with no values is left out entirely rather than sent empty. The search page
    passes base="" to get a relative link, so the filters open on the copy of the
    site it serves itself, next to it, rather than on the live one.
    """
    title_to_id = topic_title_to_id(meta)
    tags = link_tags(tags, meta)
    parts = []
    for group in GROUPS:
        values = tags.get(group) or []
        if not values:
            continue
        if group == "topic":
            values = [title_to_id.get(v, v) for v in values]
        parts.append(group + "=" + ",".join(urllib.parse.quote(v, safe="") for v in values))
    return base + "browse.html" + ("?" + "&".join(parts) if parts else "")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    meta = load_meta()
    studies = load_studies()
    print(len(studies), "sources")
    counts = {g: sum(1 for s in studies if source_tags(s, meta)[g]) for g in GROUPS}
    print("sources carrying at least one tag, per group:", counts)
    unknown = set()
    vocab = load_vocab(meta)
    for study in studies:
        tags = source_tags(study, meta)
        for group in GROUPS:
            unknown |= {group + ":" + v for v in tags[group] if v not in vocab[group]}
    print("tags on sources that are not in the vocabulary:", sorted(unknown) or "none")
