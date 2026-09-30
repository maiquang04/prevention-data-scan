"""BM25: how well each source's text matches the words of a question.

Three ideas, and nothing else:
  IDF   a word in few sources says more than a word in most of them
  k1    the fifth mention of a word adds less than the second
  b     a long source is not a better match just for being long

Used here to break ties between sources that matched the same tags, and to order
things on its own when the model chose no tags at all.
"""
import math
import re
import sys

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text):
    return _TOKEN.findall((text or "").lower())


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tokens = {d: tokenize(t) for d, t in docs.items()}
        self.n = len(self.tokens)
        self.avgdl = sum(len(t) for t in self.tokens.values()) / max(self.n, 1)
        self.df = {}
        for toks in self.tokens.values():
            for w in set(toks):
                self.df[w] = self.df.get(w, 0) + 1
        self.tf = {d: {} for d in self.tokens}
        for d, toks in self.tokens.items():
            for w in toks:
                self.tf[d][w] = self.tf[d].get(w, 0) + 1

    def idf(self, word):
        n = self.df.get(word, 0)
        return math.log((self.n - n + 0.5) / (n + 0.5) + 1)

    def score(self, query):
        """Every source's score for this question, including the zeros."""
        words = tokenize(query)
        out = {}
        for d, toks in self.tokens.items():
            dl, s = len(toks), 0.0
            for w in words:
                f = self.tf[d].get(w, 0)
                if f:
                    s += self.idf(w) * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out[d] = s
        return out

    def rank(self, query, k=10):
        scores = self.score(query)
        ranked = sorted(((s, d) for d, s in scores.items() if s > 0),
                        key=lambda x: (-x[0], x[1]))
        return [(d, s) for s, d in ranked[:k]]


def build_index(studies):
    from corpus import source_text
    return BM25({s["id"]: source_text(s) for s in studies})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    from corpus import load_studies
    index = build_index(load_studies())
    for query in ["walkability index", "children physical activity", "hospital car parking"]:
        print(query, "->", [(d, round(s, 2)) for d, s in index.rank(query, 5)])
