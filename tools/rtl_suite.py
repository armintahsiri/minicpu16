#!/usr/bin/env python3
"""Run the Phase 5A differential suite: reference traces, benchmarks, directed.

Cycle-sensitive programs are excluded from the 5A architectural comparison and
listed with reasons.  The RTL still runs them with --include-cycle-sensitive (for
information only); they are not counted as passes.

Usage:
  python3 tools/rtl_suite.py --sim nvc
  python3 tools/rtl_suite.py --sim both --quick
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rtl_run  # noqa: E402
import trace_diff  # noqa: E402
import isa_sim  # noqa: E402

REFERENCE_EXCLUDE = {
    "05_mmio": "reads GPIO_IN (synchroniser latency), TIMER (cycle number) and "
               "UART_STATUS (busy) - all cycle_sensitive in 5A",
}

BENCH_EXCLUDE = {
    "hello": "polls UART_STATUS and transmits; the number of busy-poll iterations "
             "depends on cycle timing, so the retirement trace is cycle_sensitive",
}

BENCHMARKS = [
    "fibonacci", "factorial_rec", "bubble_sort", "crc16", "memory_ops",
    "muldiv", "matmul", "switch", "nested_calls", "funcptr",
]

QUICK_BENCH = ["fibonacci", "factorial_rec", "bubble_sort", "switch"]

GHDL_SUBSET = {
    "ref_01_straight", "ref_03_calls",
    "dir_a_store_load_d1", "dir_b_load_use_alu_a",
    "dir_f_mmio", "dir_h_r0",
    "bench_fibonacci", "bench_switch",
}


def run_reference(name: str, sim: str, ns, ghdl: Optional[str]) -> Dict[str, object]:
    asm = ROOT / "tests" / "reference_traces" / (name + ".asm")
    shipped = ROOT / "tests" / "reference_traces" / (name + ".trace")
    res = rtl_run.run_case(
        [asm], name="ref_" + name, sim=sim, runtime=False,
        workdir=ns.workdir, max_cycles=ns.max_cycles,
        uart_cycles=10, expected=None, ghdl=ghdl, rebuild=ns.rebuild)
    # Also check the shipped trace equals the regenerated golden trace.
    case_dir = Path(ns.workdir) / ("ref_" + name + "_" + sim)
    gold = case_dir / "golden.trace"
    if gold.exists() and shipped.exists():
        a = trace_diff.parse_trace(str(shipped))
        b = trace_diff.parse_trace(str(gold))
        ok, msg = trace_diff.diff_traces(a, b, ignore_cycles=False)
        res["shipped_trace_ok"] = ok
        if not ok:
            res["shipped_trace_message"] = msg
            res["ok"] = False
    return res


def run_bench(name: str, sim: str, ns, ghdl: Optional[str]) -> Dict[str, object]:
    asm = ROOT / "programs" / (name + ".s")
    return rtl_run.run_case(
        [asm], name="bench_" + name, sim=sim, runtime=True,
        workdir=ns.workdir, max_cycles=ns.max_cycles,
        uart_cycles=10, expected=None, ghdl=ghdl, rebuild=ns.rebuild)


def run_directed(path: Path, sim: str, ns, ghdl: Optional[str]) -> Dict[str, object]:
    name = path.stem
    exp = path.with_suffix(".expected")
    instr_words = 65536 if name == "g_pc_wrap" else 2048
    return rtl_run.run_case(
        [path], name="dir_" + name, sim=sim, runtime=False,
        workdir=ns.workdir, max_cycles=ns.max_cycles,
        instr_words=instr_words, uart_cycles=10,
        expected=exp if exp.exists() else None,
        ghdl=ghdl, rebuild=ns.rebuild)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Phase 5A differential suite")
    p.add_argument("--sim", choices=["nvc", "ghdl", "both"], default="nvc")
    p.add_argument("--quick", action="store_true")
    p.add_argument("--workdir", default=str(ROOT / "build" / "rtl_suite"))
    p.add_argument("--max-cycles", type=int, default=400000)
    p.add_argument("--ghdl", default=None)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--include-cycle-sensitive", action="store_true")
    ns = p.parse_args(argv)
    ghdl = rtl_run.find_ghdl(ns.ghdl)
    sims = ["nvc", "ghdl"] if ns.sim == "both" else [ns.sim]
    results: List[Dict[str, object]] = []

    for sim in sims:
        print("=== reference traces (%s) ===" % sim)
        for asm in sorted((ROOT / "tests" / "reference_traces").glob("*.asm")):
            name = asm.stem
            if name in REFERENCE_EXCLUDE and not ns.include_cycle_sensitive:
                print("SKIP ref_%-24s %-4s  %s" % (name, sim, REFERENCE_EXCLUDE[name]))
                continue
            res = run_reference(name, sim, ns, ghdl)
            rtl_run.print_result(res)
            results.append(res)

        print("=== benchmark programs (%s) ===" % sim)
        bench = QUICK_BENCH if ns.quick else BENCHMARKS
        for name in bench:
            if name in BENCH_EXCLUDE and not ns.include_cycle_sensitive:
                print("SKIP bench_%-22s %-4s  %s" % (name, sim, BENCH_EXCLUDE[name]))
                continue
            res = run_bench(name, sim, ns, ghdl)
            rtl_run.print_result(res)
            results.append(res)

        print("=== directed tests (%s) ===" % sim)
        for asm in sorted((ROOT / "tests" / "directed").glob("*.asm")):
            if asm.stem == "smoke":
                continue
            if sim == "ghdl" and ns.sim == "both" and ("dir_" + asm.stem) not in GHDL_SUBSET:
                continue
            if ns.quick and asm.stem not in {
                "a_store_load_d1", "a_misaligned", "b_load_use_alu_a",
                "b_load_use_store_data", "c_branch_shadow_halt",
                "d_halt", "f_mmio", "g_beyond", "h_r0", "i_jalr_rd_rs1",
                "g_pc_wrap",
            }:
                continue
            res = run_directed(asm, sim, ns, ghdl)
            rtl_run.print_result(res)
            results.append(res)

    failed = [r for r in results if not r.get("ok")]
    print()
    print("differential suite: %d cases, %d failed" % (len(results), len(failed)))
    for r in failed:
        print("  FAILED %s (%s)" % (r.get("name"), r.get("sim")))
    print("5A cycle_sensitive exclusions:")
    for k, v in REFERENCE_EXCLUDE.items():
        print("  reference %-16s %s" % (k, v))
    for k, v in BENCH_EXCLUDE.items():
        print("  benchmark %-16s %s" % (k, v))
    print("  fuzz mmio generator mode reads TIMER/UART_STATUS/GPIO_IN; the fuzzer "
          "uses write-only MMIO programs instead.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
