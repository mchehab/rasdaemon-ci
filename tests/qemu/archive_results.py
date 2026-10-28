#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Mauro Carvalho Chehab <mchehab+huawei@kernel.org>

"""Retain selected CI run evidence and prepare its static Pages archive."""

import argparse
import datetime
import difflib
import html
import json
import os
import re
import shutil
import sys
import urllib.parse

import features
from publish_results import PublishedResult, ResultSite


RUN_ID = re.compile(r"^[0-9]+$")
ARCHIVE_NAME = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9]+$")
# Historical result files include this synthesized coverage roll-up as a test.
# The feature table already reports it; listing it as a test failure duplicates
# the underlying failed or skipped checks and has no separate evidence.
AGGREGATE_FAILURES = {"feature-coverage"}


def read_run(path: str) -> dict:
    """Read the run URL, conclusion, timestamp and id saved by the publisher."""
    with open(path, encoding="utf-8") as stream:
        fields = stream.read().strip().split("\t")

    if len(fields) != 4 or not RUN_ID.fullmatch(fields[3]):
        raise ValueError(f"invalid run metadata: {path}")

    created = datetime.datetime.fromisoformat(fields[2].replace("Z", "+00:00"))
    created = created.astimezone(datetime.timezone.utc)
    timestamp = created.strftime("%Y%m%dT%H%M%SZ")
    if fields[1] not in ("success", "failure", "cancelled", "timed_out",
                         "action_required", "startup_failure", "stale"):
        raise ValueError(f"invalid run conclusion: {fields[1]}")

    return {"url": fields[0], "conclusion": fields[1],
            "created_at": created.isoformat(), "timestamp": timestamp,
            "run_id": fields[3]}


def page_for_run(destination: str, metadata: dict, kind: str) -> None:
    """Create a browsable report page linking every retained evidence file."""
    links = []
    for root, directories, filenames in os.walk(destination):
        directories.sort()
        for filename in sorted(filenames):
            path = os.path.join(root, filename)
            relative = os.path.relpath(path, destination).replace(os.sep, "/")
            label = html.escape(relative)
            href = html.escape(relative, quote=True)
            links.append(f'<li><a href="{href}">{label}</a></li>')

    title = html.escape(f"{kind} CI evidence: {metadata['timestamp']}")
    url = html.escape(metadata["url"], quote=True)
    status = html.escape(metadata["conclusion"])
    page = ["<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"]
    page.append(f"<title>{title}</title><h1>{title}</h1>")
    page.append(f"<p>Run result: {status}. <a href=\"{url}\">GitHub Actions run</a></p>")
    page.append("<h2>Evidence files</h2><ul>" + "".join(links) + "</ul>")
    with open(os.path.join(destination, "index.html"), "w", encoding="utf-8") as stream:
        stream.write("\n".join(page) + "\n")


def publish_archived_report(store_run: str, site_run: str,
                            metadata: dict, kind: str) -> None:
    """Render the archived report with the same combined view as latest."""
    result_root = os.path.join(store_run, kind)
    results = []
    for label in sorted(os.listdir(result_root)):
        directory = os.path.join(result_root, label)
        if not os.path.isdir(directory) or not os.path.isfile(
                os.path.join(directory, "result.json")):
            continue
        results.append(PublishedResult(label, directory))

    report_dir = os.path.join(site_run, kind)
    title = f"{kind.capitalize()} results — {metadata['timestamp']}"
    report = ResultSite(title, report_dir, metadata["url"],
                        "https://github.com/mchehab/rasdaemon",
                        metadata["conclusion"], results)
    report.write()

    # ResultSite is shared with the live page; archived pages should identify
    # the run they represent rather than call it the latest result.
    index = os.path.join(report_dir, "index.html")
    with open(index, encoding="utf-8") as stream:
        page = stream.read()
    page = page.replace("Latest published result.",
                        f"Results for {html.escape(metadata['timestamp'])}.")
    with open(index, "w", encoding="utf-8") as stream:
        stream.write(page)


