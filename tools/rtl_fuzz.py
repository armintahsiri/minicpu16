#!/usr/bin/env python3
"""Seeded differential fuzzing for the Phase 5A RTL.

Every program is assembled, run on the golden timing layer, run on the RTL, and
compared by full retirement trace (retire cycles ignored in 5A) plus final state.
Coverage counters come from the core's optional COUNTING interface and from the
retirement trace; every counter must be non-zero across the run.

Usage:
  python3 tools/rtl_fuzz.py --n 300 --sim nvc
  python3 tools/rtl_fuzz.py --n 30 --sim nvc --quick
  python3 tools/rtl_fuzz.py --n 50 --sim ghdl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import isa_sim  # noqa: E402
import random_programs  # noqa: E402
import rtl_run  # noqa: E402
import trace_diff  # noqa: E402

MODES = ["hazards", "straight", "loop", "calls", "coverage", "mmio_writes"]

# Curated programs guarantee that every coverage counter is exercised even when
# a small quick run is used.  main is called by crt0; halt ends the run.
CURATED: List[Tuple[str, str, int]] = [
    ("halt_shadow", """
.text
.globl main
main:
    beq r0, r0, L1
    halt
    addi r5, r0, 1
L1:
    addi r6, r0, 1
    halt
""", 10),
    ("load_r0", """
.text
.globl main
main:
    li t0, 0x0100
    lw r0, 0(t0)
    add r1, r0, r0
    halt
""", 10),
    ("byte_lanes", """
.text
.globl main
main:
    li t0, 0x0100
    li t1, 0x00AB
    sb t1, 0(t0)
    li t2, 0x00CD
    sb t2, 1(t0)
    lb t3, 0(t0)
    lb a0, 1(t0)
    lbu a1, 0(t0)
    lbu a2, 1(t0)
    halt
""", 10),
    ("misaligned", """
.text
.globl main
main:
    li t0, 0x0100
    li t1, 0xABCD
    sw t1, 1(t0)
    lw t2, 1(t0)
    lb t3, 1(t0)
    lbu a1, 0(t0)
    halt
""", 10),
    ("store_load_d1", """
.text
.globl main
main:
    li t0, 0x0100
    li t1, 0x1234
    sw t1, 0(t0)
    lw t2, 0(t0)
    halt
""", 10),
    ("store_load_d2", """
.text
.globl main
main:
    li t0, 0x0100
    li t1, 0x1234
    sw t1, 0(t0)
    nop
    lw t2, 0(t0)
    halt
""", 10),
    ("store_load_d3", """
.text
.globl main
main:
    li t0, 0x0100
    li t1, 0x1234
    sw t1, 0(t0)
    nop
    nop
    lw t2, 0(t0)
    halt
""", 10),
    ("stall_causes", """
.text
.globl main
main:
    li t0, 0x0100
    li t1, 0x1234
    sw t1, 0(t0)
    lw t2, 0(t0)
    add t3, t2, t2
    lw a0, 0(t0)
    sw a0, 4(t0)
    lw a1, 0(t0)
    sw a1, 0(a1)
    halt
""", 10),
    ("mmio_writes", """
.text
.globl main
main:
    li t0, 0xFF04
    li t1, 0x1234
    sw t1, 0(t0)
    lw t2, 0(t0)
    li t3, 0xFF05
    li a0, 0x00AB
    sb a0, 0(t3)
    li a1, 0xFF00
    li a2, 0x0041
    sw a2, 0(a1)
    halt
""", 0),
    ("uart_drop", """
.text
.globl main
main:
    li t0, 0xFF00
    li t1, 0x0041
    sw t1, 0(t0)
    li t2, 0x0042
    sw t2, 0(t0)
    halt
""", 10),
    ("jal_flush", """
.text
.globl main
main:
    jal r15, L1
    addi r5, r0, 1
L1:
    addi r6, r0, 1
    halt
""", 10),
    ("jalr_flush", """
.text
.globl main
main:
    la t0, L1
    jalr r15, t0, 0
    addi r5, r0, 1
L1:
    addi r6, r0, 1
    halt
