# -*- coding: utf-8 -*-
"""Export the scan's source list to JSON for the website.

data/sources.csv is the source of truth, one row per source. Nothing the site reads is
hand-edited - change the CSV, then run this script:

    python build/export_data.py

Writes data/studies.json and data/meta.json. data/quadrants.json is hand-authored
and is never touched by this script.

The CSV must be saved as UTF-8. In Excel that is "CSV UTF-8 (Comma delimited)"; the
plain "CSV (Comma delimited)" choice writes the computer's own code page, and the
export stops rather than guess at what it meant.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_SRC = os.path.join(REPO, "data", "sources.csv")
OUT_DIR = os.path.join(REPO, "data")
TOPICS_FILE = os.path.join(OUT_DIR, "topics.json")
INDICATORS_FILE = os.path.join(OUT_DIR, "indicators.json")
QUADRANTS_FILE = os.path.join(OUT_DIR, "quadrants.json")

# The source's id, as in study.html?id=app1-03. It never changes once a source is
# published, because links to it are already out there. The number after "app" is
# the topic the source was first filed under, the one after the dash its place there.
ID_RE = re.compile(r"^app(\d+)-(\d+)$")

# CSV column heading -> JSON key. Anything not listed here is ignored, so a new
# column in the CSV will not silently leak onto a public website.
FIELDS = {
    "ID": "id",
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
    "Topics": "topicsRaw",
    "Domain": "domainsRaw",
    "Indicators": "indicatorsRaw",
    "Application fee": "applicationFee",
    "Data source year(s)": "dataYears",
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

# Whether getting the data costs money on top of the application. Asked for by the
# partner agency in September 2026: they need to know what exists behind a paywall so
# they can make a case to pay for it. The same four strings as the contributor template
# in build/source_templates.py, so a contributor's spreadsheet and the site never
# disagree. "Not applicable" means there is no application to pay for.
APPLICATION_FEE_VALUES = [
    "Fee required",
    "No fee",
    "Not yet assessed",
    "Not applicable",
]

# Which fee values each access value allows. Public data and non-datasets have no
# application, so the fee can only be Not applicable; a source that needs an
# application cannot claim there is nothing to pay for or not. Aggregate-only sources
# go either way - some offer a microdata application behind the free tables, some do
# not - so every value is allowed there.
FEE_ALLOWED_BY_ACCESS = {
    "Public": ("Not applicable",),
    "Not a dataset": ("Not applicable",),
    "Restricted - application": ("Fee required", "No fee", "Not yet assessed"),
    "Public (aggregate only)": tuple(APPLICATION_FEE_VALUES),
    "Not yet assessed": ("Not yet assessed",),
}
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

# Which health issue a source speaks to. Not our vocabulary: these are the seven
# domains from the partner agency's own measures spreadsheet, kept in their wording
# and their order so a tag here means to them what it means here. Seven is few
# enough to stay in code; the 61 indicators underneath them are not, and belong in
# a data file if they are ever added.
#
# Blank is allowed and common. A source about how to build an index, or about
# hospital admissions, has no health-issue domain, and inventing one to fill the
# column would be worse than leaving it empty.
DOMAINS = [
    "Physical Activity",
    "Healthy Eating",
    "Implementation",
    "Social",
    "Equity",
    "Prosperity & Productivity",
    "Mental Wellbeing",
]

# Two derived facets. The CSV's own "Source type" and "Country/system" columns
# are prose and have around a dozen distinct values each, which makes a useless
# filter. These roll them into buckets people actually filter by. The original
# wording is still shown on the record.
SOURCE_GROUPS = [
    "Peer-reviewed",
    "Government report",
    "Published dataset",
    "International agency report",
    "Academic or institutional report",
]
REGIONS = ["Queensland", "Australia", "International"]

# First rule whose wording appears in the cell wins, so order matters: "Peer-reviewed,
# plus a government data tool" is peer-reviewed, not a government report.
#
# This used to end in a bare `return "Academic or institutional report"`, which meant
# any wording nobody had thought of - a blank cell included - was quietly filed as an
# academic report. The counts still added up and nothing flagged it. That is exactly
# the failure the Access and Geo tags columns are designed to prevent, so unmatched
# wording is now a data-entry error like any other.
SOURCE_GROUP_RULES = [
    ("Peer-reviewed", ("peer-reviewed", "peer reviewed", "journal article")),
    # Renamed from "Statistical agency release". Of the 15 rows it holds, two are not
    # statistical agencies at all: PHIDU is a university unit and Queensland Globe is a
    # mapping service. Both are still published datasets, which is what a reader wants
    # to know, so the bucket now says that instead of guessing at who published it.
    ("Published dataset", ("statistical agency", "statistician", "official statistics",
                           "statistics release", "survey data", "administrative data",
                           "census", "data release", "dataset", "spatial data")),
    ("Government report", ("government", "department of", "ministry", "public agency")),
    ("International agency report", ("international agency", "world health organization",
                                     "united nations", "oecd")),
    ("Academic or institutional report", ("academic", "institutional", "university",
                                          "think tank", "communique", "statement of principles",
                                          "collective")),
]

URL_RE = re.compile(r"https?://\S+")


def source_group(source_type):
    """The bucket this source is filtered under, or None if the wording matches no
    rule - the caller turns that into a data-entry error rather than a guess."""
    text = source_type.lower()
    for group, needles in SOURCE_GROUP_RULES:
        if any(needle in text for needle in needles):
            return group
    return None


def regions_of(country):
    """Australian and Queensland evidence is wanted first, so that is the split
    worth filtering on - not the fifteen different country strings.

    Returns None for a blank cell. It used to return International, which is a
    plausible-looking answer to a question nobody answered - an unfilled row would
    appear in the filters as overseas evidence."""
    text = country.lower().strip()
    if not text:
        return None
    tags = []
    if "queensland" in text:
        tags.append("Queensland")
    if "australia" in text:
        tags.append("Australia")
    if not tags or any(word in text for word in ("global", "countries", "oecd", "many")):
        tags.append("International")
    return tags


def publication_year(reference):
    """The year in the reference, for the newest/oldest sort on the browse page.

    Every reference that has a year writes it the same way, in brackets after the
    authors: "Masters R, ... (2017). Return on investment ...". So the bracketed form
    is the only one read. A bare four-digit number elsewhere in the string is usually
    part of a title or a survey name and would date the source wrongly.

    Returns None for the 20 rows with no year, which are the ongoing collections -
    the National Health Survey, PLIDA, the Social Health Atlas, AusPlay. They have no
    single publication year, and giving them one would be inventing it. The browse
    page sorts them to the bottom whichever direction is chosen."""
    match = re.search(r"\((\d{4})[a-z]?\)", reference)
    return int(match.group(1)) if match else None


def data_year(text):
    """The latest year the data covers, for the newest-data sort on the browse page.

    Reads the free-text "Data source year(s)" cell, whose convention is to state the span
    the data covers with the latest release year written out: "2017-18 to 2023-24",
    "2001 to 2023, annual", "2021 Census". A financial year written 2023-24 counts as
    2024. Returns None for a blank cell and for "Not applicable", which is what a review
    or guide says.

    A cell that says "ongoing" or "onwards" without a four-digit end year is refused by
    the caller rather than dated here: the export cannot know when an ongoing series was
    last released, and guessing the current year would quietly go stale."""
    text = (text or "").strip()
    if not text or text.lower() == "not applicable":
        return None
    latest = None
    for match in re.finditer(r"\b((?:19|20)\d{2})(?:[-/](\d{2}))?\b", text):
        year = int(match.group(1))
        if match.group(2):
            # "2023-24": the two-digit tail is the end of a financial year.
            tail = int(match.group(2))
            if tail == (year + 1) % 100:
                year += 1
        latest = year if latest is None else max(latest, year)
    return latest


def clean(value):
    """Collapse whitespace; treat blanks and None alike."""
    if value is None:
        return ""
    return " ".join(str(value).split())


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
    """The number in a topic's old sheet name, kept in topics.json so the ?app=1 links
    sent out before topics had names still resolve."""
    match = re.search(r"App\s*(\d+)", sheet_title)
    if not match:
        raise SystemExit("Sheet name does not carry a number: " + sheet_title)
    return int(match.group(1))


def load_topics():
    """The taxonomy lives in data/topics.json so a new topic is a data change, not a
    code change. See that file's own comment for the shape."""
    with open(TOPICS_FILE, encoding="utf-8") as handle:
        topics = json.load(handle)["topics"]

    seen = set()
    for topic in topics:
        for field in ("id", "title", "heading"):
            if not topic.get(field):
                raise SystemExit("topics.json: an entry is missing '%s'" % field)
        if topic["id"] in seen:
            raise SystemExit("topics.json: duplicate id '%s'" % topic["id"])
        seen.add(topic["id"])
    return topics


