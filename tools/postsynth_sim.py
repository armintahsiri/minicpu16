#!/usr/bin/env python3
"""Optional Phase 5B Step 10: post-synthesis (gate-level) simulation.

This is deliberately small and optional.  For each of three programs it:

  1. assembles a self-contained program,
  2. wraps `minicpu16_core` in a test wrapper that exposes gpio_out/pc_out,
     the whole retirement interface (pc, instr, rd, value, mem kind/addr/
     size/data, taken, target, halt) and the pipeview interface,
  3. runs Yosys (ghdl-yosys-plugin) through `synth_ice40`-equivalent generic
     technology mapping (`proc; opt; memory; opt; techmap; opt`),
  4. executes the mapped netlist with the built-in Yosys `sim` command and
     writes a VCD,
  5. reconstructs the retirement trace and the pipeview from the VCD and
     compares them, record by record and cycle by cycle, against the golden
     timing layer's own retirement trace and pipeview for the same program;
     the final gpio_out and the HALT are compared as well.

Why generic technology mapping rather than the iCE40 cell netlist: the open
`cells_sim.v` model of `SB_RAM40_4K` only loads `INIT_0` at time zero and the
Yosys `sim` flow does not apply the per-instance `INIT_*` parameters, so the
inferred instruction ROM reads as zero and the CORE fetches HALT' (the netlist
itself carries the correct INIT_* parameters, as the synthesized JSON shows).
Mapping the memory to flip-flops/logic preserves the initialization and gives a
true gate-level run of the same logic.  Finding F31 records this.

What is compared, and what is NOT (T5):

  * COMPARED -- the full retirement trace, field by field, for every retired
    instruction of every program: pc, instruction word, rd, written value,
    memory kind/address/size/data, branch taken and target -- and the retire
    *cycle* column as well.  The golden side is the model's own retirement
    trace (`isa_sim.run_pipeline`), i.e. the same text format the RTL
    testbench writes (docs/TRACE_FORMAT.md).  The per-cycle pipeview
    (stage occupancy, stall and flush flags) is compared cycle by cycle too.
  * NOT COMPARED -- the internal `pc_out` (the fetch pointer).  Its frozen
    value after HALT is not architecturally specified (finding F3); it is
    reported only.
  * NOT COMPARED -- the final RAM contents.  The data RAM survives as 4096
    flip-flops in the generic-mapped netlist, and the VCD dumps all of them,
    but the RAM image is not part of the architectural trace; the *stores* are
    compared through the retirement trace (kind, address, size, data), which
    is what the RTL testbench itself asserts.

How the cycle numbers are recovered.  The generic-mapped netlist still carries
the core's architectural cycle counter (`cycle_count`), so the VCD contains it.
The counter is cleared by reset and incremented once per non-reset clock, and
the RTL testbench numbers its cycles so that the first cycle after reset
release is cycle 1 -- exactly the cycle in which `cycle_count` is still 0.
The gate-level cycle number of a sample is therefore `cycle_count + 1`, and
the RTL testbench and the model agree on that numbering (the `make rtl-test`
suite compares the retire-cycle column).  A cycle-exact *gate-level*
comparison is thus possible and is performed here; no approximation is made.

Standard library only.  If Yosys or the plugin is absent the tool prints SKIP
and exits 0; it is not part of `make test`.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

RTL = [
    "rtl/minicpu16_pkg.vhd", "rtl/shifter.vhd", "rtl/alu.vhd",
    "rtl/branch_unit.vhd", "rtl/register_file.vhd", "rtl/decoder.vhd",
    "rtl/hazard_unit.vhd", "rtl/forwarding_unit.vhd", "rtl/imem.vhd",
    "rtl/dmem.vhd", "rtl/mmio.vhd", "rtl/minicpu16_core.vhd",
]

# The three programs of the gate-level check.  Between them they cover ALU
# forwarding, the load-use stall, a store whose address comes from a load (the
# F32 case) and a taken branch that flushes the pipeline.
PROGRAMS: Tuple[Tuple[str, str], ...] = (
    ("j_double_producer", "tests/directed/j_double_producer.asm"),
    ("j_load_store_addr_d1", "tests/directed/j_load_store_addr_d1.asm"),
    ("j_load_branch_d1", "tests/directed/j_load_branch_d1.asm"),
)

WRAPPER = '''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.minicpu16_pkg.all;
use work.minicpu16_program_pkg.all;

entity wrap_sim is
  port (clk    : in  std_logic;
        rst    : in  std_logic;
        gpio   : out std_logic_vector(15 downto 0);
        pc     : out std_logic_vector(15 downto 0);
        halted : out std_logic;
        rv     : out std_logic;
        rpc    : out std_logic_vector(15 downto 0);
        rinstr : out std_logic_vector(31 downto 0);
        rwe    : out std_logic;
        rrd    : out std_logic_vector(3 downto 0);
        rval   : out std_logic_vector(15 downto 0);
        rkind  : out std_logic_vector(1 downto 0);
        raddr  : out std_logic_vector(15 downto 0);
        rsize  : out std_logic_vector(1 downto 0);
        rdata  : out std_logic_vector(15 downto 0);
        rtaken : out std_logic;
        rtgt   : out std_logic_vector(15 downto 0);
        pidv   : out std_logic;
        pidpc  : out std_logic_vector(15 downto 0);
        pexv   : out std_logic;
        pexpc  : out std_logic_vector(15 downto 0);
        pmemv  : out std_logic;
        pmempc : out std_logic_vector(15 downto 0);
        pwbv   : out std_logic;
        pwbpc  : out std_logic_vector(15 downto 0);
        pstall : out std_logic;
        pflush : out std_logic);
end entity;

architecture rtl of wrap_sim is
  impure function get_img return instr_image_t is
    variable r : instr_image_t(0 to 2047) := (others => (others => '0'));
  begin
    for i in 0 to PROGRAM_WORDS - 1 loop
      r(i) := INSTR_INIT(i);
    end loop;
    return r;
  end function;
  -- The data image, rebuilt from the byte banks of the program package so
  -- that a program with a .data section is simulated with its initial RAM.
  impure function get_data return byte_image_t is
    variable r : byte_image_t(0 to 4095) := (others => (others => '0'));
  begin
    for i in 0 to 2047 loop
      r(2 * i)     := DATA_EVEN(i);
      r(2 * i + 1) := DATA_ODD(i);
    end loop;
    return r;
  end function;
  constant IMG : instr_image_t(0 to 2047) := get_img;
  constant DAT : byte_image_t(0 to 4095) := get_data;
  signal uart_tx_byte_s  : std_logic_vector(7 downto 0);
  signal uart_tx_valid_s : std_logic;
  signal uart_busy_s     : std_logic;
begin
  u : entity work.minicpu16_core
    generic map (FORWARDING => {fwd}, INSTR_WORDS => 2048, RAM_BYTES => 4096,
                 UART_CYCLES_PER_BYTE => 10, COUNTING => false,
                 INSTR_INIT => IMG, DATA_INIT => DAT)
    port map (clk => clk, rst => rst, gpio_in => (others => '0'),
              uart_tx_byte => uart_tx_byte_s, uart_tx_valid => uart_tx_valid_s,
              uart_busy => uart_busy_s, gpio_out => gpio, pc_out => pc,
              retire_valid => rv, retire_pc => rpc, retire_instr => rinstr,
              retire_reg_write => rwe, retire_rd => rrd, retire_value => rval,
              retire_mem_kind => rkind, retire_mem_addr => raddr,
              retire_mem_size => rsize, retire_mem_data => rdata,
              retire_taken => rtaken, retire_target => rtgt,
              retire_halt => halted,
              pipe_id_valid => pidv, pipe_id_pc => pidpc,
              pipe_ex_valid => pexv, pipe_ex_pc => pexpc,
              pipe_mem_valid => pmemv, pipe_mem_pc => pmempc,
              pipe_wb_valid => pwbv, pipe_wb_pc => pwbpc,
              pipe_stall => pstall, pipe_flush => pflush, cov => open);
end architecture;
'''

# Signals read from the VCD.  The wrapper's own ports are the top-level names;
# `cycle_count` is an internal signal of the core instance (scope "u") and is
# the only internal one this tool relies on.
TOP_SIGNALS = (
    "clk", "rst", "gpio", "pc", "halted",
    "rv", "rpc", "rinstr", "rwe", "rrd", "rval",
    "rkind", "raddr", "rsize", "rdata", "rtaken", "rtgt",
    "pidv", "pidpc", "pexv", "pexpc", "pmemv", "pmempc",
    "pwbv", "pwbpc", "pstall", "pflush",
)
CORE_SIGNALS = ("cycle_count",)


def find_ghdl() -> str:
    # The permanent local install is preferred; PATH (what CI uses) comes next
    # and the ephemeral /tmp copy is only a fallback.  GHDL overrides all.
    cands = [os.environ.get("GHDL"),
             str(Path.home() / "tools" / "ghdl" / "bin" / "ghdl"),
             shutil.which("ghdl"),
             "/tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl"]
    for c in cands:
        if c and Path(c).is_file():
            return c
    return "ghdl"


def ghdl_prefix(ghdl: str) -> Optional[str]:
    try:
        out = subprocess.run([ghdl, "--disp-config"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True).stdout
    except OSError:
        return None
    for line in out.splitlines():
        if "library prefix:" in line:
            return line.split(":", 1)[1].strip()
    return None


def find_plugin() -> Optional[str]:
    # The permanent local install is preferred; /tmp is only a fallback because
    # it is wiped on reboot.  GHDL_YOSYS_PLUGIN overrides everything.
    cands = [os.environ.get("GHDL_YOSYS_PLUGIN"),
             str(Path.home() / "tools" / "ghdl-yosys-plugin" / "ghdl.so"),
             str(Path.home() / ".local" / "lib" / "yosys" / "ghdl.so"),
             "/tmp/ghdl-yosys-plugin/ghdl.so"]
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


def plugin_env(env: Dict[str, str], ghdl: str, plugin: str) -> Dict[str, str]:
    """Return the environment for the Yosys run.

    The ghdl-yosys-plugin links against libghdl.  When the plugin was built
    against a GHDL that now lives somewhere else, the loader cannot find the
    library; DYLD_FALLBACK_LIBRARY_PATH with the library directory of the
    GHDL in use fixes that without touching the binary.  On Linux the
    equivalent is LD_LIBRARY_PATH.
    """
    out = dict(env)
    libdirs: List[str] = []
    prefix = ghdl_prefix(ghdl)
    if prefix:
        for cand in (Path(prefix) / "lib", Path(prefix).parent / "lib"):
            if cand.is_dir():
                libdirs.append(str(cand))
    home = Path.home() / "tools" / "ghdl" / "lib"
    if home.is_dir():
        libdirs.append(str(home))
    if libdirs:
        key = "DYLD_FALLBACK_LIBRARY_PATH" if sys.platform == "darwin" \
            else "LD_LIBRARY_PATH"
        old = out.get(key)
        out[key] = ":".join(libdirs + ([old] if old else []))
    return out


# ---------------------------------------------------------------------------
# VCD reading
# ---------------------------------------------------------------------------

def read_vcd(path: Path, top: Sequence[str], core: Sequence[str]) -> Dict[str, object]:
    """Read the wanted signals out of a VCD file.

    Returns {"samples": [(time, {key: value})], "last": {key: value}} where the
    keys are "top.<name>" for the wrapper's ports and "u.<name>" for the named
    signals of the core instance.  The scope matters: the wrapper's port `pc`
    and the core's internal `pc` are different signals with the same name.
    """
    scopes: List[str] = []
    want_top = set(top)
    want_core = set(core)
    values: Dict[str, str] = {}          # code -> value
    keyed: Dict[str, str] = {}           # "scope/name" -> code
    t: Optional[int] = None
    samples: List[Tuple[int, Dict[str, str]]] = []

    def snapshot() -> Dict[str, str]:
        snap: Dict[str, str] = {}
        for key, code in keyed.items():
            v = values.get(code)
            if v is not None:
                snap[key] = v
        return snap

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("$scope"):
                scopes.append(s.split()[2])
                continue
            if s.startswith("$upscope"):
                if scopes:
                    scopes.pop()
                continue
            if s.startswith("$var"):
                parts = s.split()
                if len(parts) < 5:
                    continue
                code, name = parts[3], parts[4]
                scope = "/".join(scopes)
                if scope == "wrap_sim" and name in want_top:
                    keyed["top." + name] = code
                elif scope == "wrap_sim/u" and name in want_core:
                    keyed["u." + name] = code
                continue
            if s.startswith("$enddefinitions"):
                continue
            if s.startswith("#"):
                if t is not None:
                    samples.append((t, snapshot()))
                t = int(s[1:])
                continue
            if s[0] in "01xzXZ" and len(s.split()) >= 2:
                v, code = s.split()[:2]
                values[code] = v
                continue
            if s[0] in "bB" and " " in s:
                v, code = s.split()[:2]
                values[code] = v[1:]
    if t is not None:
        samples.append((t, snapshot()))
    last = samples[-1][1] if samples else {}
    return {"samples": samples, "last": last}


def bits(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if not value or any(c not in "01" for c in value):
        return None
    return int(value, 2)


def hexs(value: Optional[int], width: int) -> str:
    if value is None:
        return "-"
    return "%0*X" % (width, value & ((1 << (4 * width)) - 1))


# ---------------------------------------------------------------------------
# Reconstruction of the gate-level trace and pipeview
# ---------------------------------------------------------------------------

def falling_edges(samples: Sequence[Tuple[int, Dict[str, str]]]) -> List[Dict[str, str]]:
    """Return the state at every falling clock edge, in order.

    The RTL testbench samples the retirement interface and the pipeview at the
    falling edge in the middle of each cycle, so the gate-level run has to be
    sampled at the same point.  Yosys `sim` records the VCD value change after
    the edge has been evaluated, so the sample at a falling edge holds exactly
    the values the testbench would see.
    """
    out: List[Dict[str, str]] = []
    prev: Optional[str] = None
    for _t, snap in samples:
        clk = snap.get("top.clk")
        if clk == "0" and prev == "1":
            out.append(snap)
        prev = clk
    return out


def gate_records(edges: Sequence[Dict[str, str]]) -> Tuple[List[str], List[List[str]]]:
    """Return (retirement-trace lines, pipeview lines) reconstructed from VCD.

    The edges must be the falling edges of the clock, sampled with reset
    released.  The architectural cycle number is taken from the core's own
    cycle counter: it is cleared by reset and incremented once per non-reset
    clock, and the RTL testbench calls the first cycle after reset release
    cycle 1, which is the cycle in which the counter still reads 0.  The
    gate-level cycle number of a sample is therefore `cycle_count + 1`.
    """
    trace: List[str] = []
    pipe: List[List[str]] = []
    seq = 0
    for snap in edges:
        if snap.get("top.rst") == "1":
            continue
        cc = bits(snap.get("u.cycle_count"))
        if cc is None:
            raise ValueError("the VCD has no u.cycle_count signal")
        cycle = cc + 1
        pipe.append([
            str(cycle),
            snap.get("top.pidv", "x"),
            hexs(bits(snap.get("top.pidpc")), 4),
            snap.get("top.pexv", "x"),
            hexs(bits(snap.get("top.pexpc")), 4),
            snap.get("top.pmemv", "x"),
            hexs(bits(snap.get("top.pmempc")), 4),
            snap.get("top.pwbv", "x"),
            hexs(bits(snap.get("top.pwbpc")), 4),
            snap.get("top.pstall", "x"),
            snap.get("top.pflush", "x"),
        ])
        if snap.get("top.rv") != "1":
            continue
        seq += 1
        rd = bits(snap.get("top.rrd"))
        reg_write = snap.get("top.rwe") == "1"
        if reg_write and rd is not None and rd != 0:
            rd_s = "r%d" % rd
            val_s = hexs(bits(snap.get("top.rval")), 4)
        else:
            rd_s = "-"
            val_s = "-"
        kind = snap.get("top.rkind", "00")
        if kind == "01":
            memop = "L"
        elif kind == "10":
            memop = "S"
        else:
            memop = "-"
        if memop != "-":
            addr_s = hexs(bits(snap.get("top.raddr")), 4)
            size = bits(snap.get("top.rsize"))
            size_s = {1: "1", 2: "2"}.get(size if size is not None else -1, "-")
            data_s = hexs(bits(snap.get("top.rdata")), 4)
        else:
            addr_s = "-"
            size_s = "-"
            data_s = "-"
        taken = snap.get("top.rtaken") == "1"
        tgt_s = hexs(bits(snap.get("top.rtgt")), 4) if taken else "-"
        trace.append(" ".join([
            str(seq), str(cycle),
            hexs(bits(snap.get("top.rpc")), 4),
            hexs(bits(snap.get("top.rinstr")), 8),
            rd_s, val_s, memop, addr_s, size_s, data_s,
            "1" if taken else "0", tgt_s,
        ]))
    return trace, pipe


PIPE_NAMES = ["cycle", "id_v", "id_pc", "ex_v", "ex_pc", "mem_v", "mem_pc",
              "wb_v", "wb_pc", "stall", "flush"]


def diff_pipe(gold: List[List[str]], rtl: List[List[str]]) -> Tuple[bool, str]:
    """Compare pipeview rows, ignoring a stale PC in an empty stage.

    Same rule as the RTL runner (tools/rtl_run.py): a PC is architecturally
    meaningful only while its stage is valid, and the two sides keep
    different stale values in an empty stage.
    """
    n = max(len(gold), len(rtl))
    pc_pairs = [(1, 2), (3, 4), (5, 6), (7, 8)]
    for i in range(n):
        if i >= len(gold):
            return False, "gate pipeview has an extra cycle %s" % rtl[i][0]
        if i >= len(rtl):
            return False, ("gate pipeview ended after %d cycles; the model has %d"
                           % (len(rtl), len(gold)))
        skip = set()
        for v_idx, pc_idx in pc_pairs:
            if gold[i][v_idx] == "0" and rtl[i][v_idx] == "0":
                skip.add(pc_idx)
        diffs = ["%s model=%s gate=%s" % (PIPE_NAMES[k], gold[i][k], rtl[i][k])
                 for k in range(11) if k not in skip and gold[i][k] != rtl[i][k]]
        if diffs:
            return False, "first pipeview divergence at cycle %s: %s" % (
                gold[i][0], "; ".join(diffs))
    return True, "pipeview identical over %d cycles" % len(gold)


# ---------------------------------------------------------------------------
# One program
# ---------------------------------------------------------------------------

def run_program(name: str, asm: str, ns: argparse.Namespace, ghdl: str,
                env: Dict[str, str]) -> Tuple[bool, str]:
    sys.path.insert(0, str(ROOT / "tools"))
    import assembler  # noqa: E402
    import isa_sim    # noqa: E402
    import trace_diff  # noqa: E402

    build = Path(ns.build) / name
    build.mkdir(parents=True, exist_ok=True)

    prog = Path(asm)
    if not prog.is_absolute():
        prog = ROOT / prog
    src = prog.read_text(encoding="utf-8")
    img = assembler.assemble_text(src, instr_words=2048, ram_bytes=4096)
    (build / "minicpu16_program_pkg.vhd").write_text(img.vhdl_package(),
                                                     encoding="utf-8")
    (build / "wrap_sim.vhd").write_text(WRAPPER.format(fwd=ns.forwarding),
                                        encoding="utf-8")
    vcd = build / "gate.vcd"

    # Golden side: the timing layer's own retirement trace and pipeview.
    res = isa_sim.run_pipeline(img.instr_words,
                               isa_sim.Config(ram_init=img.data, max_cycles=100000))
    gold_trace = res.trace_text()
    gold_pipe = [p.line().split() for p in res.pipe]

    # Enough clock cycles for the HALT to retire, plus a few idle ones.
    cycles = max(res.cycles, 1) + 6

    rtl = RTL
    if ns.rtl_dir:
        rtl = [str((Path(ns.rtl_dir) / Path(r).name).resolve()) for r in RTL]
    script = ("ghdl --std=08 " + " ".join(rtl) +
              " " + str((build / "minicpu16_program_pkg.vhd").resolve()) +
              " " + str((build / "wrap_sim.vhd").resolve()) + " -e wrap_sim; "
              "hierarchy -top wrap_sim; proc; opt; memory; opt; techmap; opt; "
              "sim -clock clk -reset rst -rstlen 3 -n %d -vcd %s"
              % (cycles, vcd.resolve()))
    try:
        rr = subprocess.run(["yosys", "-m", ns.plugin, "-p", script], cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=1800, env=env)
    except subprocess.TimeoutExpired:
        return False, "yosys timeout"
    (build / "yosys_sim.log").write_text(rr.stdout, encoding="utf-8")
    if rr.returncode != 0 or not vcd.is_file():
        return False, "yosys exit %d (see %s)" % (rr.returncode,
                                                  build / "yosys_sim.log")

    data = read_vcd(vcd, TOP_SIGNALS, CORE_SIGNALS)
    samples = data["samples"]
    if not samples:
        return False, "the VCD has no samples"
    last = data["last"]
    edges = falling_edges(samples)
    try:
        gate_trace, gate_pipe = gate_records(edges)
    except ValueError as e:
        return False, str(e)

    (build / "gate.trace").write_text(
        trace_diff.HEADER0 + "\n" + trace_diff.HEADER1 + "\n" +
        ("\n".join(gate_trace) + "\n" if gate_trace else ""), encoding="utf-8")
    (build / "gold.trace").write_text(gold_trace, encoding="utf-8")

    # -- the retirement trace, record by record, cycle column included -----
    gold_lines = trace_diff.parse_trace(str(build / "gold.trace"))
    gate_lines = trace_diff.parse_trace(str(build / "gate.trace"))
    ok, msg = trace_diff.diff_traces(gold_lines, gate_lines,
                                      ignore_cycles=False, context=2)
    if not ok:
        return False, "retirement trace differs:\n" + msg

    # -- the pipeview, cycle by cycle --------------------------------------
    if len(gate_pipe) < len(gold_pipe):
        return False, ("gate pipeview has %d cycles, the model has %d"
                       % (len(gate_pipe), len(gold_pipe)))
    ok, msg = diff_pipe(gold_pipe, gate_pipe[:len(gold_pipe)])
    if not ok:
        return False, msg

    # -- after the HALT the machine must stay frozen -----------------------
    frozen = [r for r in gate_pipe[len(gold_pipe):]]
    for row in frozen:
        if row[7] != "0":                       # pipe_wb_valid
            return False, ("an instruction retired after the HALT (cycle %s)"
                           % row[0])

    # -- the architectural result ------------------------------------------
    gpio = bits(last.get("top.gpio"))
    halted = last.get("top.halted")
    pc = bits(last.get("top.pc"))
    if gpio is None:
        return False, "no gpio value in the VCD"
    if gpio != (res.gpio_out & 0xFFFF):
        return False, ("gate gpio_out=0x%04X, golden model gpio_out=0x%04X"
                       % (gpio, res.gpio_out & 0xFFFF))
    if halted != "1":
        return False, "the gate-level run did not reach the HALT"
    if not res.halted:
        return False, "the golden model did not halt"
    if not gate_trace or "1" != "1":
        return False, "no retirement record"

    detail = ("records=%d cycles=%d gpio=0x%04X pc_fetch=0x%04X pipeview=%d"
              % (len(gate_trace), res.cycles, gpio, pc if pc is not None else 0,
                 len(gold_pipe)))
    return True, detail


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Post-synthesis gate-level simulation")
    p.add_argument("--program", default=None,
                   help="run a single program instead of the three-program set")
    p.add_argument("--forwarding", default="true", choices=["true", "false"])
    p.add_argument("--build", default=str(ROOT / "build" / "postsynth"))
    p.add_argument("--rtl-dir", default=None,
                   help="override the RTL directory (used to prove the check "
                        "detects an injected bug)")
    ns = p.parse_args(argv)

    if not shutil.which("yosys"):
        print("SKIP postsynth: yosys not found")
        return 0
    plugin = find_plugin()
    if plugin is None:
        print("SKIP postsynth: ghdl-yosys-plugin ghdl.so not found")
        return 0
    ns.plugin = plugin

    build = Path(ns.build)
    build.mkdir(parents=True, exist_ok=True)
    ghdl = find_ghdl()
    env = dict(os.environ)
    prefix = ghdl_prefix(ghdl)
    if prefix:
        env["GHDL_PREFIX"] = prefix
    env = plugin_env(env, ghdl, plugin)

    if ns.program:
        programs = [(Path(ns.program).stem, ns.program)]
    else:
        programs = list(PROGRAMS)

    print("=== post-synthesis (gate-level) simulation, step 10 (optional) ===")
    print("yosys    : %s" % shutil.which("yosys"))
    print("plugin   : %s" % plugin)
    print("ghdl     : %s" % ghdl)
    print("forwarding: %s" % ns.forwarding)

    failures = 0
    for name, asm in programs:
        ok, detail = run_program(name, asm, ns, ghdl, env)
        if ok:
            print("PASS postsynth %-24s %s" % (name, detail))
        else:
            failures += 1
            print("FAIL postsynth %-24s %s" % (name, detail))

    if failures:
        print("FAIL postsynth: %d of %d programs failed" % (failures, len(programs)))
        return 1
    print("PASS postsynth: %d programs, the retirement trace and the pipeview "
          "match the golden model cycle for cycle" % len(programs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
