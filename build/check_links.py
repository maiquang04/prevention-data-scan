# -*- coding: utf-8 -*-
"""Check that every outbound link in the scan still resolves.

The scan has already produced links that were dead or pointed at the wrong record,
and a dead link in front of an external reader is worse than no link. Run this
before showing the site to anyone:

    python build/check_links.py

Publishers block automated requests fairly often, so a 403 is reported separately
from a 404 - it usually means "open it in a browser to confirm", not "broken".
Nothing here changes any file; it only reports.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Without a browser-like user agent a lot of publishers refuse outright.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BLOCKED = {401, 403, 429}


def collect():
    with open(os.path.join(REPO, "data", "studies.json"), encoding="utf-8") as handle:
        studies = json.load(handle)
    seen, links = set(), []
    for study in studies:
        candidates = [("source", study.get("link", ""))]
        candidates += [("data", link["url"]) for link in study.get("dataLinks", []) if link["url"]]
        for kind, url in candidates:
            if url and url not in seen:
                seen.add(url)
                links.append((study["id"], kind, url))
    return links


def check(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.geturl()
    except urllib.error.HTTPError as err:
        return err.code, url
    except Exception as err:                      # DNS failure, timeout, bad certificate
        return str(err), url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    links = collect()
    print("Checking %d links...\n" % len(links))

    dead, blocked, moved = [], [], []
    for study_id, kind, url in links:
        status, final = check(url, args.timeout)
        print("%-6s %-6s %-9s %s" % (status, kind, study_id, url))
        if isinstance(status, int):
            if status in BLOCKED:
                blocked.append((study_id, url, status))
            elif status >= 400:
                dead.append((study_id, url, status))
            elif final.rstrip("/") != url.rstrip("/"):
                moved.append((study_id, url, final))
        else:
            dead.append((study_id, url, status))

    print("")
    if moved:
        print("Redirected - the link still works, but the destination has moved:")
        for study_id, url, final in moved:
            print("  %s\n    %s\n    -> %s" % (study_id, url, final))
        print("")
    if blocked:
        print("Blocked automated access - open these in a browser to confirm:")
        for study_id, url, status in blocked:
            print("  %s  HTTP %s  %s" % (study_id, status, url))
        print("")
    if dead:
        print("BROKEN - fix these in the workbook, then re-run build/export_data.py:")
        for study_id, url, status in dead:
            print("  %s  %s  %s" % (study_id, status, url))
        return 1

    print("No broken links.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