""", 10),
]

COV_KEYS = [
    "stall_total", "stall_ex", "stall_mem", "stall_rs1", "stall_rs2",
    "stall_store_data", "stall_load_use", "flush", "flush_branch",
    "flush_jump", "halt_shadow", "store_load_d1", "store_load_d2",
    "store_load_d3", "sb_even", "sb_odd", "lb_even", "lb_odd", "lbu_even",
    "lbu_odd", "sw", "lw", "load_r0", "uart_drop", "misaligned",
    "mmio_read", "mmio_write", "branch_taken", "branch_not_taken", "jal",
    "jalr", "halt",
]


def analyze_trace(path: Path) -> Dict[str, int]:
    c = {k: 0 for k in COV_KEYS}
    recs = trace_diff.parse_trace(str(path))
    for i, r in enumerate(recs):
        f = r.fields
        d = isa_sim.decode(int(f[3], 16))
        memop = f[6]
        addr = int(f[7], 16) if f[7] != "-" else None
        size = f[8]
        if d.name in ("JAL",):
            c["jal"] += 1
        if d.name in ("JALR",):
            c["jalr"] += 1
        if d.name == "HALT":
            c["halt"] += 1
        if d.fmt == "B":
            if f[10] == "1":
                c["branch_taken"] += 1
            else:
                c["branch_not_taken"] += 1
        if memop == "S" and size == "1" and addr is not None:
            c["sb_odd" if (addr & 1) else "sb_even"] += 1
        if memop == "L" and size == "1" and addr is not None:
            if d.name == "LB":
                c["lb_odd" if (addr & 1) else "lb_even"] += 1
            elif d.name == "LBU":
                c["lbu_odd" if (addr & 1) else "lbu_even"] += 1
        if memop == "S" and size == "2":
            c["sw"] += 1
        if memop == "L" and size == "2":
            c["lw"] += 1
        if memop == "L" and d.rd == 0:
            c["load_r0"] += 1
        if memop == "L" and addr is not None:
            for dist in (1, 2, 3):
                j = i - dist
                if j < 0:
                    continue
                p = recs[j]
                if p.fields[6] == "S" and p.fields[7] != "-" and int(p.fields[7], 16) == addr:
                    c["store_load_d%d" % dist] += 1
    return c


def add_core_cov(c: Dict[str, int], cov: Sequence[int]) -> None:
    names = [
        "stall_total", "stall_ex", "stall_mem", "stall_rs1", "stall_rs2",
        "stall_store_data", "flush", "halt_shadow", "load_r0", "uart_drop",
        "misaligned", "mmio_read", "mmio_write", "stall_load_use",
        "flush_branch", "flush_jump",
    ]
    for i, n in enumerate(names):
        if i < len(cov):
            c[n] += int(cov[i])


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Phase 5A RTL differential fuzzer")
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--seed-base", type=int, default=0)
    p.add_argument("--sim", choices=["nvc", "ghdl"], default="nvc")
    p.add_argument("--ghdl-subset", type=int, default=50)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--workdir", default=str(ROOT / "build" / "rtl_fuzz"))
    p.add_argument("--max-cycles", type=int, default=50000)
    p.add_argument("--ghdl", default=None)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--minimize", action="store_true")
    p.add_argument("--stop-on-fail", action="store_true")
    ns = p.parse_args(argv)
    if ns.quick:
        ns.n = min(ns.n, 30)
        ns.ghdl_subset = min(ns.ghdl_subset, 10)
    ghdl = rtl_run.find_ghdl(ns.ghdl)
    workdir = Path(ns.workdir)
    progdir = workdir / "programs"
    progdir.mkdir(parents=True, exist_ok=True)

    cases: List[Tuple[str, Path, int, int]] = []
    for i in range(ns.n):
        seed = ns.seed_base + i
        mode = MODES[i % len(MODES)]
        gp = random_programs.generate(seed, mode)
        path = progdir / ("seed_%04d_%s.s" % (seed, mode))
        path.write_text(gp.source, encoding="utf-8")
        uart_cycles = 0 if mode == "mmio_writes" else 10
        cases.append((("fuzz_%04d_%s" % (seed, mode)), path, uart_cycles, seed))
    for name, src, uart_cycles in CURATED:
        path = progdir / ("curated_%s.s" % name)
        path.write_text(src, encoding="utf-8")
        cases.append(("curated_" + name, path, uart_cycles, -1))

    results = []
    cov = {k: 0 for k in COV_KEYS}
    failed = 0
    for idx, (name, path, uart_cycles, seed) in enumerate(cases):
        res = rtl_run.run_case(
            [path], name=name, sim=ns.sim, runtime=True,
            workdir=workdir, max_cycles=ns.max_cycles,
            uart_cycles=uart_cycles, expected=None, ghdl=ghdl,
            rebuild=ns.rebuild)
        results.append(res)
        if res.get("error"):
            print("FAIL %-34s %-4s %s" % (name, ns.sim, res["error"]))
            failed += 1
            if ns.stop_on_fail:
                break
            continue
        add_core_cov(cov, res.get("rtl_cov", []))
        tc = analyze_trace(Path(res["trace_path"]))
        for k, v in tc.items():
            cov[k] += v
        if not res.get("ok"):
            failed += 1
            rtl_run.print_result(res)
            bad = workdir / ("fail_" + name)
            bad.mkdir(parents=True, exist_ok=True)
            (bad / (path.name)).write_text(path.read_text(), encoding="utf-8")
            for f in ("golden.trace", "rtl.trace", "rtl.state"):
                src = Path(res["trace_path"]).parent / f
                if src.exists():
                    (bad / f).write_text(src.read_text(), encoding="utf-8")
            print("  failing program saved in %s (seed=%s)" % (bad, seed))
            if ns.stop_on_fail:
                break
        else:
            print("PASS %-34s %-4s cycles=%-6s stalls=%-5s" %
                  (name, ns.sim, res.get("rtl_cycles"), res.get("rtl_stalls")))

    print()
    print("fuzz: %d cases, %d failed, simulator=%s" % (len(results), failed, ns.sim))
    print("coverage table:")
    zero = []
    for k in COV_KEYS:
        print("  %-20s %d" % (k, cov[k]))
        if cov[k] == 0:
            zero.append(k)
    if zero:
        print("COVERAGE GAP: zero counters: %s" % ", ".join(zero))
        return 1

    # Fixed GHDL subset of the same generated programs.
    if ns.ghdl_subset > 0 and ns.sim == "nvc":
        subset = [c for c in cases[:ns.n] if c[3] >= 0]
        step = max(1, len(subset) // ns.ghdl_subset)
        subset = subset[::step][:ns.ghdl_subset]
        print("GHDL subset: %d programs" % len(subset))
        gfail = 0
        for name, path, uart_cycles, seed in subset:
            res = rtl_run.run_case(
                [path], name=name + "_ghdl", sim="ghdl", runtime=True,
                workdir=workdir, max_cycles=ns.max_cycles,
                uart_cycles=uart_cycles, expected=None, ghdl=ghdl,
                rebuild=ns.rebuild)
            if not res.get("ok"):
                gfail += 1
                rtl_run.print_result(res)
        print("GHDL subset: %d cases, %d failed" % (len(subset), gfail))
        if gfail:
            return 1

    if ns.minimize:
        print("minimizer: skipped (not implemented robustly in 5A; failing "
              "programs and both traces are saved for manual reduction)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
