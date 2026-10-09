"""Settings for the search prototype. Every other file imports from here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # search/
SITE_DIR = ROOT.parent                            # the site this searches
STUDIES_JSON = SITE_DIR / "data" / "studies.json"
META_JSON = SITE_DIR / "data" / "meta.json"
KEYWORD_MJS = ROOT / "keyword_baseline.mjs"
DATA = ROOT / "data"
RESULTS = ROOT / "results"

SITE_URL = "https://maiquang04.github.io/prevention-data-scan/"
LMSTUDIO_URL = "http://127.0.0.1:1234/v1"
TIMEOUT_S = 120

# The ids LM Studio reports for each model (plan Task 0). The server also lists
# qwen/qwen3-4b, the older hybrid build, and an embedding model; neither is used.
MODEL_MAIN = "qwen/qwen3-4b-2507"
MODEL_SMALL = "qwen/qwen3-1.7b"
# Hybrid Qwen3 models think out loud unless the prompt says /no_think. The 2507
# instruct build has no thinking mode, so it is not listed here.
NO_THINK_MODELS = {MODEL_SMALL, "qwen/qwen3-4b"}

# How much a matched tag group counts in rank.py. Measures are the most specific.
GROUP_WEIGHTS = {"topic": 1, "domain": 1, "indicator": 2, "geo": 1,
                 "access": 1, "fee": 1, "type": 1, "region": 1}
TOP_K = 10

# Which mapper turns a question into tags, and how the tags then order the sources.
#   MAPPER "flat"   asks for all eight kinds of tag in one go (map_query.py)
#          "staged" asks in three narrowing steps (map_staged.py)
#   RANKER "tags-first"  scores every source by tags, word matching breaks ties
#          "bm25-first"  word matching shortlists, then the tags reorder it
#
# Both were measured on the 80 frozen questions on 9 October, in results/variants.md.
# "bm25-first" won on the same tags: R@10 0.713 -> 0.812, R@5 0.600 -> 0.637.
#
# "staged" was built to stop at a broader tag when a question is not specific, and it
# did: 4.16 tags per question down to 2.10. It still lost, badly, on the questions
# that matter most. On the 20 vaguest it found the right source 40% of the time
# against the flat mapper's 75%, missing 7 that the flat mapper found and gaining
# none (p = 0.016). The extra tags were not noise: on a vague question a source
# matching 3 of 5 tags outranks one matching 1 of 5, and cutting back to 2 tags threw
# that away. It is kept in the repository as a tried and rejected option.
MAPPER = "flat"
RANKER = "bm25-first"

# How many sources word matching shortlists for "bm25-first". Chosen from its own
# reach, not from any end-to-end score: its top 30 holds the right source for 97.5%
# of the test questions and 95% of the vague ones, so looking deeper gains little.
RECALL_DEPTH = 30
