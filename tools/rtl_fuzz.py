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
import hashlib
import json
import re
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

MODES = ["hazards", "straight", "loop", "calls", "coverage", "mmio_writes", "fwd",
         "store_load"]

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
    # Phase 5B forwarding coverage (RTL counters).
    "fwd_exmem_a", "fwd_exmem_b", "fwd_memwb_a", "fwd_memwb_b",
    "fwd_branch", "fwd_store_data", "fwd_store_addr", "fwd_jalr_base",
    "fwd_priority", "fwd_r0_source", "stall_lu_alu", "stall_lu_store_data",
    "stall_lu_store_addr", "stall_lu_branch", "stall_lu_jalr",
    "stall_near_flush", "fwd_lb_sext", "fwd_link", "fwd_from_bubble",
]

# Counters that are architecturally unreachable in the forwarding configuration
# and must therefore be exactly zero there.  `stall_ex`/`stall_mem` are the
# Phase 5A conservative stall causes (producer in EX or MEM), which do not exist
# once forwarding is enabled; `stall_near_flush` and `fwd_from_bubble` cannot
# occur because a load-use stall and a redirect are mutually exclusive and the
# forwarding mux is gated by the stage valid bits (docs/RTL_FINDINGS.md F28).
EXPECTED_ZERO_FWD = {"stall_ex", "stall_mem", "stall_near_flush",
                     "fwd_from_bubble"}


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
        "fwd_exmem_a", "fwd_exmem_b", "fwd_memwb_a", "fwd_memwb_b",
        "fwd_branch", "fwd_store_data", "fwd_store_addr", "fwd_jalr_base",
        "fwd_priority", "fwd_r0_source", "stall_lu_alu", "stall_lu_store_data",
        "stall_lu_store_addr", "stall_lu_branch", "stall_lu_jalr",
        "stall_near_flush", "fwd_lb_sext", "fwd_link", "fwd_from_bubble",
    ]
    for i, n in enumerate(names):
        if i < len(cov):
            c[n] += int(cov[i])


def normalise(text: str) -> str:
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(re.sub(r"\s+", " ", line))
    return "\n".join(out)


def diversity_report(sources, modes) -> Dict[str, object]:
    from collections import defaultdict
    per_total = defaultdict(int)
    per_unique = defaultdict(set)
    all_h = set()
    for src, mode in zip(sources, modes):
        h = hashlib.sha256(normalise(src).encode()).hexdigest()
        per_total[mode] += 1
        per_unique[mode].add(h)
        all_h.add(h)
    rows = []
    for mode in sorted(per_total):
        total = per_total[mode]
        uniq = len(per_unique[mode])
        rows.append({"mode": mode, "total": total, "unique": uniq,
                     "pct": 100.0 * uniq / total if total else 0.0})
    return {"total": len(sources), "unique": len(all_h),
            "pct": 100.0 * len(all_h) / len(sources) if sources else 0.0,
            "per_mode": rows}


def check_diversity(rep, min_overall=98.0, min_mode=90.0) -> List[str]:
    errs = []
    if rep["pct"] < min_overall:
        errs.append("overall uniqueness %.1f%% < %.0f%%" % (rep["pct"], min_overall))
    for row in rep["per_mode"]:
        if row["total"] and row["pct"] < min_mode:
            errs.append("mode %s uniqueness %.1f%% < %.0f%%" %
                        (row["mode"], row["pct"], min_mode))
    return errs


