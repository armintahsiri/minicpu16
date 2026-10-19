#!/usr/bin/env python3
"""Generator self-validation and reference-trace regression tests."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

import assembler
import random_programs as rp
import trace_diff
from isa_sim import Config, run_pipeline

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "tests" / "reference_traces"
CHECKS = 0


class GeneratorTests(unittest.TestCase):
    def eq(self, got, want, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertEqual(got, want, msg)

    def check(self, cond, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertTrue(cond, msg)

    def test_deterministic_generation(self):
        for seed in (0, 1, 7, 1234):
            a = rp.generate(seed)
            b = rp.generate(seed)
            self.eq(a.source, b.source, "seed %d source" % seed)
            self.eq(a.hazards, b.hazards, "seed %d hazards" % seed)

    def test_self_validation_200(self):
        report = rp.self_validate(200, seed=1234)
        self.eq(report["programs"], 200)
        self.eq(report["halted"], 200)
        self.eq(report["missing_opcodes"], [])
        self.eq(report["missing_hazards"], [])
        for name in rp.HAZARD_NAMES:
            self.check(report["hazard_counts"].get(name, 0) > 0, "hazard " + name)
        self.check(len(report["opcodes"]) >= 33, "opcode count")

    def test_self_validation_reproducible(self):
        a = rp.self_validate(20, seed=99)
        b = rp.self_validate(20, seed=99)
        self.eq(a, b)
        c = rp.self_validate(20, seed=100)
        self.check(a != c, "different seeds should differ")

    def test_reference_manifest(self):
        manifest = json.loads((REF / "manifest.json").read_text())
        self.eq(len(manifest), 5)
        names = {e["name"] for e in manifest}
        self.eq(names, {"01_straight", "02_loop", "03_calls", "04_hazards", "05_mmio"})

    def test_reference_traces_reproduce(self):
        manifest = json.loads((REF / "manifest.json").read_text())
        for e in manifest:
            asm_path = REF / (e["name"] + ".asm")
            trace_path = REF / (e["name"] + ".trace")
            self.check(asm_path.exists(), e["name"] + ".asm exists")
            self.check(trace_path.exists(), e["name"] + ".trace exists")
            asm = asm_path.read_text()
            self.eq(hashlib.sha256(asm.encode()).hexdigest(), e["asm_sha256"], e["name"] + " asm hash")
            img = assembler.assemble_text(asm, instr_words=e["instr_words"], ram_bytes=e["ram_bytes"])
            res = run_pipeline(img.instr_words, Config(
                instr_words=e["instr_words"], ram_bytes=e["ram_bytes"], ram_init=img.data))
            self.eq(res.trace_text(), trace_path.read_text(), e["name"] + " trace")
            self.eq(hashlib.sha256(res.trace_text().encode()).hexdigest(), e["trace_sha256"],
                    e["name"] + " trace hash")
            self.eq(res.cycles, e["cycles"], e["name"] + " cycles")
            self.eq(res.instructions, e["instructions"], e["name"] + " instructions")
            self.eq(res.pc, e["pc"], e["name"] + " pc")
            self.eq(res.halted, e["halted"], e["name"] + " halted")
            parsed = trace_diff.parse_trace(str(trace_path))
            self.eq(len(parsed), e["instructions"], e["name"] + " parsed records")


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1)
    print("generator checks: %d" % CHECKS)
    sys.exit(0 if result.result.wasSuccessful() else 1)
