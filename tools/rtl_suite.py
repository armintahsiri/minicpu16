#!/usr/bin/env python3
"""Run the Phase 5B differential suite: reference traces, benchmarks, directed.

By default every case is compared cycle-exactly (retirement trace including the
retire-cycle column, final state, final cycle count, stalls and per-cycle
pipeview).  --arch-only keeps the Phase 5A architectural comparison for quick
debugging and for the FORWARDING=false regression subset.

Usage:
  python3 tools/rtl_suite.py --sim nvc
  python3 tools/rtl_suite.py --sim both --quick
  python3 tools/rtl_suite.py --sim nvc --arch-only --no-forwarding
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

BENCHMARKS = [
    "fibonacci", "factorial_rec", "bubble_sort", "crc16", "memory_ops",
    "muldiv", "matmul", "switch", "nested_calls", "funcptr", "hello",
]

QUICK_BENCH = ["fibonacci", "factorial_rec", "bubble_sort", "switch", "hello"]


def run_reference(name: str, sim: str, ns, ghdl: Optional[str]) -> Dict[str, object]:
    asm = ROOT / "tests" / "reference_traces" / (name + ".asm")
    shipped = ROOT / "tests" / "reference_traces" / (name + ".trace")
    res = rtl_run.run_case(
        [asm], name="ref_" + name, sim=sim, runtime=False,
        workdir=ns.workdir, max_cycles=ns.max_cycles,
        uart_cycles=10, expected=None, ghdl=ghdl, rebuild=ns.rebuild,
        forwarding=ns.forwarding, ignore_cycles=ns.arch_only,
        compare_pipe=not ns.arch_only)
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


def run_bench(name: str, sim: str, ns, ghdl: Optional[str],
              uart_cycles: int = 10) -> Dict[str, object]:
    asm = ROOT / "programs" / (name + ".s")
    return rtl_run.run_case(
        [asm], name="bench_" + name, sim=sim, runtime=True,
        workdir=ns.workdir, max_cycles=ns.max_cycles,
        uart_cycles=uart_cycles, expected=None, ghdl=ghdl, rebuild=ns.rebuild,
        forwarding=ns.forwarding, ignore_cycles=ns.arch_only,
        compare_pipe=not ns.arch_only)


def run_directed(path: Path, sim: str, ns, ghdl: Optional[str]) -> Dict[str, object]:
    name = path.stem
    exp = path.with_suffix(".expected")
    sched = path.with_suffix(".gpio")
    instr_words = 65536 if name == "g_pc_wrap" else 2048
    return rtl_run.run_case(
        [path], name="dir_" + name, sim=sim, runtime=False,
        workdir=ns.workdir, max_cycles=ns.max_cycles,
        instr_words=instr_words, uart_cycles=10,
        gpio_schedule=sched if sched.exists() else None,
        expected=exp if exp.exists() else None,
        ghdl=ghdl, rebuild=ns.rebuild, forwarding=ns.forwarding,
        ignore_cycles=ns.arch_only, compare_pipe=not ns.arch_only)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Phase 5B differential suite")
    p.add_argument("--sim", choices=["nvc", "ghdl", "both"], default="nvc")
    p.add_argument("--quick", action="store_true")
    p.add_argument("--workdir", default=str(ROOT / "build" / "rtl_suite"))
    p.add_argument("--max-cycles", type=int, default=400000)
    p.add_argument("--ghdl", default=None)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--arch-only", action="store_true",
                   help="ignore retire cycles and pipeview (Phase 5A comparison)")
    p.add_argument("--forwarding", dest="forwarding", action="store_true", default=True)
    p.add_argument("--no-forwarding", dest="forwarding", action="store_false")
    p.add_argument("--uart-cycle-tests", action="store_true",
                   help="also run hello at UART_CYCLES_PER_BYTE = 1 and 104")
    p.add_argument("--exclude-timing-dependent", action="store_true",
                   help="skip programs whose architectural trace depends on cycle timing")
    ns = p.parse_args(argv)
    ghdl = rtl_run.find_ghdl(ns.ghdl)
    sims = ["nvc", "ghdl"] if ns.sim == "both" else [ns.sim]
    results: List[Dict[str, object]] = []

    for sim in sims:
        print("=== reference traces (%s) ===" % sim)
        for asm in sorted((ROOT / "tests" / "reference_traces").glob("*.asm")):
            if ns.exclude_timing_dependent and asm.stem == "05_mmio":
                continue
            res = run_reference(asm.stem, sim, ns, ghdl)
            rtl_run.print_result(res)
            results.append(res)

        print("=== benchmark programs (%s) ===" % sim)
        bench = QUICK_BENCH if ns.quick else BENCHMARKS
        for name in bench:
            if ns.exclude_timing_dependent and name == "hello":
                continue
            res = run_bench(name, sim, ns, ghdl)
            rtl_run.print_result(res)
            results.append(res)
        if ns.uart_cycle_tests and not ns.quick:
            for n in (1, 104):
                res = run_bench("hello", sim, ns, ghdl, uart_cycles=n)
                res["name"] = "bench_hello_uart%d" % n
                rtl_run.print_result(res)
                results.append(res)

        print("=== directed tests (%s) ===" % sim)
        for asm in sorted((ROOT / "tests" / "directed").glob("*.asm")):
            if asm.stem == "smoke":
                continue
            if ns.exclude_timing_dependent and asm.stem in {
                "k_gpio_schedule", "f_timer_cycle", "f_uart_busy_edge",
            }:
                continue
            if ns.quick and asm.stem not in {
                "a_store_load_d1", "a_misaligned", "b_load_use_alu_a",
                "b_load_use_store_data", "c_branch_shadow_halt",
                "d_halt", "f_mmio", "g_beyond", "h_r0", "i_jalr_rd_rs1",
                "g_pc_wrap", "f_timer_cycle", "f_uart_busy_edge",
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
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