def print_cov(title: str, cov: Dict[str, int]) -> None:
    print(title)
    for k in COV_KEYS:
        print("  %-20s %d" % (k, cov[k]))


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Phase 5B RTL differential fuzzer")
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--seed-base", type=int, default=0)
    p.add_argument("--sim", choices=["nvc", "ghdl"], default="nvc")
    p.add_argument("--ghdl-subset", type=int, default=100)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--workdir", default=str(ROOT / "build" / "rtl_fuzz"))
    p.add_argument("--max-cycles", type=int, default=50000)
    p.add_argument("--ghdl", default=None)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--minimize", action="store_true")
    p.add_argument("--stop-on-fail", action="store_true")
    p.add_argument("--no-cov-assert", action="store_true")
    p.add_argument("--gpio-schedule", default=None,
                   help="GPIO_IN cycle/value schedule file shared with the model")
    p.add_argument("--check-cycles", action="store_true",
                   help="compare the retire-cycle column too (Phase 5B)")
    p.add_argument("--mode", choices=MODES, default=None,
                   help="force one generator mode (default: cycle through MODES)")
    p.add_argument("--forwarding", dest="forwarding", action="store_true",
                   default=False, help="run the forwarding configuration")
    p.add_argument("--no-forwarding", dest="forwarding", action="store_false")
    ns = p.parse_args(argv)
    if ns.quick:
        ns.n = min(ns.n, 40)
        ns.ghdl_subset = min(ns.ghdl_subset, 8)
    ghdl = rtl_run.find_ghdl(ns.ghdl)
    workdir = Path(ns.workdir)
    progdir = workdir / "programs"
    progdir.mkdir(parents=True, exist_ok=True)

    # Deterministic case list: one generated program per seed, mode selected by
    # the case index exactly as in Phase 5A, plus curated programs.
    generated: List[Tuple[str, Path, int]] = []
    gen_sources: List[str] = []
    gen_modes: List[str] = []
    for i in range(ns.n):
        seed = ns.seed_base + i
        mode = ns.mode if ns.mode else MODES[i % len(MODES)]
        gp = random_programs.generate(seed, mode)
        path = progdir / ("seed_%04d_%s.s" % (seed, mode))
        path.write_text(gp.source, encoding="utf-8")
        generated.append((("fuzz_%04d_%s" % (seed, mode)), path, 10))
        gen_sources.append(gp.source)
        gen_modes.append(mode)

    rep = diversity_report(gen_sources, gen_modes)
    errs = check_diversity(rep)
    print("diversity: %d/%d unique (%.1f%%)" % (rep["unique"], rep["total"], rep["pct"]))
    for row in rep["per_mode"]:
        print("  %-14s %3d/%3d (%.1f%%)" % (row["mode"], row["unique"],
                                            row["total"], row["pct"]))
    if errs:
        for e in errs:
            print("DIVERSITY ASSERTION FAILED: " + e)
        return 1

    cases = list(generated)
    for name, src, uart_cycles in CURATED:
        path = progdir / ("curated_%s.s" % name)
        path.write_text(src, encoding="utf-8")
        cases.append(("curated_" + name, path, uart_cycles))
    random_names = {c[0] for c in generated}

    results = []
    cov_all = {k: 0 for k in COV_KEYS}
    cov_random = {k: 0 for k in COV_KEYS}
    failed = 0
    for idx, (name, path, uart_cycles) in enumerate(cases):
        res = rtl_run.run_case(
            [path], name=name, sim=ns.sim, runtime=True,
            workdir=workdir, max_cycles=ns.max_cycles,
            uart_cycles=uart_cycles, expected=None, ghdl=ghdl,
            gpio_schedule=Path(ns.gpio_schedule) if ns.gpio_schedule else None,
            rebuild=ns.rebuild, forwarding=ns.forwarding,
            ignore_cycles=not ns.check_cycles)
        results.append(res)
        if res.get("error"):
            print("FAIL %-34s %-4s %s" % (name, ns.sim, res["error"]))
            failed += 1
            if ns.stop_on_fail:
                break
            continue
        # Per-case coverage so the random-only table excludes curated cases.
        case_cov = {k: 0 for k in COV_KEYS}
        add_core_cov(case_cov, res.get("rtl_cov", []))
        for k, v in analyze_trace(Path(res["trace_path"])).items():
            case_cov[k] += v
        for k, v in case_cov.items():
            cov_all[k] += v
            if name in random_names:
                cov_random[k] += v
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
            print("  failing program saved in %s (case=%s)" % (bad, name))
            if ns.stop_on_fail:
                break
        else:
            print("PASS %-34s %-4s cycles=%-6s stalls=%-5s" %
                  (name, ns.sim, res.get("rtl_cycles"), res.get("rtl_stalls")))

    print()
    print("fuzz: %d cases, %d failed, simulator=%s" % (len(results), failed, ns.sim))
    print_cov("coverage table (all cases):", cov_all)
    print()
    print_cov("coverage table (random only):", cov_random)

    # Phase 5B Step 1c: the full run must exercise every counter and the
    # store->load distance counters from random programs only.
    if ns.n >= 300 and not ns.no_cov_assert:
        checked = [k for k in COV_KEYS
                   if not (ns.forwarding and k in EXPECTED_ZERO_FWD)]
        zero = [k for k in checked if cov_random[k] == 0]
        low = [k for k in checked if 0 < cov_random[k] < 20]
        stores = {k: cov_random[k] for k in
                  ("store_load_d1", "store_load_d2", "store_load_d3")}
        low_stores = [k for k, v in stores.items() if v < 50]
        if zero or low or low_stores:
            print("RANDOM-ONLY COVERAGE GAP:")
            if zero:
                print("  zero counters: %s" % ", ".join(zero))
            if low:
                print("  counters < 20: %s" % ", ".join(
                    "%s=%d" % (k, cov_random[k]) for k in low))
            if low_stores:
                print("  store_load counters < 50: %s" % ", ".join(
                    "%s=%d" % (k, cov_random[k]) for k in low_stores))
            return 1

    if ns.ghdl_subset > 0 and ns.sim == "nvc":
        subset = generated[::max(1, len(generated) // ns.ghdl_subset)][:ns.ghdl_subset]
        print("GHDL subset: %d programs" % len(subset))
        gfail = 0
        for name, path, uart_cycles in subset:
            res = rtl_run.run_case(
                [path], name=name + "_ghdl", sim="ghdl", runtime=True,
                workdir=workdir, max_cycles=ns.max_cycles,
                uart_cycles=uart_cycles, expected=None, ghdl=ghdl,
            gpio_schedule=Path(ns.gpio_schedule) if ns.gpio_schedule else None,
                rebuild=ns.rebuild, forwarding=ns.forwarding,
                ignore_cycles=not ns.check_cycles)
            if not res.get("ok"):
                gfail += 1
                rtl_run.print_result(res)
        print("GHDL subset: %d cases, %d failed" % (len(subset), gfail))
        if gfail:
            return 1

    if ns.minimize:
        print("minimizer: failing programs and both traces are saved for manual reduction")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
