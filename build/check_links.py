# -*- coding: utf-8 -*-
"""Check that every outbound link in the scan still resolves.

The scan has already produced links that were dead or pointed at the wrong record,
and a dead link in front of an external reader is worse than no link. Run this
before showing the site to anyone:

    python build/check_links.py

Three outcomes get kept apart, because confusing them wastes an afternoon:

  the server replied 404      the link really is broken - fix the workbook
  the server replied 403      a publisher blocking robots; open it in a browser
  nothing replied at all      a network problem here, not a problem with the link

If nothing replies at all, the script says so and stops rather than reporting every
link as broken. Under Windows Subsystem for Linux outbound HTTPS often fails while
the same links open fine in the browser, so run it from Windows PowerShell.

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
    """Return (http_status, final_url, error).

    A server answering "404" and the network never reaching the server at all are
    completely different findings, so they come back as different things: a status
    code means the site replied, and an error means we never got that far.
    """
    request = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.geturl(), None
    except urllib.error.HTTPError as err:
        return err.code, url, None                # the server replied, just not with 200
    except Exception as err:                      # DNS failure, timeout, TLS handshake
        return None, url, str(err)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--give-up-after", type=int, default=5,
                        help="stop once this many checks in a row fail to connect at all")
    args = parser.parse_args()

    links = collect()
    print("Checking %d links...\n" % len(links))

    dead, blocked, moved, unreachable = [], [], [], []
    in_a_row = 0
    checked = 0

    for study_id, kind, url in links:
        status, final, error = check(url, args.timeout)
        checked += 1
        print("%-6s %-6s %-9s %s" % (status if status else "----", kind, study_id, url))

        if status is None:
            unreachable.append((study_id, url, error))
            in_a_row += 1
            if in_a_row >= args.give_up_after and not (dead or blocked or moved):
                print("\nStopping after %d checks in a row that could not connect." % in_a_row)
                break
            continue

        in_a_row = 0
        if status in BLOCKED:
            blocked.append((study_id, url, status))
        elif status >= 400:
            dead.append((study_id, url, status))
        elif final.rstrip("/") != url.rstrip("/"):
            moved.append((study_id, url, final))

    print("")

    # Nothing answered at all. That is this machine, not these links - saying
    # "38 broken links" here would be alarming and wrong.
    if unreachable and len(unreachable) == checked:
        print("Could not reach any site. This looks like a network problem on this machine,")
        print("not a problem with the links - none of them got as far as a reply.")
        print("")
        print("  %s" % unreachable[0][2])
        print("")
        print("Under Windows Subsystem for Linux, outbound HTTPS often fails while the same")
        print("links open fine in the browser. Run this from Windows PowerShell instead:")
        print("")
        print("  python build\\check_links.py")
        return 2

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
    if unreachable:
        print("Could not be checked - no reply at all. Not necessarily broken; try again")
        print("or open them in a browser:")
        for study_id, url, error in unreachable:
            print("  %s  %s\n    %s" % (study_id, url, error))
        print("")
    if dead:
        print("BROKEN - the server replied with an error. Fix these in the workbook, then")
        print("re-run build/export_data.py:")
        for study_id, url, status in dead:
            print("  %s  HTTP %s  %s" % (study_id, status, url))
        return 1

    if unreachable:
        return 2
    print("No broken links.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
