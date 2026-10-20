#!/usr/bin/env python3
"""Optional Phase 5B Step 10: post-synthesis (gate-level) simulation.

This is deliberately small and optional.  It:

  1. assembles a self-contained program,
  2. wraps `minicpu16_core` in a test wrapper that exposes gpio_out/pc_out/
     retire_halt,
  3. runs Yosys (ghdl-yosys-plugin) through `synth_ice40`-equivalent generic
     technology mapping (`proc; opt; memory; opt; techmap; opt`),
  4. executes the mapped netlist with the built-in Yosys `sim` command and
     writes a VCD,
  5. compares the final gpio_out / pc_out / halted against the golden timing
     layer for the same program.

Why generic technology mapping rather than the iCE40 cell netlist: the open
`cells_sim.v` model of `SB_RAM40_4K` only loads `INIT_0` at time zero and the
Yosys `sim` flow does not apply the per-instance `INIT_*` parameters, so the
inferred instruction ROM reads as zero and the CORE fetches HALT' (the netlist
itself carries the correct INIT_* parameters, as the synthesized JSON shows).
Mapping the memory to flip-flops/logic preserves the initialization and gives a
true gate-level run of the same logic.  Finding F31 records this.

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
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

RTL = [
    "rtl/minicpu16_pkg.vhd", "rtl/shifter.vhd", "rtl/alu.vhd",
    "rtl/branch_unit.vhd", "rtl/register_file.vhd", "rtl/decoder.vhd",
    "rtl/hazard_unit.vhd", "rtl/forwarding_unit.vhd", "rtl/imem.vhd",
    "rtl/dmem.vhd", "rtl/mmio.vhd", "rtl/minicpu16_core.vhd",
]

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
        halted : out std_logic);
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
  constant IMG : instr_image_t(0 to 2047) := get_img;
  signal uart_tx_byte_s  : std_logic_vector(7 downto 0);
  signal uart_tx_valid_s : std_logic;
  signal uart_busy_s     : std_logic;
begin
  u : entity work.minicpu16_core
    generic map (FORWARDING => {fwd}, INSTR_WORDS => 2048, RAM_BYTES => 4096,
                 UART_CYCLES_PER_BYTE => 10, COUNTING => false,
                 INSTR_INIT => IMG, DATA_INIT => (0 => x"00"))
    port map (clk => clk, rst => rst, gpio_in => (others => '0'),
              uart_tx_byte => uart_tx_byte_s, uart_tx_valid => uart_tx_valid_s,
              uart_busy => uart_busy_s, gpio_out => gpio, pc_out => pc,
              retire_valid => open, retire_pc => open, retire_instr => open,
              retire_reg_write => open, retire_rd => open, retire_value => open,
              retire_mem_kind => open, retire_mem_addr => open,
              retire_mem_size => open, retire_mem_data => open,
              retire_taken => open, retire_target => open,
              retire_halt => halted, pipe_id_valid => open, pipe_id_pc => open,
              pipe_ex_valid => open, pipe_ex_pc => open, pipe_mem_valid => open,
              pipe_mem_pc => open, pipe_wb_valid => open, pipe_wb_pc => open,
              pipe_stall => open, pipe_flush => open, cov => open);
end architecture;
'''


def find_ghdl() -> str:
    for c in (os.environ.get("GHDL"), shutil.which("ghdl"),
              "/tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl"):
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


def parse_vcd(path: Path, wanted: Tuple[str, ...]) -> Dict[str, str]:
    """Return the last value of each wanted top-level signal (by name)."""
    codes: Dict[str, str] = {}
    values: Dict[str, str] = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    depth = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("$scope"):
            depth += 1
        elif line.startswith("$upscope"):
            depth -= 1
        elif line.startswith("$var") and depth == 1:
            parts = line.split()
            if len(parts) >= 5 and parts[4] in wanted:
                codes[parts[3]] = parts[4]
                values[parts[4]] = "x"
        elif line and line[0] in "01" and not line.startswith("$"):
            val, code = line.split()[:2]
            if code in codes:
                values[codes[code]] = val
        elif line and (line[0] in "bB" and " " in line):
            val, code = line.split()[:2]
            if code in codes:
                values[codes[code]] = val[1:]
        i += 1
    return values


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Post-synthesis gate-level simulation")
    p.add_argument("--program", default="tests/directed/j_double_producer.asm")
    p.add_argument("--forwarding", default="true", choices=["true", "false"])
    p.add_argument("--cycles", type=int, default=200)
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

    build = Path(ns.build)
    build.mkdir(parents=True, exist_ok=True)
    ghdl = find_ghdl()
    env = dict(os.environ)
    prefix = ghdl_prefix(ghdl)
    if prefix:
        env["GHDL_PREFIX"] = prefix

    prog = Path(ns.program)
    if not prog.is_absolute():
        prog = ROOT / prog
    sys.path.insert(0, str(ROOT / "tools"))
    import assembler  # noqa: E402
    import isa_sim    # noqa: E402

    src = prog.read_text(encoding="utf-8")
    img = assembler.assemble_text(src, instr_words=2048, ram_bytes=4096)
    (build / "minicpu16_program_pkg.vhd").write_text(img.vhdl_package(),
                                                     encoding="utf-8")
    (build / "wrap_sim.vhd").write_text(WRAPPER.format(fwd=ns.forwarding),
                                        encoding="utf-8")
    vcd = build / "gate.vcd"

    rtl = RTL
    if ns.rtl_dir:
        rtl = [str((Path(ns.rtl_dir) / Path(r).name).resolve()) for r in RTL]
    script = ("ghdl --std=08 " + " ".join(rtl) +
              " " + str((build / "minicpu16_program_pkg.vhd").resolve()) +
              " " + str((build / "wrap_sim.vhd").resolve()) + " -e wrap_sim; "
              "hierarchy -top wrap_sim; proc; opt; memory; opt; techmap; opt; "
              "sim -clock clk -reset rst -rstlen 3 -n %d -vcd %s"
              % (ns.cycles, vcd.resolve()))
    try:
        rr = subprocess.run(["yosys", "-m", plugin, "-p", script], cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=900, env=env)
    except subprocess.TimeoutExpired:
        print("FAIL postsynth: yosys timeout")
        return 1
    (build / "yosys_sim.log").write_text(rr.stdout, encoding="utf-8")
    if rr.returncode != 0 or not vcd.is_file():
        print("FAIL postsynth: yosys exit %d (see %s)" %
              (rr.returncode, build / "yosys_sim.log"))
        return 1

    got = parse_vcd(vcd, ("gpio", "pc", "halted"))

    def as_int(name: str) -> Optional[int]:
        v = str(got.get(name, "x"))
        return int(v, 2) if v and set(v) <= {"0", "1"} else None

    gpio = as_int("gpio")
    pc = as_int("pc")
    halted = as_int("halted")

    # Golden reference: run the architecture-only model to get the final PC and
    # GPIO_OUT.  The design is deterministic; cycles do not enter this check.
    res = isa_sim.run_pipeline(img.instr_words,
                              isa_sim.Config(ram_init=img.data, max_cycles=100000))
    gold_gpio = res.gpio_out

    print("program   : %s" % prog)
    print("forwarding: %s" % ns.forwarding)
    print("netlist   : %s" % (build / "gate.v" if (build / "gate.v").exists() else "(in-memory yosys)"))
    gpio_s = "0x%04X" % gpio if gpio is not None else "x"
    pc_s = "0x%04X" % pc if pc is not None else "x"
    print("gate      : gpio=%s pc_fetch=%s halted=%s" % (gpio_s, pc_s, halted))
    print("golden    : gpio=0x%04X pc_arch=0x%04X" % (gold_gpio, res.pc))
    # pc_out is the internal fetch pointer.  Its frozen value after HALT is not
    # architecturally specified (finding F3), so it is reported but not
    # asserted; the architectural quantity is the GPIO_OUT register and HALT.
    ok = (gpio is not None and gpio == gold_gpio and halted == 1)
    if ok:
        print("PASS postsynth: gate-level gpio_out and HALT match the golden "
              "model (pc_out is the unspecified frozen fetch pointer, F3)")
        return 0
    print("FAIL postsynth: gate-level result differs from the golden model")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
