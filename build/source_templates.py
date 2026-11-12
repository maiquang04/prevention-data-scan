#!/usr/bin/env python3
"""Build the two CSV templates for people who suggest changes to the scan.

One CSV is a deliberately lightweight form for suggesting new resources. The
second exports the current focus-area, health-focus and measure assignments for
human review, with a Notes column for corrections. Both are written to
templates/ (gitignored) from the site's own data, so they always offer the
current tag values. A returned file is read by a person and copied into
data/sources.csv; nothing here publishes anything.

Usage, from the repository root::

    python build/source_templates.py
    python build/source_templates.py --check
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
STUDIES_PATH = DATA_DIR / "studies.json"
META_PATH = DATA_DIR / "meta.json"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
CSV_PATH = TEMPLATES_DIR / "resource-entry-template.csv"
TAG_REVIEW_CSV_PATH = TEMPLATES_DIR / "current-source-tag-review.csv"
# Where the tag-review CSV links each source. Change it if the site moves.
WEBSITE_ROOT = "https://maiquang04.github.io/prevention-data-scan"

FREE_ENTRY_CSV_COLUMNS = {
    "number": "No.",
    "notes": "Free entry - Notes",
    "reference": "Free entry - Reference",
    "original_link": "Free entry - Original source link",
    "purpose": "Free entry - Purpose",
    "input": "Free entry - Input",
    "output": "Free entry - Output",
    "underlying_data": "Free entry - Underlying data",
    "data_source_years": "Free entry - Data source year(s)",
    "source_links": "Free entry - Source links",
    "scale": "Free entry - Scale",
    "key_details": "Free entry - Key details",
}


APPLICATION_FEE_VALUES = [
    "Fee required",
    "No fee",
    "Not yet assessed",
    "Not applicable",
]


BRIEF_EXAMPLE_VALUES = {
    "app6-10": {
        "reference": "Queensland Preventive Health Survey and QSAS",
        "purpose": "Monitor preventive health indicators",
        "input": "Telephone survey responses",
        "output": "Regional percentages and spreadsheets",
        "underlying_data": "Queensland Preventive Health Survey",
        "data_source_years": "Ongoing series",
        "source_links": (
            "QSAS viewer: https://public.tableau.com/app/profile/"
            "population.epidemiology/viz/QSAS_Regional_By_Region/Adult"
        ),
        "scale": "Statewide; two years pooled",
        "key_details": "Age; sex; region; sociodemographic group",
    },
    "app1-11": {
        "reference": "Queensland Joint Regional Needs Assessments, 2025-2027",
        "purpose": "Identify regional health needs and service gaps",
        "input": "Population, hospital and consultation data",
        "output": "Regional needs assessment",
        "underlying_data": "Census; QPHS; AIHW; Medicare; Queensland Health",
        "data_source_years": "Census 2021; other sources vary",
        "source_links": "",
        "scale": "One report per PHN",
        "key_details": "Regional priorities and data sources",
    },
}


TAG_REVIEW_CSV_COLUMNS = [
    "Review number",
    "Notes",
    "Resource title or citation",
    "Website record",
    "Original source link",
    "Current focus area(s)",
    "Current health focus(es)",
    "Current measure(s) or indicator(s)",
    # Added September 2026 alongside the site's own two new columns, so a
    # reviewer checking tags can check these two in the same pass.
    "Current application fee",
    "Current data source year(s)",
]


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def joined(values: Iterable[str] | None) -> str:
    return "; ".join(value for value in (values or []) if value)


def load_source_data() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    studies = read_json(STUDIES_PATH)
    meta = read_json(META_PATH)
    if not isinstance(studies, list):
        raise ValueError(f"{STUDIES_PATH} must contain a list")
    if len(studies) < 61:
        # The pack has shipped with 61 sources since it was first built. A count
        # below that is the sign of a stale or partial data/studies.json, not of the
        # scan having grown - which it since has (66 as of September 2026, when five
        # longitudinal datasets were added).
        raise ValueError(f"expected at least 61 website sources, found {len(studies)}")

    ids = [study.get("id") for study in studies]
    if any(not source_id for source_id in ids):
        raise ValueError("every website source must have an id")
    if len(set(ids)) != len(ids):
        raise ValueError("website source ids are not unique")
    if meta.get("total") != len(studies):
        raise ValueError(
            f"meta.json reports {meta.get('total')} sources but studies.json has {len(studies)}"
        )

    topic_ids = {topic["id"] for topic in meta["topics"]}
    indicator_names = {item["name"] for item in meta["indicators"]}
    controlled = {
        "topics": topic_ids,
        "sourceGroup": set(meta["sourceGroups"]),
        "regions": set(meta["regions"]),
        "geoTags": set(meta["geoTags"]),
        "domains": set(meta["domains"]),
        "indicators": indicator_names,
        "access": set(meta["accessValues"]),
    }
    for study in studies:
        source_id = study["id"]
        for key in ("topics", "regions", "geoTags", "domains", "indicators"):
            unknown = set(study.get(key, [])) - controlled[key]
            if unknown:
                raise ValueError(f"{source_id}: unknown {key}: {sorted(unknown)}")
        for key in ("sourceGroup", "access"):
            value = study.get(key)
            if value not in controlled[key]:
                raise ValueError(f"{source_id}: unknown {key}: {value!r}")

    return studies, meta


def topic_titles(meta: dict[str, Any]) -> dict[str, str]:
    return {topic["id"]: topic["title"] for topic in meta["topics"]}


def resource_entry_tag_specs(
    meta: dict[str, Any],
) -> list[tuple[str, str, list[str], bool]]:
    """Return website tag groups plus the two columns added in September 2026.

    The final bool records whether several values may be entered. The visible
    headings are generated from these values so the CSV cannot drift away from
    the controlled vocabulary.
    """
    specs = [
        (
            "topics",
            "Related focus areas",
            [topic["title"] for topic in meta["topics"]],
            True,
        ),
        ("source_group", "Kind of source", list(meta["sourceGroups"]), False),
        ("geo_tags", "Geography tags", list(meta["geoTags"]), True),
        ("domains", "Health focus", list(meta["domains"]), True),
    ]
    for domain in meta["domains"]:
        specs.append(
            (
                f"indicators:{domain}",
                f"{domain} measures and indicators",
                [
                    item["name"]
                    for item in meta["indicators"]
                    if item["domain"] == domain
                ],
                True,
            )
        )
    specs.extend(
        [
            ("access", "Access", list(meta["accessValues"]), False),
            ("application_fee", "Application fee", APPLICATION_FEE_VALUES, False),
            ("regions", "Region", list(meta["regions"]), True),
        ]
    )
    return specs


def tag_column_heading(label: str, values: list[str], multiple: bool) -> str:
    heading = f"Tag - {label} (available: {' | '.join(values)}"
    if multiple:
        heading += "; separate multiple tags with semicolons"
    return heading + ")"


def resource_entry_tag_columns(meta: dict[str, Any]) -> dict[str, str]:
    return {
        key: tag_column_heading(label, values, multiple)
        for key, label, values, multiple in resource_entry_tag_specs(meta)
    }


def resource_entry_columns(meta: dict[str, Any]) -> list[str]:
    return list(FREE_ENTRY_CSV_COLUMNS.values()) + list(
        resource_entry_tag_columns(meta).values()
    )


def csv_example(
    number: int, study: dict[str, Any], meta: dict[str, Any]
) -> dict[str, str]:
    titles = topic_titles(meta)
    free = FREE_ENTRY_CSV_COLUMNS
    tags = resource_entry_tag_columns(meta)
    example = BRIEF_EXAMPLE_VALUES[study["id"]]
    row = {column: "" for column in resource_entry_columns(meta)}
    row.update(
        {
            free["number"]: str(number),
            free["reference"]: example["reference"],
            free["original_link"]: study.get("link", ""),
            free["purpose"]: example["purpose"],
            free["input"]: example["input"],
            free["output"]: example["output"],
            free["underlying_data"]: example["underlying_data"],
            free["data_source_years"]: example["data_source_years"],
            free["source_links"]: example["source_links"],
            free["scale"]: example["scale"],
            free["key_details"]: example["key_details"],
            free["notes"]: "Example",
            tags["topics"]: joined(
                titles[topic] for topic in study.get("topics", [])
            ),
            tags["source_group"]: study.get("sourceGroup", ""),
            tags["geo_tags"]: joined(study.get("geoTags", [])),
            tags["domains"]: joined(study.get("domains", [])),
            tags["access"]: study.get("access", ""),
            tags["application_fee"]: (
                "Not yet assessed"
                if study.get("access") == "Restricted - application"
                else "Not applicable"
            ),
            tags["regions"]: joined(study.get("regions", [])),
        }
    )
    indicator_domains = {
        item["name"]: item["domain"] for item in meta["indicators"]
    }
    for domain in meta["domains"]:
        row[tags[f"indicators:{domain}"]] = joined(
            indicator
            for indicator in study.get("indicators", [])
            if indicator_domains[indicator] == domain
        )
    return row


def csv_rows(studies: list[dict[str, Any]], meta: dict[str, Any]) -> list[dict[str, str]]:
    by_id = {study["id"]: study for study in studies}
    examples = [
        csv_example(1, by_id["app6-10"], meta),
        csv_example(2, by_id["app1-11"], meta),
    ]
    blank_rows = []
    for number in range(3, 8):
        row = {column: "" for column in resource_entry_columns(meta)}
        row[FREE_ENTRY_CSV_COLUMNS["number"]] = str(number)
        blank_rows.append(row)
    return examples + blank_rows


def build_csv(studies: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    write_excel_csv(CSV_PATH, resource_entry_columns(meta), csv_rows(studies, meta))


def write_excel_csv(
    path: Path, columns: list[str], rows: Iterable[dict[str, str]]
) -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        # Excel otherwise uses the machine's regional list separator when a CSV
        # is opened by double-clicking it. This keeps comma-separated data in
        # separate columns even where desktop Excel expects semicolons.
        handle.write("sep=,\r\n")
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)


def tag_review_csv_row(
    review_number: int, study: dict[str, Any], meta: dict[str, Any]
) -> dict[str, str]:
    titles = topic_titles(meta)
    return {
        "Review number": str(review_number),
        "Notes": "",
        "Resource title or citation": study.get("reference", ""),
        "Website record": f"{WEBSITE_ROOT}/study.html?id={study['id']}",
        "Original source link": study.get("link", ""),
        "Current focus area(s)": joined(
            titles[topic] for topic in study.get("topics", [])
        ),
        "Current health focus(es)": joined(study.get("domains", [])),
        "Current measure(s) or indicator(s)": joined(study.get("indicators", [])),
        "Current application fee": study.get("applicationFee", ""),
        "Current data source year(s)": study.get("dataYears", ""),
    }


def tag_review_csv_rows(
    studies: list[dict[str, Any]], meta: dict[str, Any]
) -> list[dict[str, str]]:
    return [
        tag_review_csv_row(review_number, study, meta)
        for review_number, study in enumerate(studies, start=1)
    ]


def build_tag_review_csv(
    studies: list[dict[str, Any]], meta: dict[str, Any]
) -> None:
    write_excel_csv(
        TAG_REVIEW_CSV_PATH,
        TAG_REVIEW_CSV_COLUMNS,
        tag_review_csv_rows(studies, meta),
    )


def build_pack(studies: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    build_csv(studies, meta)
    build_tag_review_csv(studies, meta)
    print(f"wrote {CSV_PATH.relative_to(PROJECT_ROOT)}")
    print(
        f"  {len(resource_entry_columns(meta))} columns, "
        "2 examples, 5 numbered new-resource rows"
    )
    print(f"wrote {TAG_REVIEW_CSV_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  {len(studies)} current-source rows, {len(TAG_REVIEW_CSV_COLUMNS)} columns")


def read_checked_csv(path: Path, expected_columns: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise AssertionError(f"missing {path.relative_to(PROJECT_ROOT)}")
    raw = path.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise AssertionError(f"{path.name} is missing its UTF-8 byte-order mark")
    without_crlf = raw.replace(b"\r\n", b"")
    if b"\n" in without_crlf or b"\r" in without_crlf:
        raise AssertionError(f"{path.name} contains a line ending other than CRLF")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        separator_directive = handle.readline().rstrip("\r\n")
        if separator_directive != "sep=,":
            raise AssertionError(
                f"{path.name} is missing the Excel comma-separator directive"
            )
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_columns:
            raise AssertionError(f"{path.name} columns do not match the approved schema")
        rows = list(reader)
    return rows


def check_csv(studies: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    columns = resource_entry_columns(meta)
    rows = read_checked_csv(CSV_PATH, columns)
    if len(rows) != 7:
        raise AssertionError(f"CSV has {len(rows)} data rows, expected 7")
    number_column = FREE_ENTRY_CSV_COLUMNS["number"]
    notes_column = FREE_ENTRY_CSV_COLUMNS["notes"]
    if [row[number_column] for row in rows] != [str(number) for number in range(1, 8)]:
        raise AssertionError("resource-entry numbers must run from 1 to 7")
    if [row[notes_column] for row in rows[:2]] != ["Example", "Example"]:
        raise AssertionError("the first two CSV rows are not clearly marked examples")
    if any(
        value
        for row in rows[2:]
        for column, value in row.items()
        if column != number_column
    ):
        raise AssertionError("the CSV does not contain five empty resource-entry rows")
    if rows != csv_rows(studies, meta):
        raise AssertionError("CSV content does not match the current template definition")

    free_count = len(FREE_ENTRY_CSV_COLUMNS)
    if columns[0] != number_column:
        raise AssertionError("resource-entry No. must be the first column")
    if columns[1] != notes_column:
        raise AssertionError("resource-entry Notes must be the second column")
    if not all(
        column.startswith("Free entry - ") for column in columns[1:free_count]
    ):
        raise AssertionError("free-entry columns are not grouped and labelled clearly")
    duplicated_free_entry_columns = {
        "Free entry - Specific source type",
        "Free entry - Country or system",
        "Free entry - Measures",
        "Free entry - Geography",
        "Free entry - Access details",
    }
    if duplicated_free_entry_columns.intersection(columns):
        raise AssertionError("a free-entry field duplicates a controlled tag column")
    data_years_column = FREE_ENTRY_CSV_COLUMNS["data_source_years"]
    underlying_data_column = FREE_ENTRY_CSV_COLUMNS["underlying_data"]
    if columns.index(data_years_column) != columns.index(underlying_data_column) + 1:
        raise AssertionError("Data source year(s) must follow Underlying data")
    if not all(column.startswith("Tag - ") for column in columns[free_count:]):
        raise AssertionError("tag columns are not grouped and labelled clearly")

    tag_columns = resource_entry_tag_columns(meta)
    access_column = tag_columns["access"]
    application_fee_column = tag_columns["application_fee"]
    if columns.index(application_fee_column) != columns.index(access_column) + 1:
        raise AssertionError("Application fee must follow Access")
    if [row[application_fee_column] for row in rows[:2]] != ["Not applicable"] * 2:
        raise AssertionError("example application-fee tags are inconsistent with Access")

    link_columns = {
        FREE_ENTRY_CSV_COLUMNS["original_link"],
        FREE_ENTRY_CSV_COLUMNS["source_links"],
    }
    for row in rows[:2]:
        for column in columns[:free_count]:
            if column not in link_columns and len(row[column]) > 80:
                raise AssertionError(f"example text is too long in {column}")
    for _key, label, values, multiple in resource_entry_tag_specs(meta):
        expected_heading = tag_column_heading(label, values, multiple)
        if expected_heading not in columns:
            raise AssertionError(f"missing current options in the {label} tag heading")

    indicator_specs = [
        spec for spec in resource_entry_tag_specs(meta) if spec[0].startswith("indicators:")
    ]
    indicator_domains = [
        spec[0].removeprefix("indicators:") for spec in indicator_specs
    ]
    if indicator_domains != meta["domains"]:
        raise AssertionError("measure and indicator columns do not match the health focuses")
    indicator_options = [value for spec in indicator_specs for value in spec[2]]
    expected_indicators = [item["name"] for item in meta["indicators"]]
    if (
        len(indicator_options) != len(expected_indicators)
        or len(set(indicator_options)) != len(indicator_options)
        or set(indicator_options) != set(expected_indicators)
    ):
        raise AssertionError("measure and indicator options are missing or duplicated")


def check_tag_review_csv(
    studies: list[dict[str, Any]], meta: dict[str, Any]
) -> None:
    rows = read_checked_csv(TAG_REVIEW_CSV_PATH, TAG_REVIEW_CSV_COLUMNS)
    if TAG_REVIEW_CSV_COLUMNS[1] != "Notes":
        raise AssertionError("tag-review Notes must be the second column")
    expected_rows = tag_review_csv_rows(studies, meta)
    if rows != expected_rows:
        raise AssertionError("tag-review CSV does not match the current website metadata")
    review_numbers = [row["Review number"] for row in rows]
    if review_numbers != [str(number) for number in range(1, len(studies) + 1)]:
        raise AssertionError(
            f"tag-review CSV review numbers must run from 1 to {len(studies)}")
    if any(row["Notes"] for row in rows):
        raise AssertionError("tag-review CSV Notes cells must be blank when generated")


def check_pack(studies: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    check_csv(studies, meta)
    check_tag_review_csv(studies, meta)
    print("source_templates: clean")
    print(
        "  resource-entry CSV: non-duplicative free-entry fields followed by tag fields; "
        "7 health-focus measure columns; all current options in headings; "
        "rows numbered 1-7; 2 examples; 5 empty entry rows"
    )
    print(
        f"  tag-review CSV: {len(TAG_REVIEW_CSV_COLUMNS)} focused columns; "
        f"neutral review numbers 1-{len(studies)}; "
        "Notes second and blank; "
        "Excel comma directive; UTF-8 BOM; CRLF"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="check the existing templates without rewriting them",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        studies, meta = load_source_data()
        if args.check:
            check_pack(studies, meta)
        else:
            build_pack(studies, meta)
    except (AssertionError, KeyError, OSError, ValueError) as error:
        print(f"source_templates: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
