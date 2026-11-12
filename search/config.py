"""Settings for the search prototype. Every other file imports from here."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # search/
SITE_DIR = ROOT.parent                            # the site this searches
STUDIES_JSON = SITE_DIR / "data" / "studies.json"
META_JSON = SITE_DIR / "data" / "meta.json"
DATA = ROOT / "data"

SITE_URL = "https://maiquang04.github.io/prevention-data-scan/"
# Where the model answers. LM Studio on this computer unless LLM_URL says otherwise;
# Docker Compose sets it to the model container. Any OpenAI-compatible server works
# if it honours a JSON schema in response_format.
LMSTUDIO_URL = os.environ.get("LLM_URL", "http://127.0.0.1:1234/v1")
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

# How the chosen tags order the sources.
#   "tags-first"  scores every source by tags, word matching breaks ties
#   "bm25-first"  word matching shortlists, then the tags reorder it
# "bm25-first" won when both were measured on the same tags in October 2026
# (right source in the top ten 71% -> 81% of 80 test questions).
#
# Choosing the tags in three narrowing steps instead of one was also tried then, and
# found the right source less often on the vaguest questions. That code and the
# evaluation that measured it are in the repository's history, not in the tree.
RANKER = "bm25-first"

# How many sources word matching shortlists for "bm25-first". Chosen from its own
# reach, not from any end-to-end score: in October 2026 its top 30 held the right
# source for 97.5% of the test questions, so looking deeper gains little.
RECALL_DEPTH = 30
