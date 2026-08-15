# -*- coding: utf-8 -*-
"""Refuse to let internal material reach a public repository.

This repository is public. The wider project it draws on holds documents marked
OFFICIAL - INTERNAL, meeting transcripts, email threads and a partner agency's
unpublished slides. None of that belongs here, and the failure mode is quiet: one
careless copy-paste and it is in the git history forever.

    python build/check_publish.py

Exits non-zero on anything suspicious. Run it before every push. It is deliberately
noisy - a false alarm costs a few seconds, the alternative costs a lot more.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".vscode", ".idea"}

# Only these extensions are expected in a static site. Anything else is probably a
# document that wandered in from the project folder.
ALLOWED_EXT = {".html", ".css", ".js", ".json", ".md", ".py", ".txt", ".svg",
               ".png", ".jpg", ".jpeg", ".ico", ".webmanifest", ".gitignore", ""}
BANNED_EXT = {".xlsx", ".xls", ".xlsm", ".docx", ".doc", ".eml", ".msg", ".pdf",
              ".pptx", ".ppt", ".csv", ".zip"}

# Phrases that must never appear in a published file. Matched case-insensitively
# on word boundaries where that makes sense.
# Classification markings only ever appear in capitals, and "official population
# maps" is a perfectly ordinary phrase - so these are matched case-sensitively.
BANNED_UPPERCASE = ["OFFICIAL", "OFFICIAL - INTERNAL", "OFFICIAL-INTERNAL", "SENSITIVE"]

BANNED_PHRASES = [
    # the partner agency: naming them publicly is their call to make, not ours
    "Health and Wellbeing Queensland", "HWQLD", "hw.qld.gov.au",
    # named individuals - authors of published papers are fine, colleagues are not
    "Li Kheng", "Drew Armstrong", "Erica Clifford",
    "Rocky Chen", "Shazia Sadiq", "Tong Chen", "@uq.edu.au",
    # internal documents
    "Applications List", "Health Impact and Legacy Measures",
    "Rocky_Mark", "CIRES Resource Scanning", "briefing-notes", "transcript",
]

# Suburb-level detail from the partner's unpublished slide. Their mapping of health
# issues onto named suburbs is their own unpublished analysis.
BANNED_PLACES = [
    "Inala", "Richlands", "Wacol", "Kuraby", "Runcorn", "Calamvale", "Sunnybank",
    "Zillmere", "Rocklea", "Acacia Ridge", "Wynnum West", "Forest Lake", "Chermside",
    "Enoggera", "Keperra", "Bracken Ridge", "Nundah", "Annerley", "Greenslopes",
    "Moorooka", "Stafford",
]

# Substrings that are fine even though they trip a rule above.
ALLOWED_EXCEPTIONS = [
    "build/check_publish.py",   # this file lists the banned words on purpose
]

TEXT_EXT = {".html", ".css", ".js", ".json", ".md", ".py", ".txt", ".svg"}


def walk():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = os.path.join(root, name)
            yield path, os.path.relpath(path, REPO).replace("\\", "/")


def check_files(problems, warnings):
    for path, rel in walk():
        ext = os.path.splitext(rel)[1].lower()
        if ext in BANNED_EXT:
            problems.append("%s: %s files must not be published from this repository" % (rel, ext))
        elif ext not in ALLOWED_EXT:
            warnings.append("%s: unexpected file type %s - check it belongs here" % (rel, ext or "(none)"))


def check_text(problems):
    # Word boundaries throughout, so "Chermside" does not fire on a longer word and
    # a bare surname does not fire inside an unrelated one.
    needles = [(p, re.compile(r"\b" + re.escape(p) + r"\b", re.I))
               for p in BANNED_PHRASES + BANNED_PLACES]
    needles += [(p, re.compile(r"\b" + re.escape(p) + r"\b")) for p in BANNED_UPPERCASE]

    for path, rel in walk():
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        if any(rel.endswith(x) for x in ALLOWED_EXCEPTIONS):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
        except (UnicodeDecodeError, OSError) as err:
            problems.append("%s: could not read as text (%s)" % (rel, err))
            continue
        for number, line in enumerate(lines, 1):
            for phrase, pattern in needles:
                if pattern.search(line):
                    problems.append('%s:%d: contains "%s"' % (rel, number, phrase))


def check_quadrant_ids(problems):
    """Every study id referenced by a card must exist, or the page renders a dead link."""
    data_dir = os.path.join(REPO, "data")
    try:
        with open(os.path.join(data_dir, "studies.json"), encoding="utf-8") as handle:
            known = {s["id"] for s in json.load(handle)}
        with open(os.path.join(data_dir, "quadrants.json"), encoding="utf-8") as handle:
            quad = json.load(handle)
    except (OSError, ValueError) as err:
        problems.append("data: could not read the JSON (%s). Run build/export_data.py first." % err)
        return

    for app, body in quad.get("applications", {}).items():
        for cell, cards in body.get("cells", {}).items():
            for card in cards:
                for sid in card.get("studies", []):
                    if sid not in known:
                        problems.append("quadrants.json: app %s, %s, card '%s' points at unknown "
                                        "study id '%s'" % (app, cell, card["name"], sid))


def main():
    problems, warnings = [], []
    check_files(problems, warnings)
    check_text(problems)
    check_quadrant_ids(problems)

    for warning in warnings:
        print("warning: " + warning)

    if problems:
        print("\nNOT SAFE TO PUBLISH - %d problem(s):" % len(problems))
        for problem in problems:
            print("  - " + problem)
        print("\nFix these before committing. If a hit is a genuine false alarm, add the file "
              "to ALLOWED_EXCEPTIONS in this script and say why.")
        return 1

    print("check_publish: clean (%d warning(s))" % len(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
