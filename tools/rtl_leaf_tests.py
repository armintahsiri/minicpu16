#!/usr/bin/env python3
"""Run every self-checking leaf testbench under NVC and/or GHDL.

Each testbench reports a single line "PASS <name> checks=<n>" and exits with
status 0.  A nonzero simulator status, a missing PASS line, or a watchdog
timeout is a failure and makes this script exit nonzero.
"""

from __future__ import annotations

import argparse
from functools import partial
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl"
TB = RTL / "tb"

print = partial(print, flush=True)

RTL_FILES = [
    "minicpu16_pkg.vhd",
    "shifter.vhd",
    "alu.vhd",
    "branch_unit.vhd",
    "register_file.vhd",
    "decoder.vhd",
    "hazard_unit.vhd",
    "forwarding_unit.vhd",
    "imem.vhd",
    "dmem.vhd",
    "mmio.vhd",
    "minicpu16_core.vhd",
]

TB_UTIL = "tb_util.vhd"

TESTS: List[Tuple[str, str, str]] = [
    # file, entity, description
    ("tb_shifter.vhd", "tb_shifter", "SLL/SRL/SRA exhaustive 65536x16x3"),
    ("tb_alu.vhd", "tb_alu", "ALU edge cross product + 200k random"),
    ("tb_branch_unit.vhd", "tb_branch_unit", "branches edge + 100k random"),
    ("tb_register_file.vhd", "tb_register_file", "RF R0/write-through/reset/random"),
    ("tb_imem.vhd", "tb_imem", "imem latency/range/HALT"),
    ("tb_dmem.vhd", "tb_dmem", "dmem all types/alignments/boundaries"),
    ("tb_mmio.vhd", "tb_mmio", "MMIO all registers/byte rules/UART drop"),
    ("tb_decoder.vhd", "tb_decoder", "decoder all opcodes + 50k random"),
    ("tb_hazard.vhd", "tb_hazard", "hazard 5A/5B directed + 100k random"),
    ("tb_forwarding.vhd", "tb_forwarding", "forwarding directed + 100k random"),
    ("tb_reset.vhd", "tb_reset", "warm reset preservation and no writes"),
]

# Same entity, different generics, so each run gets its own work directory.
EXTRA_TESTS: List[Tuple[str, str, str, Sequence[str], str]] = [
    ("tb_reset_midrun.vhd", "tb_reset_midrun", "mid-run reset, 1-cycle", ("-gRESET_LEN=1",), "_len1"),
    ("tb_reset_midrun.vhd", "tb_reset_midrun", "mid-run reset, 3-cycle", ("-gRESET_LEN=3",), "_len3"),
]

# Search order for the second simulator, kept in step with the Makefile:
# the permanent local install under $HOME/tools first, then PATH (what CI
# uses), and only then the ephemeral /tmp copy.
GHDL_CANDIDATES = [
    str(Path.home() / "tools" / "ghdl" / "bin" / "ghdl"),
    "ghdl",
    "/tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl",
]


