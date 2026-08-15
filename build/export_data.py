# -*- coding: utf-8 -*-
"""Export the literature and data scan workbook to JSON for the website.

The workbook is the source of truth. Thanh maintains it and Rocky annotates it by
highlighting rows, so nothing here is hand-edited - run this script instead:

    python build/export_data.py

Writes data/studies.json and data/meta.json. data/quadrants.json is hand-authored
and is never touched by this script.

Close the workbook in Excel before running, or openpyxl will read a stale copy.
"""
import argparse
import datetime
import json
import os
import re
import sys

from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_SRC = os.path.normpath(os.path.join(REPO, "..", "lit-scan", "lit-scan-draft.xlsx"))
OUT_DIR = os.path.join(REPO, "data")

HEADER_ROW = 2

# Workbook column heading -> JSON key. Anything not listed here is ignored, so a
# new column in the workbook will not silently leak onto a public website.
FIELDS = {
    "#": "num",
    "Reference": "reference",
    "Link": "link",
    "Source type": "sourceType",
    "Country/system": "country",
    "Task described": "task",
    "Metrics / indicators": "metrics",
    "Geographic level": "geoLevel",
    "Input data": "inputData",
    "Output": "output",
    "Data sources used": "dataSources",
    "Public data sources?": "accessNote",
    "Data sources link": "dataLinkRaw",
    "Scale": "scale",
    "Key attributes": "keyAttributes",
    "Access": "access",
    "Geo tags": "geoTagsRaw",
}

APPLICATIONS = {
    1: {
        "title": "Prevention and the health system",
        "question": "How do we show that prevention changes what happens in hospitals?",
        "blurb": "Waiting lists, bed days, ambulance ramping and potentially preventable hospitalisations.",
    },
    5: {
        "title": "Targeting prevention to reduce health inequities",
        "question": "Where should prevention effort go, so the gap between groups narrows?",
        "blurb": "Differences in health between population groups and between areas, and how they are measured.",
    },
    6: {
        "title": "A prevention measurement framework for local government",
        "question": "How would a council know whether its area is getting healthier?",
        "blurb": "Walkability, green space, food environment and participation, measured at council level.",
    },
}

# Controlled vocabulary. Anything outside these lists is a data-entry mistake and
# the script stops rather than shipping a filter option nobody meant to create.
ACCESS_VALUES = [
    "Public",
    "Public (aggregate only)",
    "Restricted - application",
    "Not a dataset",
    "Not yet assessed",
]
GEO_TAGS = [
    "Address",
    "Small area",
    "LGA",
    "PHN",
    "HHS",
    "Remoteness",
    "State",
    "National",
    "International",
    "Individual",
    "Any",
]

# Two derived facets. The workbook's own "Source type" and "Country/system" columns
# are prose and have around a dozen distinct values each, which makes a useless
# filter. These roll them into buckets people actually filter by. The original
# wording is still shown on the record.
SOURCE_GROUPS = [
    "Peer-reviewed",
    "Government report",
    "International agency report",
    "Academic or institutional report",
]
REGIONS = ["Queensland", "Australia", "International"]

URL_RE = re.compile(r"https?://\S+")


def source_group(source_type):
    text = source_type.lower()
    if text.startswith("peer-reviewed"):
        return "Peer-reviewed"
    if "government" in text:
        return "Government report"
    if "international agency" in text:
        return "International agency report"
    return "Academic or institutional report"


def regions_of(country):
    """Australian and Queensland evidence is wanted first, so that is the split
    worth filtering on - not the fifteen different country strings."""
    text = country.lower()
    tags = []
    if "queensland" in text:
        tags.append("Queensland")
    if "australia" in text:
        tags.append("Australia")
    if not tags or any(word in text for word in ("global", "countries", "oecd", "many")):
        tags.append("International")
    return tags