def load_indicators():
    """The 61 measures come from the partner agency and change when they revise them,
    so they live in data/indicators.json rather than in a list here. The seven domains
    stay in code above because they are few and stable.

    Returns {name, domain} in file order. Order matters: it is the agency's own ranking
    and the pages show them in it. The domain travels with the name because the browse
    filter groups all 61 under their seven domain headings - sixty-one flat checkboxes
    would drown the six filter groups below them."""
    with open(INDICATORS_FILE, encoding="utf-8") as handle:
        entries = json.load(handle)["indicators"]

    out, seen = [], set()
    for entry in entries:
        name = (entry.get("name") or "").strip()
        if not name:
            raise SystemExit("indicators.json: an entry has no 'name'")
        if name in seen:
            raise SystemExit("indicators.json: duplicate indicator '%s'" % name)
        domain = (entry.get("domain") or "").strip()
        if domain and domain not in DOMAINS:
            raise SystemExit("indicators.json: '%s' has domain '%s', which is not one of "
                             "the seven" % (name, domain))
        seen.add(name)
        out.append({"name": name, "domain": domain})
    return out


def gridded_topic_ids():
    """Which topics have a hand-authored quadrant grid. Lets the pages stop hardcoding
    that Application 1 is the only one built."""
    try:
        with open(QUADRANTS_FILE, encoding="utf-8") as handle:
            return set(json.load(handle).get("topics", {}))
    except (OSError, ValueError):
        return set()


