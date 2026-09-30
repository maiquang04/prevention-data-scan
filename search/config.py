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
