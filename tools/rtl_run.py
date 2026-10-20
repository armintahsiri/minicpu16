#!/usr/bin/env python3
"""Phase 5A differential runner: assemble -> golden model -> RTL -> trace diff.

Usage:
  python3 tools/rtl_run.py tests/directed/a_store_load_d1.asm --no-runtime
  python3 tools/rtl_run.py tests/reference_traces/01_straight.asm --no-runtime
  python3 tools/rtl_run.py programs/bubble_sort.s --runtime
  python3 tools/rtl_run.py prog.s --sim ghdl --uart-cycles 10

The default is an architectural comparison (retire cycles ignored, as required
for the stall-only Phase 5A machine).  --check-cycles compares the retire-cycle
column too and is only meaningful for Phase 5B.

Exit status: 0 match, 1 mismatch/error, 2 tool/configuration error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import assembler  # noqa: E402
import isa_sim  # noqa: E402
import trace_diff  # noqa: E402
import disassembler  # noqa: E402

RUNTIME = [
    "programs/runtime/crt0.s",
    "programs/runtime/arith.s",
    "programs/runtime/string.s",
]

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
    "tb/cpu_tb.vhd",
]

GHDL_CANDIDATES = [
    "ghdl",
    str(Path.home() / "tools" / "ghdl" / "bin" / "ghdl"),
    "/tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl",
]


def find_ghdl(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
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


def sha256_files(paths: Sequence[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(str(p).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def sim_files(rtl_dir: Path) -> List[Path]:
    return [rtl_dir / f for f in RTL_FILES]


def read_text_sources(paths: Sequence[Path]) -> List[Tuple[str, str]]:
    return [(str(p), p.read_text(encoding="utf-8")) for p in paths]


def assemble(
    sources: Sequence[Path],
    runtime: bool,
    instr_words: int,
    ram_bytes: int,
) -> assembler.Image:
    files: List[Tuple[str, str]] = []
    if runtime:
        for rel in RUNTIME:
            p = ROOT / rel
            files.append((rel, p.read_text(encoding="utf-8")))
    for p in sources:
        files.append((str(p), p.read_text(encoding="utf-8")))
    return assembler.assemble_sources(files, instr_words=instr_words, ram_bytes=ram_bytes)


def parse_state(path: Path) -> Dict[str, object]:
    d: Dict[str, object] = {"regs": {}, "uart": [], "cov": []}
    if not path.exists():
        raise RuntimeError("RTL state file missing: %s" % path)
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        parts = raw.split()
        key = parts[0]
        if key == "reg":
            d["regs"][int(parts[1])] = int(parts[2], 16)
        elif key == "uart":
            d["uart"] = [int(x) for x in parts[1:]]
        elif key == "ram":
            d["ram"] = parts[1]
        elif key == "cov":
            d["cov"] = [int(x) for x in parts[1:]]
        else:
            d[key] = parts[1]
    for k in ("cycles", "instructions", "stalls", "flushes"):
        if k in d:
            d[k] = int(d[k])
    return d


def parse_pipe(path: Path) -> List[List[str]]:
    rows: List[List[str]] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) != 11:
            raise ValueError("%s: expected 11 pipeview fields, got %d" % (path, len(parts)))
        rows.append(parts)
    return rows


PIPE_NAMES = ["cycle", "id_v", "id_pc", "ex_v", "ex_pc", "mem_v", "mem_pc",
              "wb_v", "wb_pc", "stall", "flush"]


def diff_pipe(gold: List[List[str]], rtl: List[List[str]]) -> Tuple[bool, str]:
    n = max(len(gold), len(rtl))
    for i in range(n):
        if i >= len(gold):
            return False, "rtl pipeview has an extra cycle %s" % (rtl[i][0],)
        if i >= len(rtl):
            return False, "rtl pipeview ended at cycle %s; model has %s" % (
                len(rtl), len(gold))
        # PC is only architecturally meaningful while the stage is valid.  When
        # both sides agree the stage is empty, a stale PC in the RTL register is
        # irrelevant (the model clears it).
        pc_pairs = [(1, 2), (3, 4), (5, 6), (7, 8)]
        skip = set()
        for v_idx, pc_idx in pc_pairs:
            if gold[i][v_idx] == "0" and rtl[i][v_idx] == "0":
                skip.add(pc_idx)
        diffs = ["%s model=%s rtl=%s" % (PIPE_NAMES[k], gold[i][k], rtl[i][k])
                 for k in range(11) if k not in skip and gold[i][k] != rtl[i][k]]
        if diffs:
            return False, "first pipeview divergence at line %d (cycle %s): %s" % (
                i + 1, gold[i][0], "; ".join(diffs))
    return True, "pipeview identical over %d cycles" % len(gold)


def parse_expected(path: Path) -> Dict[str, object]:
    d: Dict[str, object] = {"regs": {}, "ram": []}
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        key = parts[0]
        if key == "reg":
            d["regs"][int(parts[1])] = int(parts[2], 16)
        elif key == "pc":
            d["pc"] = int(parts[1], 16)
        elif key == "gpio":
            d["gpio_out"] = int(parts[1], 16)
        elif key == "uart":
            d["uart"] = [int(x, 16) for x in parts[1:]]
        elif key == "ram":
            d["ram"].append((int(parts[1], 16), parts[2]))
        elif key == "halted":
            d["halted"] = parts[1] == "1"
        elif key == "instructions":
            d["instructions"] = int(parts[1])
        elif key == "cycles":
            d["cycles"] = int(parts[1])
        elif key == "stalls":
            d["stalls"] = int(parts[1])
        else:
            raise ValueError("bad expected line: %r" % s)
    return d


def compare_expected(exp: Dict[str, object], got: Dict[str, object],
                    ignore_cycles: bool = False) -> List[str]:
    errs: List[str] = []
    for reg, want in exp.get("regs", {}).items():
        g = got["regs"].get(reg, 0)
        if g != want:
            errs.append("r%d: got 0x%04X want 0x%04X" % (reg, g, want))
    if "pc" in exp and int(str(got.get("pc")), 16) != exp["pc"]:
        errs.append("pc: got %s want 0x%04X" % (got.get("pc"), exp["pc"]))
    if "gpio_out" in exp and int(str(got.get("gpio_out")), 16) != exp["gpio_out"]:
        errs.append("gpio_out: got %s want 0x%04X" % (got.get("gpio_out"), exp["gpio_out"]))
    if "uart" in exp and list(got.get("uart", [])) != list(exp["uart"]):
        errs.append("uart: got %r want %r" % (got.get("uart"), exp["uart"]))
    if "halted" in exp and bool(got.get("halted")) != bool(exp["halted"]):
        errs.append("halted: got %r want %r" % (got.get("halted"), exp["halted"]))
    if "instructions" in exp and int(got.get("instructions", -1)) != exp["instructions"]:
        errs.append("instructions: got %r want %r" % (got.get("instructions"), exp["instructions"]))
    # Cycle and stall expectations are contract-timing assertions.  The
    # architectural (stall-only) comparison deliberately ignores timing, so the
    # expected file's cycles/stalls lines are not asserted in that mode; the
    # cycle-exact run asserts them.
    if not ignore_cycles:
        if "cycles" in exp and int(got.get("cycles", -1)) != exp["cycles"]:
            errs.append("cycles: got %r want %r" % (got.get("cycles"), exp["cycles"]))
        if "stalls" in exp and int(got.get("stalls", -1)) != exp["stalls"]:
            errs.append("stalls: got %r want %r" % (got.get("stalls"), exp["stalls"]))
    ram = str(got.get("ram", ""))
    for addr, hx in exp.get("ram", []):
        off = addr * 2
        seg = ram[off:off + len(hx)]
        if seg.upper() != hx.upper():
            errs.append("ram[0x%04X]: got %s want %s" % (addr, seg, hx))
    return errs


def _run(cmd: List[str], cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=timeout)


def ensure_built(rtl_dir: Path, sim: str, work: Path, ghdl: str,
                 rebuild: bool = False) -> None:
    files = sim_files(rtl_dir)
    for f in files:
        if not f.exists():
            raise RuntimeError("missing RTL file: %s" % f)
    work.mkdir(parents=True, exist_ok=True)
    stamp = work / "rtl.stamp"
    digest = sha256_files(files) + ":" + sim
    if not rebuild and stamp.exists() and stamp.read_text().strip() == digest:
        return
    for child in work.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    abs_files = [str(f.resolve()) for f in files]
    if sim == "nvc":
        cmd = ["nvc", "--std=08", "-L", str(work.resolve()), "-a"] + abs_files
        r = _run(cmd, work)
        if r.returncode != 0:
            raise RuntimeError("NVC analyse failed:\n" + r.stdout)
    elif sim == "ghdl":
        cmd = [ghdl, "-a", "--std=08", "--workdir=" + str(work.resolve())] + abs_files
        r = _run(cmd, work)
        if r.returncode != 0:
            raise RuntimeError("GHDL analyse failed:\n" + r.stdout)
        cmd = [ghdl, "-e", "--std=08",
               "--workdir=" + str(work.resolve()), "tb_cpu"]
        r = _run(cmd, work)
        if r.returncode != 0:
            raise RuntimeError("GHDL elaborate failed:\n" + r.stdout)
    else:
        raise ValueError("unknown simulator %r" % sim)
    stamp.write_text(digest + "\n")


def run_rtl(
    sim: str,
    rtl_dir: Path,
    work: Path,
    ghdl: str,
    hex_path: Path,
    data_path: Path,
    trace_path: Path,
    state_path: Path,
    pipe_path: Path,
    instr_words: int,
    ram_bytes: int,
    uart_cycles: int,
    max_cycles: int,
    forwarding: bool,
    gpio_in: int,
    gpio_schedule: Optional[Path] = None,
    rebuild: bool = False,
) -> Dict[str, object]:
    ensure_built(rtl_dir, sim, work, ghdl, rebuild=rebuild)
    generics = [
        "-gPROGRAM_HEX=" + str(hex_path.resolve()),
        "-gDATA_HEX=" + str(data_path.resolve()),
        "-gTRACE_OUT=" + str(trace_path.resolve()),
        "-gSTATE_OUT=" + str(state_path.resolve()),
        "-gPIPE_OUT=" + str(pipe_path.resolve()),
        "-gMAX_CYCLES=" + str(max_cycles),
        "-gFORWARDING=" + ("true" if forwarding else "false"),
        "-gUART_CYCLES_PER_BYTE=" + str(uart_cycles),
        "-gINSTR_WORDS=" + str(instr_words),
        "-gRAM_BYTES=" + str(ram_bytes),
        "-gGPIO_IN_VALUE=" + str(gpio_in),
        "-gCOUNTING=true",
    ]
    if gpio_schedule is not None:
        generics.append("-gGPIO_SCHEDULE=" + str(Path(gpio_schedule).resolve()))
    t0 = time.time()
    if sim == "nvc":
        cmd = ["nvc", "--std=08", "-L", str(work.resolve()), "-e", "tb_cpu"] + generics
        r = _run(cmd, work)
        if r.returncode != 0:
            raise RuntimeError("NVC elaborate failed:\n" + r.stdout)
        cmd = ["nvc", "--std=08", "-L", str(work.resolve()), "-r", "tb_cpu"]
        r = _run(cmd, work)
    else:
        cmd = [ghdl, "-r", "--std=08",
               "--workdir=" + str(work.resolve()), "tb_cpu"] + generics + [
               "--max-stack-alloc=8192"]
        r = _run(cmd, work)
    elapsed = time.time() - t0
    if r.returncode != 0:
        raise RuntimeError("%s simulation failed (exit %d):\n%s" %
                           (sim, r.returncode, r.stdout))
    return {"sim": sim, "elapsed_s": round(elapsed, 3), "log": r.stdout}


def run_case(
    sources: Sequence[Path],
    name: Optional[str] = None,
    sim: str = "nvc",
    rtl_dir: Optional[Path] = None,
    runtime: bool = False,
    workdir: Optional[Path] = None,
    instr_words: int = 2048,
    ram_bytes: int = 4096,
    uart_cycles: int = 10,
    max_cycles: int = 200000,
    forwarding: bool = False,
    gpio_in: int = 0,
    gpio_schedule: Optional[Path] = None,
    ignore_cycles: bool = True,
    compare_pipe: bool = True,
    expected: Optional[Path] = None,
    ghdl: Optional[str] = None,
    rebuild: bool = False,
    keep: bool = False,
) -> Dict[str, object]:
    rtl_dir = Path(rtl_dir) if rtl_dir else ROOT / "rtl"
    name = name or Path(sources[0]).stem
    workroot = Path(workdir) if workdir else ROOT / "build" / "rtl"
    case_dir = workroot / (name + "_" + sim)
    if case_dir.exists() and not keep:
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    hex_path = case_dir / (name + ".hex")
    data_path = case_dir / (name + ".data.hex")
    gold_trace = case_dir / "golden.trace"
    gold_json = case_dir / "golden.json"
    rtl_trace = case_dir / "rtl.trace"
    rtl_state = case_dir / "rtl.state"
    rtl_pipe = case_dir / "rtl.pipe"
    gold_pipe = case_dir / "golden.pipe"
    ghdl = find_ghdl(ghdl)

    gpio_sched = None
    if gpio_schedule is not None:
        gpio_sched = isa_sim.load_gpio_schedule(str(gpio_schedule))

    img = assemble(sources, runtime, instr_words, ram_bytes)
    hex_path.write_text(img.hex_text(), encoding="utf-8")
    data_path.write_text(img.data_hex_text(), encoding="utf-8")

    cfg = isa_sim.Config(
        instr_words=instr_words,
        ram_bytes=ram_bytes,
        uart_cycles_per_byte=uart_cycles,
        gpio_in=(gpio_sched if gpio_sched is not None else gpio_in),
        max_cycles=max_cycles,
        ram_init=img.data,
    )
    try:
        gold = isa_sim.run_pipeline(img.instr_words, cfg)
    except Exception as e:  # noqa: BLE001 - a non-terminating program is a result
        out["error"] = "golden model: %s" % e
        return out
    gold_trace.write_text(gold.trace_text(), encoding="utf-8")
    gold_json.write_text(gold.to_json() + "\n", encoding="utf-8")
    gold_pipe.write_text(gold.pipe_text(), encoding="utf-8")

    out: Dict[str, object] = {
        "name": name,
        "sim": sim,
        "sources": [str(p) for p in sources],
        "instr_words": instr_words,
        "ram_bytes": ram_bytes,
        "uart_cycles": uart_cycles,
        "gold_cycles": gold.cycles,
        "gold_instructions": gold.instructions,
        "gold_stalls": gold.stalls,
        "gold_flushes": gold.flushes,
        "trace_path": str(rtl_trace),
        "state_path": str(rtl_state),
        "pipe_path": str(rtl_pipe),
        "ok": False,
    }
    try:
        run_rtl(sim, rtl_dir, workroot / (sim + "_lib"), ghdl, hex_path, data_path,
                rtl_trace, rtl_state, rtl_pipe, instr_words, ram_bytes, uart_cycles,
                max_cycles, forwarding, gpio_in, gpio_schedule, rebuild=rebuild)
    except Exception as e:  # noqa: BLE001 - report, do not crash the suite
        out["error"] = str(e)
        return out

    state = parse_state(rtl_state)
    out["rtl_cycles"] = state.get("cycles")
    out["rtl_instructions"] = state.get("instructions")
    out["rtl_stalls"] = state.get("stalls")
    out["rtl_flushes"] = state.get("flushes")
    out["rtl_cov"] = state.get("cov", [])
    out["rtl_uart"] = state.get("uart", [])
    out["rtl_gpio_out"] = state.get("gpio_out")
    out["rtl_pc"] = state.get("pc")

    # Final-state comparison (architectural fields; cycles/stalls intentionally
    # excluded in Phase 5A).
    state_errors: List[str] = []
    gregs = [int(x, 16) for x in json.loads(gold_json.read_text())["regs"]]
    rregs = [state["regs"].get(i, 0) for i in range(16)]
    if gregs != rregs:
        state_errors.append("registers: golden=%s rtl=%s" %
                            (["%04X" % x for x in gregs], ["%04X" % x for x in rregs]))
    g = json.loads(gold_json.read_text())
    for key, rkey in (("pc", "pc"), ("gpio_out", "gpio_out")):
        if int(g[key], 16) != int(str(state[rkey]), 16):
            state_errors.append("%s: golden=%s rtl=%s" % (key, g[key], state[rkey]))
    if list(g["uart"]) != list(state["uart"]):
        state_errors.append("uart: golden=%r rtl=%r" % (g["uart"], state["uart"]))
    if int(g["instructions"]) != int(state["instructions"]):
        state_errors.append("instructions: golden=%d rtl=%d" %
                            (g["instructions"], state["instructions"]))
    if bool(g["halted"]) != (state.get("halted") == "1"):
        state_errors.append("halted: golden=%r rtl=%r" % (g["halted"], state.get("halted")))
    if str(g["ram_hex"]).upper() != str(state.get("ram", "")).upper():
        # Find the first differing byte for a useful message.
        a = str(g["ram_hex"]).lower()
        b = str(state.get("ram", "")).lower()
        idx = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
        state_errors.append("ram differs at byte %s (golden=%s rtl=%s)" %
                            (idx // 2 if idx is not None else "length",
                             a[idx:idx + 4] if idx is not None else a[:8],
                             b[idx:idx + 4] if idx is not None else b[:8]))
    if int(g["flushes"]) != int(state.get("flushes", -1)):
        state_errors.append("flushes: golden=%d rtl=%s" % (g["flushes"], state.get("flushes")))
    if not ignore_cycles:
        if int(state.get("cycles", -1)) != int(gold.cycles):
            state_errors.append("cycles: golden=%d rtl=%s" %
                                (gold.cycles, state.get("cycles")))
        if int(state.get("stalls", -1)) != int(gold.stalls):
            state_errors.append("stalls: golden=%d rtl=%s" %
                                (gold.stalls, state.get("stalls")))
    out["state_errors"] = state_errors

    pipe_ok = True
    pipe_msg = "pipeview not compared"
    if compare_pipe and not ignore_cycles and rtl_pipe.exists() and gold_pipe.exists():
        try:
            pipe_ok, pipe_msg = diff_pipe(parse_pipe(gold_pipe), parse_pipe(rtl_pipe))
        except Exception as e:  # noqa: BLE001
            pipe_ok, pipe_msg = False, "pipeview parse error: %s" % e
    out["pipe_ok"] = pipe_ok
    out["pipe_message"] = pipe_msg

    gt = trace_diff.parse_trace(str(gold_trace))
    rt = trace_diff.parse_trace(str(rtl_trace))
    ok_trace, msg = trace_diff.diff_traces(gt, rt, ignore_cycles=ignore_cycles, context=3)
    out["trace_ok"] = ok_trace
    out["trace_message"] = msg
    out["gold_trace_records"] = len(gt)
    out["rtl_trace_records"] = len(rt)
    if not ok_trace:
        # Add disassembly context for the first divergence.
        div = next((i for i in range(min(len(gt), len(rt)))
                    if gt[i].key(ignore_cycles) != rt[i].key(ignore_cycles)), None)
        if div is not None:
            gi = int(gt[div].instr, 16)
            ri = int(rt[div].instr, 16)
            out["divergence"] = {
                "record": div + 1,
                "golden_pc": gt[div].pc,
                "golden_instr": gt[div].instr,
                "golden_disasm": disassembler.decode_word(gi, int(gt[div].pc, 16)),
                "rtl_pc": rt[div].pc,
                "rtl_instr": rt[div].instr,
                "rtl_disasm": disassembler.decode_word(ri, int(rt[div].pc, 16)),
            }

    exp_errors: List[str] = []
    if expected is not None:
        exp_errors = compare_expected(parse_expected(Path(expected)), state,
                                      ignore_cycles=ignore_cycles)
        out["expected_errors"] = exp_errors
    out["ok"] = bool(ok_trace and not state_errors and not exp_errors and pipe_ok)
    return out


def print_result(res: Dict[str, object]) -> None:
    name = res.get("name")
    sim = res.get("sim")
    if res.get("error"):
        print("FAIL %-28s %-4s  %s" % (name, sim, res["error"]))
        return
    status = "PASS" if res.get("ok") else "FAIL"
    print("%s %-28s %-4s  cycles=%-6s (gold %-6s) stalls=%-5s trace=%d/%d" %
          (status, name, sim, res.get("rtl_cycles"), res.get("gold_cycles"),
           res.get("rtl_stalls"), res.get("rtl_trace_records", 0),
           res.get("gold_trace_records", 0)))
    if not res.get("ok"):
        if res.get("state_errors"):
            for e in res["state_errors"]:
                print("  state: " + e)
        if res.get("expected_errors"):
            for e in res["expected_errors"]:
                print("  expected: " + e)
        if not res.get("pipe_ok", True):
            print("  pipeview: " + str(res.get("pipe_message")))
        if not res.get("trace_ok"):
            print("  " + str(res.get("trace_message")).replace("\n", "\n  "))
            div = res.get("divergence")
            if div:
                print("  first divergence: record %d" % div["record"])
                print("    golden: pc=%s instr=%s  %s" %
                      (div["golden_pc"], div["golden_instr"], div["golden_disasm"]))
                print("    rtl   : pc=%s instr=%s  %s" %
                      (div["rtl_pc"], div["rtl_instr"], div["rtl_disasm"]))


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="MiniCPU-16 RTL differential runner")
    p.add_argument("sources", nargs="+", help="assembly source file(s)")
    p.add_argument("--name", help="case name (default: first source stem)")
    p.add_argument("--sim", choices=["nvc", "ghdl"], default="nvc")
    p.add_argument("--rtl-dir", default=str(ROOT / "rtl"))
    p.add_argument("--workdir", default=str(ROOT / "build" / "rtl"))
    p.add_argument("--runtime", action="store_true", help="prepend crt0/arith/string")
    p.add_argument("--no-runtime", action="store_true", help="do not prepend the runtime (default)")
    p.add_argument("--instr-words", type=lambda s: int(s, 0), default=2048)
    p.add_argument("--ram-bytes", type=lambda s: int(s, 0), default=4096)
    p.add_argument("--uart-cycles", type=int, default=10)
    p.add_argument("--max-cycles", type=int, default=200000)
    p.add_argument("--forwarding", dest="forwarding", action="store_true", default=True)
    p.add_argument("--no-forwarding", dest="forwarding", action="store_false")
    p.add_argument("--gpio-in", type=lambda s: int(s, 0), default=0)
    p.add_argument("--gpio-schedule", help="GPIO_IN cycle/value schedule file")
    p.add_argument("--no-pipe", dest="compare_pipe", action="store_false", default=True)
    p.add_argument("--check-cycles", action="store_true",
                   help="also compare retire cycles (5B only)")
    p.add_argument("--expected", help="hand-computed expected final-state file")
    p.add_argument("--ghdl", help="GHDL executable")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--keep", action="store_true")
    p.add_argument("--json", help="write a JSON result")
    ns = p.parse_args(argv)
    try:
        res = run_case(
            [Path(x) for x in ns.sources], name=ns.name, sim=ns.sim,
            rtl_dir=Path(ns.rtl_dir), runtime=ns.runtime, workdir=Path(ns.workdir),
            instr_words=ns.instr_words, ram_bytes=ns.ram_bytes,
            uart_cycles=ns.uart_cycles, max_cycles=ns.max_cycles,
            forwarding=ns.forwarding, gpio_in=ns.gpio_in,
            gpio_schedule=Path(ns.gpio_schedule) if ns.gpio_schedule else None,
            ignore_cycles=not ns.check_cycles, compare_pipe=ns.compare_pipe,
            expected=Path(ns.expected) if ns.expected else None,
            ghdl=ns.ghdl, rebuild=ns.rebuild, keep=ns.keep)
    except Exception as e:  # noqa: BLE001
        print("rtl_run: %s" % e)
        return 2
    print_result(res)
    if ns.json:
        Path(ns.json).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