def evidence_html(value: object, name: str = "") -> str:
    """Render nested failure evidence as labeled fields and readable text."""
    if isinstance(value, dict):
        if not value:
            return "<em>No evidence was recorded.</em>"
        fields = []
        for key, item in value.items():
            label = html.escape(str(key).replace("_", " ").capitalize())
            fields.append(f"<dt>{label}</dt><dd>{evidence_html(item, str(key))}</dd>")
        return '<dl class="evidence-fields">' + "".join(fields) + "</dl>"

    if isinstance(value, (list, tuple)):
        if not value:
            return "<em>None</em>"
        return '<ol class="evidence-list">' + "".join(
            f"<li>{evidence_html(item, name)}</li>" for item in value) + "</ol>"

    if value is None:
        return "<em>Not available</em>"
    if isinstance(value, bool):
        return "Yes" if value else "No"

    text = str(value)
    if name in ("output", "stdout", "response") and text:
        try:
            decoded = json.loads(text)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            return evidence_html(decoded)
        return "<pre class=\"evidence-text\">" + html.escape(text) + "</pre>"
    if "\n" in text:
        return "<pre class=\"evidence-text\">" + html.escape(text) + "</pre>"
    return html.escape(text) or "<em>Empty</em>"


def evidence_summary_lines(evidence: dict) -> list[str]:
    """Format evidence into concise, labeled lines suitable for a diff."""
    if "expected_warning" in evidence:
        lines = [
            f"Threshold: {evidence.get('threshold', 'unknown')}",
            f"Expected warning: {evidence.get('expected_warning', '')}",
            f"Warning observed: {'Yes' if evidence.get('warning_observed') else 'No'}",
            f"Hardware corrupted: {evidence.get('hardware_corrupted', 'not recorded')}",
            f"Kernel event observed: {'Yes' if evidence.get('kernel_event_observed') else 'No'}",
        ]
        output = str(evidence.get("rasdaemon_output", ""))
        relevant = [line.strip() for line in output.splitlines()
                    if evidence.get("expected_warning", "") in line or
                    "memory_failure_event" in line]
        if relevant:
            lines.append("Relevant rasdaemon log lines:")
            lines.extend("  " + line for line in relevant)
        trace = str(evidence.get("kernel_trace", ""))
        relevant_trace = [line.strip() for line in trace.splitlines()
                          if "memory_failure_event" in line]
        if relevant_trace:
            lines.append("Relevant kernel trace lines:")
            lines.extend("  " + line for line in relevant_trace)
        return lines

    lines = []

    def append(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                append(value[key], f"{path}.{key}" if path else str(key))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                append(item, f"{path}[{index}]")
        elif isinstance(value, str) and "\n" in value:
            lines.append(f"{path}:")
            lines.extend("  " + line for line in value.splitlines()[:80])
            if len(value.splitlines()) > 80:
                lines.append("  … output truncated in comparison; full evidence is above")
        else:
            lines.append(f"{path}: {value}")

    append(evidence, "")
    return lines


def passing_comparison(store: str, retained: list[tuple[str, dict]],
                      run_name: str, metadata: dict, kind: str, label: str,
                      test_name: str) -> dict | None:
    """Find the closest retained passing instance of the same check."""
    failed_at = datetime.datetime.fromisoformat(metadata["created_at"])
    candidates = []
    for candidate_run, candidate_metadata in retained:
        if candidate_run == run_name or candidate_metadata["kind"] != kind:
            continue
        result_path = os.path.join(store, candidate_run, kind, label, "result.json")
        if not os.path.isfile(result_path):
            continue
        with open(result_path, encoding="utf-8") as stream:
            result = json.load(stream)
        passed = next((test for test in result.get("tests", [])
                       if test.get("name") == test_name and
                       test.get("status") == "passed"), None)
        if passed is None:
            continue
        created = datetime.datetime.fromisoformat(candidate_metadata["created_at"])
        # Prefer the nearest prior pass; if none exists, use the nearest later pass.
        candidates.append((created >= failed_at, abs((failed_at - created).total_seconds()),
                           candidate_run, candidate_metadata, passed))

    if not candidates:
        return None
    _, _, candidate_run, candidate_metadata, passed = min(candidates)
    return {"run_name": candidate_run, "timestamp": candidate_metadata["timestamp"],
            "url": candidate_metadata["url"], "reason": passed.get("reason", ""),
            "evidence": passed.get("evidence") or {}}


def failure_page(destination: str, metadata: dict, kind: str, label: str,
                 name: str, reason: str, evidence: dict, run_name: str,
                 comparison: dict | None = None) -> str:
    """Write a focused detail page for one failed check."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "failure"
    directory = os.path.join(destination, run_name, kind, label)
    os.makedirs(directory, exist_ok=True)
    filename = f"{slug}.html"
    title = html.escape(name)
    escaped_reason = html.escape(reason or "No failure reason was recorded.")
    rendered_evidence = evidence_html(evidence)
    run_url = html.escape(metadata["url"], quote=True)
    page = ["<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"]
    page.append("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    page.append("<style>body{font:16px system-ui,sans-serif;line-height:1.5;"
                "max-width:80rem;margin:2rem auto;padding:0 1rem}"
                "a{color:#0969da}.evidence-fields{display:grid;"
                "grid-template-columns:minmax(10rem,auto) 1fr;gap:.4rem 1rem}"
                ".evidence-fields dt{font-weight:600;color:#59636e}"
                ".evidence-fields dd{margin:0;min-width:0}"
                ".evidence-text{background:#f6f8fa;border:1px solid #d0d7de;"
                "border-radius:.25rem;padding:.8rem;max-height:34rem;overflow:auto;"
                "white-space:pre-wrap;overflow-wrap:anywhere}"
                "code{overflow-wrap:anywhere}</style>")
    page.append(f"<title>Failed test: {title}</title><h1>Failed test: {title}</h1>")
    page.append(f"<p>{html.escape(metadata['timestamp'])} · {html.escape(kind)} · "
                f"{html.escape(label)}</p>")
    page.append(f"<p><strong>Reason:</strong> {escaped_reason}</p>")
    page.append(f"<p><a href=\"{run_url}\">GitHub Actions run</a> · "
                f"<a href=\"../../../../{run_name}/\">All run evidence</a></p>")
    page.append(f"<h2>Failure evidence</h2>{rendered_evidence}")
    page.append("<h2>Comparison with a passing run</h2>")
    if comparison:
        comparison_run = html.escape(comparison["run_name"], quote=True)
        comparison_url = html.escape(comparison["url"], quote=True)
        page.append(f"<p>Passing result from {html.escape(comparison['timestamp'])}: "
                    f"<a href=\"../../../../{comparison_run}/{kind}/{label}/\">"
                    "open the successful report</a> · "
                    f"<a href=\"{comparison_url}\">GitHub Actions run</a></p>")
        if comparison.get("reason"):
            page.append(f"<p><strong>Passing check:</strong> "
                        f"{html.escape(comparison['reason'])}</p>")
        diff = difflib.unified_diff(
            evidence_summary_lines(comparison.get("evidence") or {}),
            evidence_summary_lines(evidence),
            fromfile=f"passing {comparison['timestamp']}",
            tofile=f"failed {metadata['timestamp']}", lineterm="")
        page.append("<h3>Evidence difference (passing → failed)</h3><pre class=\"evidence-text\">" +
                    html.escape("\n".join(diff) or "No evidence differences were recorded.") +
                    "</pre>")
    else:
        page.append("<p>No retained passing result for this check and architecture "
                    "was found.</p>")
    with open(os.path.join(directory, filename), "w", encoding="utf-8") as stream:
        stream.write("\n".join(page) + "\n")

    relative = os.path.relpath(os.path.join(directory, filename), destination)
    return urllib.parse.quote(relative.replace(os.sep, "/"), safe="/-_.")


def collect_failures(store: str, retained: list[tuple[str, dict]],
                     destination: str) -> list[dict]:
    """Create one focused detail page and index row per failed test."""
    failures = []
    for run_name, metadata in retained:
        kind = metadata["kind"]
        result_root = os.path.join(store, run_name, kind)
        found_failed_test = False
        for root, directories, filenames in os.walk(result_root):
            directories.sort()
            if "result.json" not in filenames:
                continue
            result_path = os.path.join(root, "result.json")
            with open(result_path, encoding="utf-8") as stream:
                result = json.load(stream)
            label = os.path.relpath(root, result_root).split(os.sep, 1)[0]
            if label == ".":
                label = kind

            for test in result.get("tests", []):
                if test.get("status") != "failed":
                    continue
                if test.get("name") in AGGREGATE_FAILURES:
                    found_failed_test = True
                    continue
                found_failed_test = True
                test_name = str(test.get("name") or "unnamed test")
                reason = str(test.get("reason") or "")
                evidence = test.get("evidence") or {}
                comparison = passing_comparison(store, retained, run_name, metadata,
                                                kind, label, test_name)
                link = failure_page(destination, metadata, kind, label,
                                    test_name, reason, evidence, run_name, comparison)
                report_path = os.path.relpath(
                    os.path.join(root, "results.html"), store)
                report_url = "../" + urllib.parse.quote(
                    report_path.replace(os.sep, "/"), safe="/-_.")
                failures.append({"timestamp": metadata["timestamp"],
                                 "kind": kind, "label": label,
                                 "name": test_name, "reason": reason,
                                 "url": link, "report_url": report_url,
                                 "run_name": run_name})

            infrastructure = result.get("infrastructure_failure")
            if infrastructure and not any(t.get("status") == "failed"
                                          for t in result.get("tests", [])):
                name = "infrastructure"
                reason = str(infrastructure)
                link = failure_page(destination, metadata, kind, label, name,
                                    reason, {"infrastructure_failure": reason}, run_name)
                failures.append({"timestamp": metadata["timestamp"],
                                 "kind": kind, "label": label, "name": name,
                                 "reason": reason, "url": link,
                                 "report_url": "../" + run_name + "/",
                                 "run_name": run_name})

        if metadata["conclusion"] != "success" and not found_failed_test:
            reason = f"Workflow conclusion: {metadata['conclusion']}"
            link = failure_page(destination, metadata, kind, kind, "workflow",
                                reason, {"conclusion": metadata["conclusion"]}, run_name)
            failures.append({"timestamp": metadata["timestamp"],
                             "kind": kind, "label": kind, "name": "workflow",
                             "reason": reason, "url": link,
                             "report_url": "../" + run_name + "/",
                             "run_name": run_name})

    failures.sort(key=lambda item: (item["timestamp"], item["kind"],
                                    item["label"], item["name"]), reverse=True)
    return failures


def archive(store: str, site: str, inputs: list[str], now: datetime.datetime) -> None:
    """Copy input runs into durable storage and render retained runs for Pages."""
    os.makedirs(store, exist_ok=True)
    os.makedirs(site, exist_ok=True)
    cutoff = now - datetime.timedelta(days=30)

    for item in inputs:
        kind, separator, result_dir = item.partition("=")
        if not separator or kind not in ("daily", "weekly"):
            raise ValueError(f"invalid evidence input: {item}")

        run_path = os.path.join(result_dir, "run")
        if not os.path.isfile(run_path):
            continue

        metadata = read_run(run_path)
        name = f"{metadata['timestamp']}-{metadata['run_id']}"
        if not ARCHIVE_NAME.fullmatch(name):
            raise ValueError(f"invalid archive directory: {name}")
        destination = os.path.join(store, name)
        os.makedirs(destination, exist_ok=True)
        metadata_path = os.path.join(destination, f"{kind}.json")
        with open(metadata_path, "w", encoding="utf-8") as stream:
            json.dump({**metadata, "kind": kind}, stream, indent=2)
            stream.write("\n")

        result_destination = os.path.join(destination, kind)
        shutil.rmtree(result_destination, ignore_errors=True)
        shutil.copytree(result_dir, result_destination)
        page_for_run(destination, metadata, kind)

    retained = []
    for name in sorted(os.listdir(store), reverse=True):
        if not ARCHIVE_NAME.fullmatch(name):
            continue
        directory = os.path.join(store, name)
        metadata_files = [os.path.join(directory, filename)
                          for filename in ("daily.json", "weekly.json")]
        metadata = []
        for path in metadata_files:
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as stream:
                    metadata.append(json.load(stream))

        if not metadata:
            continue

        newest = max(datetime.datetime.fromisoformat(item["created_at"])
                     for item in metadata)
        if all(item["conclusion"] == "success" for item in metadata) and newest < cutoff:
            shutil.rmtree(directory)
            continue

        for item in metadata:
            retained.append((name, item))

    archive_site = os.path.join(site, "archive")
    shutil.rmtree(archive_site, ignore_errors=True)
    os.makedirs(archive_site)
    for name, metadata in retained:
        site_run = os.path.join(site, name)
        os.makedirs(site_run, exist_ok=True)
        store_run = os.path.join(store, name)
        for kind in ("daily", "weekly"):
            metadata_path = os.path.join(store_run, f"{kind}.json")
            if not os.path.isfile(metadata_path):
                continue
            with open(metadata_path, encoding="utf-8") as stream:
                run_metadata = json.load(stream)
            publish_archived_report(store_run, site_run, run_metadata, kind)
        page_for_run(site_run, metadata, metadata["kind"])

    failures_site = os.path.join(site, "failures")
    shutil.rmtree(failures_site, ignore_errors=True)
    os.makedirs(failures_site)
    failures = collect_failures(store, retained, failures_site)

    # Link failed checks in both the live summary and that run's archived page.
    # Passed checks remain plain text, so links themselves signal failures.
    for failure in failures:
        name = html.escape(failure["name"])
        label = html.escape(failure["label"])
        row_prefix = f"<tr><td>{label}</td><td>{name}</td>"
        targets = [(os.path.join(site, failure["kind"], "index.html"),
                    "../failures/" + failure["url"])]
        targets.append((os.path.join(site, failure["run_name"],
                                     failure["kind"], "index.html"),
                        "../../failures/" + failure["url"]))
        for summary, link in targets:
            if not os.path.isfile(summary):
                continue
            with open(summary, encoding="utf-8") as stream:
                page = stream.read()
            linked_name = (f'<tr><td>{label}</td><td><a href="'
                           f'{html.escape(link, quote=True)}">{name}</a></td>')
            if row_prefix in page:
                page = page.replace(row_prefix, linked_name, 1)
            # Functional tests are summarized by feature name, so link that
            # row too (e.g. consumer-poison-page-stat -> poison-page-stat).
            for feature, (owner, checks) in features.FEATURES.items():
                if failure["label"] != owner or failure["name"] not in checks:
                    continue
                feature_cell = f"<td>{html.escape(feature)}</td>"
                failed_cell = '<td class="FAIL">1</td>'
                feature_link = (f'<td><a href="{html.escape(link, quote=True)}">'
                                f'{html.escape(feature)}</a></td>')
                marker = feature_cell + "<td>0</td>" + failed_cell
                replacement = feature_link + "<td>0</td>" + failed_cell
                if marker in page:
                    page = page.replace(marker, replacement, 1)
            with open(summary, "w", encoding="utf-8") as stream:
                stream.write(page)

    rows = []
    for name, item in retained:
        label = html.escape(f"{item['kind']} — {item['timestamp']} — {item['conclusion']}")
        href = html.escape(f"../{name}/", quote=True)
        rows.append(f'<li><a href="{href}">{label}</a></li>')

    page = ["<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"]
    page.append("<title>rasdaemon CI evidence archive</title>")
    page.append("<h1>rasdaemon CI evidence archive</h1>")
    page.append("<p>Failed runs are retained indefinitely. Successful runs are retained for 30 days.</p>")
    page.append("<ul>" + "".join(rows) + "</ul>")
    with open(os.path.join(archive_site, "index.html"), "w", encoding="utf-8") as stream:
        stream.write("\n".join(page) + "\n")

    failure_rows = []
    for item in failures:
        label = html.escape(f"{item['timestamp']} · {item['kind']} · "
                            f"{item['label']} · {item['name']}")
        reason = html.escape(item["reason"])
        detail = html.escape(item["url"], quote=True)
        report = html.escape(item["report_url"], quote=True)
        failure_rows.append(
            f'<tr><td><a href="{detail}">{label}</a></td><td>{reason}</td>'
            f'<td><a href="{report}">Full run report</a></td></tr>')

    failure_page_html = ["<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"]
    failure_page_html.append("<title>Failed rasdaemon CI tests</title>")
    failure_page_html.append("<h1>Failed rasdaemon CI tests</h1>")
    failure_page_html.append("<p>Each failed test links to its captured evidence.</p>")
    if failure_rows:
        failure_page_html.append(
            "<table><thead><tr><th>Failed test</th><th>Reason</th>"
            "<th>Run report</th></tr></thead><tbody>" +
            "".join(failure_rows) + "</tbody></table>")
    else:
        failure_page_html.append("<p>No failed tests are in the retained archive.</p>")
    with open(os.path.join(failures_site, "index.html"), "w",
              encoding="utf-8") as stream:
        stream.write("\n".join(failure_page_html) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="persistent archive directory")
    parser.add_argument("--site", required=True, help="Pages archive directory")
    parser.add_argument("--run", action="append", default=[], metavar="KIND=DIR",
                        help="selected daily or weekly result artifact")
    args = parser.parse_args()
    archive(args.store, args.site, args.run, datetime.datetime.now(datetime.timezone.utc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
