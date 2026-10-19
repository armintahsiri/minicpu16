#!/usr/bin/env python3
"""Tests for tools/trace_diff.py."""

import contextlib
import io
import os
import sys
import tempfile
import unittest

import trace_diff
from trace_diff import TraceFormatError, diff_traces, parse_trace

CHECKS = 0

HDR = trace_diff.HEADER0 + "\n" + trace_diff.HEADER1 + "\n"
L1 = "1 5 0000 0B100001 r1 0001 - - - - 0 -\n"
L2 = "2 6 0001 00000000 - - - - - - 0 -\n"


class TraceDiffTests(unittest.TestCase):
    def eq(self, got, want, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertEqual(got, want, msg)

    def check(self, cond, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertTrue(cond, msg)

    def write(self, d, name, text):
        p = os.path.join(d, name)
        with open(p, "w", newline="\n") as f:
            f.write(text)
        return p

    def test_identical(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.trace", HDR + L1 + L2)
            b = self.write(d, "b.trace", HDR + L1 + L2)
            ok, msg = diff_traces(parse_trace(a), parse_trace(b))
            self.eq(ok, True)
            self.check("identical" in msg)

    def test_value_divergence(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.trace", HDR + L1 + L2)
            bad = L1.replace("r1 0001", "r1 0002")
            b = self.write(d, "b.trace", HDR + bad + L2)
            ok, msg = diff_traces(parse_trace(a), parse_trace(b), context=1)
            self.eq(ok, False)
            self.check("first divergence at record 1" in msg)
            self.check("value" in msg)
            self.check("left='0001'" in msg and "right='0002'" in msg)

    def test_cycle_difference(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.trace", HDR + L1 + L2)
            b = self.write(d, "b.trace", HDR + L1.replace("1 5 ", "1 9 ", 1) + L2)
            self.eq(diff_traces(parse_trace(a), parse_trace(b))[0], False)
            self.eq(diff_traces(parse_trace(a), parse_trace(b), ignore_cycles=True)[0], True)

    def test_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.trace", HDR + L1 + L2)
            b = self.write(d, "b.trace", HDR + L1)
            ok, msg = diff_traces(parse_trace(a), parse_trace(b))
            self.eq(ok, False)
            self.check("right trace ended" in msg)

    def test_malformed_field_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "bad.trace", HDR + "1 5 0000\n")
            global CHECKS
            CHECKS += 1
            with self.assertRaises(TraceFormatError):
                parse_trace(p)

    def test_malformed_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "bad.trace", L1)
            global CHECKS
            CHECKS += 1
            with self.assertRaises(TraceFormatError):
                parse_trace(p)

    def test_taken_target(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.trace", HDR + "1 5 0000 1F000003 r15 0001 - - - - 1 0003\n")
            b = self.write(d, "b.trace", HDR + "1 5 0000 1F000003 r15 0001 - - - - 1 0004\n")
            ok, msg = diff_traces(parse_trace(a), parse_trace(b))
            self.eq(ok, False)
            self.check("target" in msg)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.write(d, "a.trace", HDR + L1 + L2)
            b = self.write(d, "b.trace", HDR + L1 + L2)
            c = self.write(d, "c.trace", HDR + L1.replace("r1 0001", "r1 0002") + L2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.eq(trace_diff.main([a, b]), 0)
            with contextlib.redirect_stdout(buf):
                self.eq(trace_diff.main([a, c]), 1)
            bad = self.write(d, "bad.trace", "nonsense\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.eq(trace_diff.main([a, bad]), 2)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1)
    print("trace_diff checks: %d" % CHECKS)
    sys.exit(0 if result.result.wasSuccessful() else 1)
