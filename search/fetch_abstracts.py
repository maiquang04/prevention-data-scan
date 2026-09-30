"""Fetch abstracts for the sources that are papers with a DOI or PubMed link.

The test questions are built from the abstracts of the papers in the scan. Only some
of the 80 sources are papers with a findable abstract; the rest are datasets and
reports, and make_testset.py falls back to the site's own summary for those.

Europe PMC is used because it is free, needs no key and covers PubMed, PubMed
Central and DOIs in one search.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from config import DATA
from corpus import load_studies

SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OUT = DATA / "abstracts.json"


def identify(link):
    """The Europe PMC query for this link, or None if it is not a paper link."""
    link = link or ""
    if "doi.org/" in link:
        return 'DOI:"%s"' % link.split("doi.org/", 1)[1].strip("/")
    found = re.search(r"pmc\.ncbi[^\s]*?PMC(\d+)", link, re.I)
    if found:
        return "PMCID:PMC" + found.group(1)
    found = re.search(r"pubmed\.ncbi[^\s]*?/(\d+)", link, re.I)
    if found:
        return "EXT_ID:%s AND SRC:MED" % found.group(1)
    return None


def clean(text):
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())


def fetch(query, attempts=4):
    """The first record's abstract, or (None, why not)."""
    url = "%s?query=%s&resultType=core&format=json" % (SEARCH, urllib.parse.quote(query))
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                body = json.load(r)
            break
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < attempts:
                # Europe PMC throttles a burst of requests. Back off further each
                # time rather than hammering it: 10s, 30s, 60s.
                time.sleep([10, 30, 60][min(attempt - 1, 2)])
                continue
            return None, "Europe PMC returned %s" % err.code
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt < attempts:
                time.sleep(5)
                continue
            return None, "could not reach Europe PMC: %s" % err
    else:
        return None, "Europe PMC kept failing"

    results = body.get("resultList", {}).get("result", [])
    if not results:
        return None, "no record in Europe PMC"
    abstract = clean(results[0].get("abstractText"))
    if not abstract:
        return None, "record found but it carries no abstract"
    return abstract, ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true",
                        help="fetch everything again instead of filling the gaps")
    args = parser.parse_args()

    wanted = [(s["id"], identify(s.get("link"))) for s in load_studies()]
    wanted = [(sid, query) for sid, query in wanted if query]

    # Keep what a previous run already got. Europe PMC throttles, so a rerun is
    # normally about filling the handful of gaps rather than starting over.
    out = {}
    if OUT.exists() and not args.overwrite:
        with open(OUT, encoding="utf-8") as f:
            out = json.load(f)
    todo = [(sid, q) for sid, q in wanted
            if not (out.get(sid) or {}).get("abstract")]
    if out and todo:
        print("%d already found, trying the remaining %d" % (len(out) - len(todo),
                                                             len(todo)))

    DATA.mkdir(parents=True, exist_ok=True)
    for sid, query in todo:
        abstract, note = fetch(query)
        out[sid] = {"abstract": abstract, "via": "europepmc", "query": query,
                    "note": note}
        print("%-9s %s" % (sid, "%d words" % len(abstract.split()) if abstract else note),
              flush=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        time.sleep(2)                  # be polite to a free service

    found = sum(1 for v in out.values() if v["abstract"])
    print("\nabstracts found for %d of %d" % (found, len(wanted)))
    missing = {sid: v["note"] for sid, v in out.items() if not v["abstract"]}
    if missing:
        print("still missing:", json.dumps(missing, indent=2))
    print("written to", OUT)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
