import importlib.util
from pathlib import Path


_PATH = Path(__file__).parents[1] / "scripts" / "ci_report.py"
_SPEC = importlib.util.spec_from_file_location("ci_report", _PATH)
ci_report = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(ci_report)


def test_junit_totals_accepts_testsuites_and_missing_report(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text('<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1" time="1.5" /></testsuites>')

    assert ci_report.junit_totals(report) == {
        "tests": 3, "failures": 1, "errors": 0, "skipped": 1, "time": "1.50s",
    }
    assert ci_report.junit_totals(tmp_path / "missing.xml")["tests"] == "unavailable"


def test_warning_count_uses_pytest_summary_when_available(tmp_path):
    output = tmp_path / "pytest.txt"
    output.write_text("10 passed, 2 warnings in 0.40s\n")

    assert ci_report.warning_count(output) == 2
    assert ci_report.warning_count(tmp_path / "missing.txt") == "unavailable"


def test_failed_tests_reports_bounded_testcase_identifiers(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.test_ci" name="passes" />'
        '<testcase classname="tests.test_ci" name="fails"><failure /></testcase></testsuite>'
    )

    assert ci_report.failed_tests(report) == ["tests.test_ci.fails"]
    assert ci_report.failed_tests(tmp_path / "missing.xml") == "unavailable"