def find_ghdl(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    # The GHDL environment variable wins: the Makefile exports it so that
    # "make GHDL=/path/to/ghdl" reaches every tool.
    env = os.environ.get("GHDL")
    if env:
        return env
    for c in GHDL_CANDIDATES:
        if c == "ghdl":
            found = shutil.which("ghdl")
            if found:
                return found
        elif Path(c).is_file() and os.access(c, os.X_OK):
            return c
    return "ghdl"


def run(cmd: List[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=timeout)


def run_one(sim: str, tb_file: str, entity: str, ghdl: str, workroot: Path,
            rebuild: bool, rtl_dir: Path = RTL,
            generics: Sequence[str] = (), tag: str = "") -> Dict[str, object]:
    work = workroot / sim / (entity + tag)
    if rebuild and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    tb_dir = rtl_dir / "tb"
    files = [rtl_dir / f for f in RTL_FILES] + [tb_dir / TB_UTIL, tb_dir / tb_file]
    files = [f.resolve() for f in files]
    t0 = time.time()
    if sim == "nvc":
        r = run(["nvc", "--std=08", "-L", ".", "-a"] + [str(f) for f in files], work)
        if r.returncode == 0:
            r = run(["nvc", "--std=08", "-L", ".", "-e", entity] + list(generics), work)
        if r.returncode == 0:
            r = run(["nvc", "--std=08", "-L", ".", "-r", entity], work)
    else:
        r = run([ghdl, "-a", "--std=08", "--workdir=.", ] + [str(f) for f in files], work)
        if r.returncode == 0:
            r = run([ghdl, "-e", "--std=08", "--workdir=.", entity], work)
        if r.returncode == 0:
            r = run([ghdl, "-r", "--std=08", "--workdir=.", entity] + list(generics), work)
    elapsed = time.time() - t0
    out = r.stdout
    m = re.search(r"PASS\s+\S+\s+checks=(\d+)", out)
    ok = r.returncode == 0 and m is not None
    return {
        "sim": sim, "entity": entity, "ok": ok,
        "checks": int(m.group(1)) if m else 0,
        "elapsed_s": round(elapsed, 2),
        "output": out,
        "returncode": r.returncode,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run leaf RTL testbenches")
    p.add_argument("--sim", choices=["nvc", "ghdl", "both"], default="both")
    p.add_argument("--filter", default=None)
    p.add_argument("--ghdl", default=None)
    p.add_argument("--workdir", default=str(ROOT / "build" / "rtl_leaf"))
    p.add_argument("--rtl-dir", default=str(RTL))
    p.add_argument("--rebuild", action="store_true")
    ns = p.parse_args(argv)
    ghdl = find_ghdl(ns.ghdl)
    sims = ["nvc", "ghdl"] if ns.sim == "both" else [ns.sim]
    workroot = Path(ns.workdir)
    failed = 0
    results: List[Dict[str, object]] = []
    for tf, ent, desc in TESTS:
        if ns.filter and ns.filter not in ent:
            continue
        for sim in sims:
            started = time.monotonic()
            print("START %s %s" % (ent, sim))
            try:
                res = run_one(sim, tf, ent, ghdl, workroot, ns.rebuild, Path(ns.rtl_dir))
            except subprocess.TimeoutExpired:
                res = {"sim": sim, "entity": ent, "ok": False, "checks": 0,
                       "elapsed_s": time.monotonic() - started,
                       "output": "TIMEOUT", "returncode": -1}
            results.append(res)
            status = "PASS" if res["ok"] else "FAIL"
            elapsed = time.monotonic() - started
            print("%s %s %s %.2fs" % (status, ent, sim, elapsed))
            print("%s %-18s %-4s checks=%-9d %6.2fs" %
                  (status, ent, sim, res["checks"], elapsed))
            if not res["ok"]:
                failed += 1
                tail = "\n".join(str(res["output"]).splitlines()[-12:])
                print(tail)
    for tf, ent, desc, gens, tag in EXTRA_TESTS:
        if ns.filter and ns.filter not in ent:
            continue
        for sim in sims:
            started = time.monotonic()
            print("START %s %s" % (ent + tag, sim))
            try:
                res = run_one(sim, tf, ent, ghdl, workroot, ns.rebuild,
                              Path(ns.rtl_dir), gens, tag)
            except subprocess.TimeoutExpired:
                res = {"sim": sim, "entity": ent, "ok": False, "checks": 0,
                       "elapsed_s": time.monotonic() - started,
                       "output": "TIMEOUT", "returncode": -1}
            results.append(res)
            status = "PASS" if res["ok"] else "FAIL"
            elapsed = time.monotonic() - started
            print("%s %s %s %.2fs" % (status, ent + tag, sim, elapsed))
            print("%s %-18s %-4s checks=%-9d %6.2fs  %s" %
                  (status, ent + tag, sim, res["checks"], elapsed, desc))
            if not res["ok"]:
                failed += 1
                tail = "\n".join(str(res["output"]).splitlines()[-14:])
                print(tail)
    total = sum(int(r["checks"]) for r in results)
    print("leaf testbenches: %d runs, %d failed, %d checks" % (len(results), failed, total))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
