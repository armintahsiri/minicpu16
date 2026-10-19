#!/usr/bin/env python3
"""Run every program on both golden-model layers and assert its results."""

import os
import sys
import tempfile
import unittest

import program_suite as ps
import trace_diff
from isa_sim import Config, decode, run_arch

CHECKS = 0


# hello polls UART_STATUS, which depends on the cycle number.  The architectural
# layer counts one cycle per instruction, so its polling loop runs a different
# number of iterations than the timing layer.  Only the UART byte stream is
# required to match; the timing layer is the contract for busy timing.
CYCLE_DEPENDENT = {"hello"}


class ProgramTests(unittest.TestCase):
    def eq(self, got, want, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertEqual(got, want, msg)

    def check(self, cond, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertTrue(cond, msg)

    def test_all_expected_results(self):
        for p in ps.PROGRAMS:
            img, res = ps.run(p)
            checks = ps.verify(p, img, res)
            for name, got, want in checks:
                self.eq(got, want, "%s %s" % (p.name, name))

    def test_architectural_and_timing_layers_agree(self):
        for p in ps.PROGRAMS:
            img, pres = ps.run(p)
            ares = run_arch(img.instr_words, Config(ram_init=img.data))
            if p.name in CYCLE_DEPENDENT:
                self.eq(ares.uart, pres.uart, p.name + " uart")
                continue
            self.eq(ares.regs, pres.regs, p.name + " regs")
            self.eq(ares.ram, pres.ram, p.name + " ram")
            self.eq(ares.pc, pres.pc, p.name + " pc")
            self.eq(ares.instructions, pres.instructions, p.name + " instructions")
            self.eq(ares.uart, pres.uart, p.name + " uart")
            self.eq(ares.gpio_out, pres.gpio_out, p.name + " gpio")

    def test_traces_agree_except_cycles(self):
        for p in ps.PROGRAMS:
            if p.name in CYCLE_DEPENDENT:
                continue
            img, pres = ps.run(p)
            ares = run_arch(img.instr_words, Config(ram_init=img.data))
            with tempfile.TemporaryDirectory() as d:
                pa = os.path.join(d, "a.trace")
                pp = os.path.join(d, "p.trace")
                with open(pa, "w", newline="\n") as f:
                    f.write(ares.trace_text())
                with open(pp, "w", newline="\n") as f:
                    f.write(pres.trace_text())
                ta = trace_diff.parse_trace(pa)
                tp = trace_diff.parse_trace(pp)
                ok, msg = trace_diff.diff_traces(ta, tp, ignore_cycles=True)
                self.check(ok, "%s trace diff: %s" % (p.name, msg))

    def test_halt_trace_and_sanity(self):
        for p in ps.PROGRAMS:
            img, res = ps.run(p)
            self.eq(res.halted, True, p.name + " halted")
            self.eq(decode(res.trace[-1].instr).name, "HALT", p.name + " last is HALT")
            self.check(res.cycles >= res.instructions, p.name + " cycles >= instructions")
            self.check(res.instructions > 0, p.name + " non-empty")
            self.check(res.stalls >= 0, p.name + " stalls")

    def test_required_program_set(self):
        names = {p.name for p in ps.PROGRAMS}
        for required in ("fibonacci", "factorial_rec", "bubble_sort", "crc16",
                         "memory_ops", "muldiv", "matmul", "hello", "switch",
                         "nested_calls", "funcptr"):
            self.check(required in names, "missing program " + required)
            self.check((ps.ROOT / "programs" / (required + ".s")).exists(),
                       "missing source " + required + ".s")

    def test_runtime_files_exist(self):
        for f in ps.RUNTIME:
            self.check((ps.ROOT / f).exists(), "missing runtime " + f)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1)
    print("program checks: %d" % CHECKS)
    sys.exit(0 if result.result.wasSuccessful() else 1)