def read_sources(src):
    """The CSV's rows as (row number, {heading: text}), blank rows left out. The row
    number is the one Excel shows, header row 1, so a message points at the right row
    even when a cell above holds several lines of text.

    Accepts the two things Excel may add: a byte-order mark, and a "sep=," first line
    that makes Excel split on commas whatever the computer's regional settings say.
    """
    try:
        with open(src, encoding="utf-8-sig", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError:
        raise SystemExit('%s is not saved as UTF-8. In Excel use Save As > '
                         '"CSV UTF-8 (Comma delimited)", then run this again.' % src)
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip().lower().startswith("sep="):
        lines = lines[1:]
    reader = csv.DictReader(lines)
    missing = [h for h in FIELDS if h not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit("%s is missing columns: %s" % (src, ", ".join(missing)))
    for number, row in enumerate(reader, start=2):
        if not any(isinstance(v, str) and v.strip() for v in row.values()):
            continue
        yield number, row


def export(src, out_dir, quiet=False):
    if not os.path.exists(src):
        raise SystemExit("Source list not found: " + src)

    topics = load_topics()
    indicators_meta = load_indicators()
    indicator_names = [i["name"] for i in indicators_meta]
    topic_by_title = {t["title"]: t["id"] for t in topics}

    studies, problems, seen_ids = [], [], set()

    for number, row in read_sources(src):
        record = {}
        for heading, key in FIELDS.items():
            record[key] = clean(row.get(heading))

        where = "Row %d (%s)" % (number, record["id"] or "no ID")
        match = ID_RE.match(record["id"])
        if not match:
            problems.append("%s: ID must look like app1-03" % where)
            continue
        if record["id"] in seen_ids:
            problems.append("%s: ID is used by an earlier row too" % where)
            continue
        seen_ids.add(record["id"])
        app, num = int(match.group(1)), int(match.group(2))

        # Only ID and Reference have to be filled in; a source can be added with what
        # is known and completed over time. A blank that the pages need an answer for
        # becomes "Not yet assessed", which is what it means. A filled cell must still
        # hold an allowed value, or the filters would grow an option nobody meant.
        access = record.get("access", "") or "Not yet assessed"
        record["access"] = access
        if access not in ACCESS_VALUES:
            problems.append("%s: Access is '%s', not one of the allowed values" % (where, access))

        tags = [t.strip() for t in record.pop("geoTagsRaw", "").split(";") if t.strip()]
        for tag in tags:
            if tag not in GEO_TAGS:
                problems.append("%s: geo tag '%s' is not in the vocabulary" % (where, tag))

        # Same split and check as the geo tags, without the "must have at least
        # one" rule - plenty of sources genuinely have no health-issue domain.
        domains = [d.strip() for d in record.pop("domainsRaw", "").split(";") if d.strip()]
        for domain in domains:
            if domain not in DOMAINS:
                problems.append("%s: domain '%s' is not in the vocabulary" % (where, domain))

        # Same again for the indicators, and blank is the usual answer here. A
        # source earns a tag only by naming or computing the measure; the agency
        # said they would fill in the rest, so gaps are the plan and not a lapse.
        indicators = [i.strip() for i in record.pop("indicatorsRaw", "").split(";") if i.strip()]
        for indicator in indicators:
            if indicator not in indicator_names:
                problems.append("%s: indicator '%s' is not in indicators.json"
                                % (where, indicator))

        # Every source names its topics. In the old workbook a blank cell meant
        # "this sheet's topic"; with one list there is no sheet to fall back on.
        named = [t.strip() for t in record.pop("topicsRaw", "").split(";") if t.strip()]
        topic_ids = []
        for name in named:
            if name not in topic_by_title:
                problems.append("%s: topic '%s' is not in topics.json" % (where, name))
            elif topic_by_title[name] not in topic_ids:
                topic_ids.append(topic_by_title[name])

        record["app"] = app
        record["num"] = num
        record["topics"] = topic_ids
        record["geoTags"] = tags
        record["domains"] = domains
        record["indicators"] = indicators
        group = source_group(record.get("sourceType", ""))
        if group is None and record.get("sourceType", ""):
            problems.append("%s: source type '%s' matches no rule in "
                            "SOURCE_GROUP_RULES - add a rule or fix the cell"
                            % (where, record.get("sourceType", "")))
        record["sourceGroup"] = group or ""

        # Access answers "can you get the data" and Source type answers "what kind
        # of thing is this", so a row can hold both without either being checked
        # against the other. These two values contradict each other, and app4-07
        # shipped reading "No dataset" and "Published dataset" side by side until
        # someone spotted it on the page.
        if access == "Not a dataset" and group == "Published dataset":
            problems.append("%s: Access says 'Not a dataset' but the Source type "
                            "'%s' files it under Published dataset - the row would "
                            "show both. Fix whichever is wrong"
                            % (where, record.get("sourceType", "")))

        regions = regions_of(record.get("country", ""))
        record["regions"] = regions or []
        record["dataLinks"] = parse_links(record.pop("dataLinkRaw", ""))

        record["year"] = publication_year(record["reference"])

        fee = record.get("applicationFee", "")
        if not fee:
            # Nothing to apply for, so nothing to pay; otherwise not known yet.
            fee = ("Not applicable" if access in ("Public", "Not a dataset")
                   else "Not yet assessed")
            record["applicationFee"] = fee
        if fee not in APPLICATION_FEE_VALUES:
            problems.append("%s: Application fee is '%s', not one of the allowed values"
                            % (where, fee))
        elif access in FEE_ALLOWED_BY_ACCESS and fee not in FEE_ALLOWED_BY_ACCESS[access]:
            problems.append("%s: Application fee '%s' does not go with Access '%s' "
                            "(allowed: %s)" % (where, fee, access,
                                               ", ".join(FEE_ALLOWED_BY_ACCESS[access])))

        years_text = record.get("dataYears", "")
        if not years_text:
            years_text = "Not applicable" if access == "Not a dataset" else "Not yet assessed"
            record["dataYears"] = years_text
        record["dataYear"] = data_year(years_text)
        if access == "Not a dataset":
            if years_text.lower() != "not applicable":
                problems.append("%s: Data source year(s) must be 'Not applicable' for a "
                                "source that is not a dataset, got '%s'" % (where, years_text))
        elif not years_text:
            problems.append("%s: Data source year(s) is blank - state the span the data "
                            "covers, or 'Not yet assessed'" % where)
        elif (record["dataYear"] is None
              and years_text.lower() != "not yet assessed"
              and not years_text.lower().startswith("varies by")):
            # "Varies by dataset" is the honest answer for a portal or atlas that
            # holds many collections; it sorts as undated rather than being given
            # one year that is wrong for most of what it holds.
            problems.append("%s: Data source year(s) '%s' has no four-digit year the "
                            "export can sort on" % (where, years_text))
        elif (re.search(r"\b(ongoing|onwards|to date|present)\b", years_text, re.I)
              and not re.search(r"\bto (19|20)\d{2}\b|\b(19|20)\d{2}\)?\s*$", years_text)):
            problems.append("%s: Data source year(s) '%s' says the series is ongoing "
                            "without stating the latest release year" % (where, years_text))

        if not record["reference"]:
            problems.append("%s: no reference" % where)

        studies.append(record)

    if problems:
        print("Export stopped - fix data/sources.csv first:", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        raise SystemExit(1)

    studies.sort(key=lambda s: (s["app"], s["num"]))

    counts = {}
    for study in studies:
        counts[study["access"]] = counts.get(study["access"], 0) + 1

    gridded = gridded_topic_ids()
    meta = {
        "generated": datetime.date.today().isoformat(),
        "source": os.path.basename(src),
        "total": len(studies),
        "topics": [
            {
                "id": t["id"],
                "title": t["title"],
                "heading": t["heading"],
                "question": t.get("question", ""),
                "blurb": t.get("blurb", ""),
                "count": sum(1 for s in studies if t["id"] in s["topics"]),
                "hasGrid": t["id"] in gridded,
                # The old ?app=1 links already sent out have to keep resolving.
                # Derived from the sheet name, the same way a study id is, so this
                # cannot drift out of step with it.
                "legacyApp": app_number(t["sheet"]) if t.get("sheet") else None,
            }
            for t in topics
        ],
        "byAccess": counts,
        "accessValues": ACCESS_VALUES,
        "geoTags": GEO_TAGS,
        "domains": DOMAINS,
        "indicators": indicators_meta,
        "sourceGroups": SOURCE_GROUPS,
        "applicationFeeValues": APPLICATION_FEE_VALUES,
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
            ", ".join("%s = %d" % (t["title"], t["count"]) for t in meta["topics"]),
        ))
        for value in ACCESS_VALUES:
            print("  %-26s %d" % (value, counts.get(value, 0)))
        dated = [s2["year"] for s2 in studies if s2["year"]]
        print()
        print("%d of %d sources carry a publication year (%d-%d); "
              "%d are ongoing collections with none"
              % (len(dated), len(studies), min(dated), max(dated),
                 len(studies) - len(dated)))
        with_data = [s2 for s2 in studies if s2["access"] != "Not a dataset"]
        dated_data = [s2["dataYear"] for s2 in with_data if s2["dataYear"]]
        print("%d of the %d sources with data carry a data year (%s); %d have no single year"
              % (len(dated_data), len(with_data),
                 "%d-%d" % (min(dated_data), max(dated_data)) if dated_data else "none",
                 len(with_data) - len(dated_data)))
        fees = {}
        for s2 in studies:
            fees[s2["applicationFee"]] = fees.get(s2["applicationFee"], 0) + 1
        print("application fee: " + ", ".join("%s = %d" % (v, fees.get(v, 0))
                                               for v in APPLICATION_FEE_VALUES))

    # Re-stamp the asset links here too, so the one command people actually run
    # leaves the site in a publishable state.
    if out_dir == OUT_DIR:
        import stamp_assets
        if not quiet:
            print("")
        stamp_assets.stamp(quiet)

    return studies, meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=DEFAULT_SRC, help="path to the sources CSV")
    parser.add_argument("--out", default=OUT_DIR, help="output directory for the JSON")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    export(args.src, args.out, args.quiet)

