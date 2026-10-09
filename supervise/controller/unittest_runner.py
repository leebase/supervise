# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Structured adapter for ordinary ``python -m unittest`` execution."""

import json
import sys
import traceback
import unittest
from pathlib import Path


class Observed(unittest.TextTestResult):
    def __init__(self, *args):
        super().__init__(*args)
        self.outcomes = {}

    def startTest(self, test):
        self.outcomes[test.id()] = {"outcome": "UNKNOWN", "diagnostic": "incomplete"}
        super().startTest(test)

    def observe(self, test, outcome, diagnostic=""):
        self.outcomes[test.id()] = {"outcome": outcome, "diagnostic": diagnostic}

    def addSuccess(self, test):
        if self.outcomes[test.id()]["outcome"] != "FAIL":
            self.observe(test, "PASS")
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.observe(test, "FAIL", "".join(traceback.format_exception(*err)))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.observe(test, "ERROR", "".join(traceback.format_exception(*err)))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.observe(test, "SKIP", reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.observe(test, "XFAIL", "".join(traceback.format_exception(*err)))
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.observe(test, "FAIL", "unexpected success")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.observe(test, "FAIL", "".join(traceback.format_exception(*err)))
        super().addSubTest(test, subtest, err)


def flatten(suite):
    if isinstance(suite, unittest.TestSuite):
        for child in suite:
            yield from flatten(child)
    else:
        yield suite


class ObservedProgram(unittest.TestProgram):
    def __init__(self, *, collect_only, **kwargs):
        self.collect_only = collect_only
        self.observed_ids = []
        self.collection_complete = False
        super().__init__(**kwargs)

    def runTests(self):
        tests = list(flatten(self.test))
        self.observed_ids = [test.id() for test in tests]
        self.collection_complete = (
            not self.testLoader.errors
            and len(set(self.observed_ids)) == len(self.observed_ids)
        )
        if not self.collect_only:
            super().runTests()


class ObservedRunner(unittest.TextTestRunner):
    resultclass = Observed


def main():
    if len(sys.argv) < 3 or sys.argv[2] not in {"run", "collect"}:
        raise SystemExit("usage: unittest_runner.py OUTPUT run|collect [unittest args...]")
    output, mode, *args = sys.argv[1:]
    collect = mode == "collect"
    sys.path[0] = str(Path.cwd())
    report = {"complete": False, "ids": [], "outcomes": {}}
    exit_code = 2
    try:
        program = ObservedProgram(
            collect_only=collect,
            module=None,
            argv=["unittest", *args],
            exit=False,
            testRunner=ObservedRunner,
        )
        outcomes = {}
        if not collect:
            if not isinstance(program.result, Observed):
                raise ValueError("Structured result adapter not used")
            outcomes = program.result.outcomes
        report.update(ids=program.observed_ids, complete=program.collection_complete,
                      outcomes=outcomes)
        if not collect:
            report["complete"] &= set(report["ids"]) == set(report["outcomes"])
        exit_code = 0 if collect or program.result.wasSuccessful() else 1
    except BaseException:
        report["error"] = traceback.format_exc()
        traceback.print_exc()
    Path(output).write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
