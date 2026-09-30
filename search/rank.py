"""Order the sources by how well they carry the tags the question asked for.

The rule this follows: find "the resources that have all these tags mentioned, or if
not all, we just try our best to find the best match". So a source scores a point for
each tag group it matches, and everything is sorted by that score rather than filtered.
A source carrying every tag comes first; one carrying most of them comes next,
instead of vanishing because the model picked one tag too many.

BM25 on the words of the question breaks ties, and orders the list on its own when
the model chose no tags at all.
"""
import sys
from dataclasses import dataclass

from config import GROUP_WEIGHTS, TOP_K
from corpus import source_tags
from vocab import GROUPS, indicator_domain


@dataclass
class Ranked:
    id: str
    score: float      # weighted tag score
    matched: int      # tag groups this source matched
    asked: int        # tag groups the question used
    bm25: float


def group_matches(src_tags, group, wanted, ind_domain):
    """Does this source answer to any of the wanted values in this group?

    Health focus is a tree on the site: asking for Physical Activity also finds a
    source that carries no domain of its own but does carry a measure filed under
    Physical Activity. Mirrors matchesHealthTree in assets/browse.js.
    """
    if src_tags[group] & set(wanted):
        return True
    if group == "domain":
        return any(ind_domain.get(name) in wanted for name in src_tags["indicator"])
    return False


def rank(question, tags, studies, meta, index, weights=None, k=TOP_K):
    weights = GROUP_WEIGHTS if weights is None else weights
    ind_domain = indicator_domain(meta)
    asked = [g for g in GROUPS if tags.get(g)]
    scores = index.score(question)

    rows = []
    for study in studies:
        src = source_tags(study, meta)
        matched = [g for g in asked if group_matches(src, g, tags[g], ind_domain)]
        score = sum(weights.get(g, 1) for g in matched)
        bm = scores.get(study["id"], 0.0)
        if score == 0 and bm == 0:
            continue          # matches nothing at all, so not a result
        rows.append(Ranked(study["id"], score, len(matched), len(asked), bm))

    rows.sort(key=lambda r: (-r.score, -r.bm25, r.id))
    return rows if k is None else rows[:k]


def full_matches(ranked):
    """How many sources carry every tag the question asked for."""
    return sum(1 for r in ranked if r.asked > 0 and r.matched == r.asked)


def explain(src_tags, tags, ind_domain):
    """Which of the chosen tags this source carries, and which groups it misses.

    Returns (hits, misses), each {group: [values]}. A group counts as a hit when
    the source carries any of its chosen values, the same rule rank() scores by,
    so the number of groups in hits is always the source's `matched`. A group it
    misses lists every value that was asked for in it.
    """
    hits, misses = {}, {}
    via_measures = {ind_domain.get(name) for name in src_tags["indicator"]}
    for group in GROUPS:
        wanted = tags.get(group) or []
        if not wanted:
            continue
        if group == "domain":
            found = [v for v in wanted if v in src_tags["domain"] or v in via_measures]
        else:
            found = [v for v in wanted if v in src_tags[group]]
        if found:
            hits[group] = found
        else:
            misses[group] = list(wanted)
    return hits, misses


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from bm25 import build_index
    from corpus import load_studies
    from vocab import load_meta

    meta, studies = load_meta(), load_studies()
    index = build_index(studies)
    ranked = rank("public walkability data",
                  {"indicator": ["Walkability Index"], "access": ["Public"]},
                  studies, meta, index, k=None)
    print([(r.id, r.matched, r.asked) for r in ranked[:5]], full_matches(ranked))
