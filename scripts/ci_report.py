"""Write a truthful GitHub Actions test summary from a pytest JUnit report."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def junit_totals(path: Path) -> dict[str, int | str]:
    totals: dict[str, int | str] = {"tests": "unavailable", "failures": "unavailable", "errors": "unavailable",
                                    "skipped": "unavailable", "time": "unavailable"}
    if not path.exists():
        return totals
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return totals
    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if not suites:
        return totals
    values = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    duration = 0.0
    for suite in suites:
        for key in values:
            values[key] += int(suite.get(key, "0"))
        duration += float(suite.get("time", "0") or 0)
    values["time"] = f"{duration:.2f}s"
    return values


def code(path: Path) -> str:
    try:
        return "PASS" if path.read_text().strip() == "0" else "FAIL"
    except OSError:
        return "INCOMPLETE"


def warning_count(path: Path) -> int | str:
    """Return pytest's final warning count when it recorded one."""
    try:
        match = re.search(r"\b(\d+) warnings?\b", path.read_text())
    except OSError:
        return "unavailable"
    return int(match.group(1)) if match else 0


def failed_tests(path: Path, limit: int = 10) -> list[str] | str:
    """Return a bounded list of failed JUnit testcase identifiers."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return "unavailable"
    failures = []
    for case in root.findall(".//testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            identifier = ".".join(filter(None, (case.get("classname"), case.get("name"))))
            failures.append(identifier or "unnamed testcase")
            if len(failures) == limit:
                break
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, default=Path("artifacts/junit.xml"))
    args = parser.parse_args()
    totals = junit_totals(args.junit)
    failures = failed_tests(args.junit)
    lines = [
        "## PHOS software validation",
        "",
        f"- Event: `{os.getenv('GITHUB_EVENT_NAME', 'local')}`",
        f"- Ref: `{os.getenv('GITHUB_REF_NAME', 'local')}`",
        f"- Commit: `{os.getenv('GITHUB_SHA', 'unknown')}`",
        f"- Runner: `{os.getenv('RUNNER_OS', 'local')}`",
        f"- Python: `{sys.version.split()[0]}`",
        f"- Tests: {totals['tests']}; failures: {totals['failures']}; errors: {totals['errors']}; skipped: {totals['skipped']}; duration: {totals['time']}",
        f"- Pytest warnings: {warning_count(Path('artifacts/pytest.txt'))}",
        f"- Failed testcases: {', '.join(failures) if isinstance(failures, list) and failures else ('none' if isinstance(failures, list) else failures)}",
        f"- Static compilation: {code(Path('artifacts/compileall.exit-code'))}",
        f"- Documentation build: {code(Path('artifacts/docs.exit-code'))}",
        f"- Final outcome: {code(Path('artifacts/validation.exit-code'))}",
        "- Physical Raspberry Pi validation: **NOT RUN**",
    ]
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).open("a").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
