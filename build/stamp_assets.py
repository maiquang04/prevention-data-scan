# -*- coding: utf-8 -*-
"""Stamp a content hash onto every stylesheet and script reference.

GitHub Pages tells browsers to cache assets for ten minutes, and its CDN caches
them too. After a push that changes two files, a visitor can end up holding the new
copy of one and the old copy of the other. That has already happened once here: a
cached common.js met a fresh quadrants.js and the page died on a missing function.

Adding ?v=<hash of the file> to each reference makes the address change whenever the
file changes, so a browser can never pair an old asset with a new one.

    python build/stamp_assets.py

export_data.py calls this at the end, and check_publish.py refuses to publish if any
stamp is out of date, so a forgotten run cannot reach the live site.
"""
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

PAGES = ["index.html", "browse.html", "application.html", "study.html"]

# href="assets/style.css" or src="assets/common.js", with or without an old stamp.
REF_RE = re.compile(r'((?:href|src)=")(assets/[A-Za-z0-9_.-]+\.(?:css|js))(\?v=[0-9a-f]+)?(")')


def digest(rel_path):
    """First 8 hex characters of the file's SHA-256. Short enough to stay readable,
    long enough that two different files will not collide in a site this size."""
    full = os.path.join(REPO, rel_path)
    if not os.path.exists(full):
        return None
    with open(full, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:8]


def _rewrite(text, on_missing):
    changed = []

    def swap(match):
        opener, asset, old, closer = match.groups()
        stamp = digest(asset)
        if stamp is None:
            on_missing(asset)
            return match.group(0)
        new = "?v=" + stamp
        if old != new:
            changed.append((asset, old, new))
        return opener + asset + new + closer

    return REF_RE.sub(swap, text), changed


def stamp(quiet=False):
    missing = []
    touched = 0

    for page in PAGES:
        path = os.path.join(REPO, page)
        with open(path, encoding="utf-8") as handle:
            before = handle.read()

        after, changed = _rewrite(before, missing.append)
        if after != before:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(after)
            touched += 1
            if not quiet:
                for asset, old, new in changed:
                    print("  %-16s %s  %s -> %s" % (page, asset, old or "(none)", new))

    if missing:
        print("Referenced files that do not exist: " + ", ".join(sorted(set(missing))), file=sys.stderr)
        return 1

    if not quiet:
        print("stamp_assets: %s" % ("%d page(s) updated" % touched if touched else "already current"))
    return 0


def verify():
    """Return a list of pages whose stamps no longer match their files."""
    problems = []
    for page in PAGES:
        path = os.path.join(REPO, page)
        if not os.path.exists(path):
            problems.append("%s: missing" % page)
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for match in REF_RE.finditer(text):
            _, asset, old, _ = match.groups()
            stamp_now = digest(asset)
            if stamp_now is None:
                problems.append("%s: references %s, which does not exist" % (page, asset))
            elif old != "?v=" + stamp_now:
                problems.append("%s: %s is stamped %s but the file is now %s - run "
                                "build/stamp_assets.py" % (page, asset, old or "(unstamped)", "?v=" + stamp_now))
    return problems


if __name__ == "__main__":
    sys.exit(stamp())