def clean(value):
    """Collapse whitespace; treat blanks and None alike."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def priority_of(row):
    """Rocky marks relevance by filling cells: yellow relevant, red highly relevant."""
    for cell in row:
        fill = cell.fill
        if fill is not None and fill.fill_type == "solid":
            if getattr(fill.start_color, "rgb", None) == "FFFFFF00":
                return "relevant"
            if getattr(fill.start_color, "theme", None) == 5:
                return "highly-relevant"
    return "unmarked"


def parse_links(text):
    """Split a free-text cell into labelled links.

    Cells hold anything from a bare URL to 'Office for National Statistics:
    https://... Deprivation data: https://...'. Take each URL, and use whatever
    text sits in front of it as the label.
    """
    text = clean(text)
    if not text:
        return []
    links, cursor = [], 0
    for match in URL_RE.finditer(text):
        label = text[cursor:match.start()].strip(" -:;,")
        url = match.group(0).rstrip(".,;)")
        links.append({"label": label, "url": url})
        cursor = match.end()
    if not links:
        # No URL at all - the cell is a note, not a link.
        return [{"label": text, "url": ""}]
    return links


def app_number(sheet_title):
    match = re.search(r"App\s*(\d+)", sheet_title)
    if not match:
        raise SystemExit("Sheet name does not start with an application number: " + sheet_title)
    return int(match.group(1))


def export(src, out_dir, quiet=False):
    if not os.path.exists(src):
        raise SystemExit("Workbook not found: " + src)

    workbook = load_workbook(src, data_only=True)
    studies, problems = [], []

    for sheet in workbook:
        app = app_number(sheet.title)
        if app not in APPLICATIONS:
            problems.append("Unknown application number %s in sheet '%s'" % (app, sheet.title))
            continue

        headers = {cell.value: idx for idx, cell in enumerate(sheet[HEADER_ROW]) if cell.value}
        missing = [h for h in FIELDS if h not in headers]
        if missing:
            problems.append("Sheet '%s' is missing columns: %s" % (sheet.title, ", ".join(missing)))
            continue

        for row in sheet.iter_rows(min_row=HEADER_ROW + 1):
            if row[0].value is None:
                continue

            record = {}
            for heading, key in FIELDS.items():
                record[key] = clean(row[headers[heading]].value)

            num = int(record.pop("num"))
            where = "App %d row %d" % (app, num)

            access = record.get("access", "")
            if access not in ACCESS_VALUES:
                problems.append("%s: Access is '%s', not one of the allowed values" % (where, access))

            tags = [t.strip() for t in record.pop("geoTagsRaw", "").split(";") if t.strip()]
            for tag in tags:
                if tag not in GEO_TAGS:
                    problems.append("%s: geo tag '%s' is not in the vocabulary" % (where, tag))
            if not tags:
                problems.append("%s: no geo tags" % where)

            record["id"] = "app%d-%02d" % (app, num)
            record["app"] = app
            record["num"] = num
            record["priority"] = priority_of(row)
            record["geoTags"] = tags
            record["sourceGroup"] = source_group(record.get("sourceType", ""))
            record["regions"] = regions_of(record.get("country", ""))
            record["dataLinks"] = parse_links(record.pop("dataLinkRaw", ""))

            if not record["reference"]:
                problems.append("%s: no reference" % where)

            studies.append(record)

    if problems:
        print("Export stopped - fix the workbook first:", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        raise SystemExit(1)

    studies.sort(key=lambda s: (s["app"], s["num"]))

    counts = {}
    for study in studies:
        counts[study["access"]] = counts.get(study["access"], 0) + 1

    meta = {
        "generated": datetime.date.today().isoformat(),
        "source": os.path.basename(src),
        "total": len(studies),
        "byApplication": {
            str(app): {
                "count": sum(1 for s in studies if s["app"] == app),
                **APPLICATIONS[app],
            }
            for app in sorted(APPLICATIONS)
        },
        "byAccess": counts,
        "accessValues": ACCESS_VALUES,
        "geoTags": GEO_TAGS,
        "sourceGroups": SOURCE_GROUPS,
        "regions": REGIONS,
        "withDownloadableData": sum(
            1 for s in studies if s["access"] in ("Public", "Public (aggregate only)")
        ),
    }

    os.makedirs(out_dir, exist_ok=True)
    for name, payload in (("studies.json", studies), ("meta.json", meta)):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        if not quiet:
            print("wrote %s (%d KB)" % (path, os.path.getsize(path) // 1024))

    if not quiet:
        print("\n%d studies: %s" % (
            len(studies),
            ", ".join("App %s = %d" % (a, v["count"]) for a, v in meta["byApplication"].items()),
        ))
        for value in ACCESS_VALUES:
            print("  %-26s %d" % (value, counts.get(value, 0)))

    return studies, meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=DEFAULT_SRC, help="path to lit-scan-draft.xlsx")
    parser.add_argument("--out", default=OUT_DIR, help="output directory for the JSON")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    export(args.src, args.out, args.quiet)
