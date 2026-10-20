#!/usr/bin/env python3
"""Synthesis and timing measurement for MiniCPU-16 Phase 5B.

Runs GHDL + ghdl-yosys-plugin + Yosys synth_ice40 on the core, once with
FORWARDING => true and once with FORWARDING => false, asserts that the
instruction/data memories infer exactly BRAM_BLOCKS (24) SB_RAM40_4K blocks,
runs nextpnr-ice40 for HX8K and UP5K at 50 MHz with seeds 1, 2, 3, and prints
the cycle-exact CPI of the benchmark set.

If the plugin or a tool is missing the target prints SKIP with the exact
commands and exits 0; it is not part of make test.
"""

from __future__ import annotations

import argparse
import os
import re
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

BRAM_BLOCKS = 24
DEFAULT_SEEDS = (1, 2, 3)
DEFAULT_DEVICES = (("hx8k", "ct256"), ("up5k", "sg48"))
TARGET_MHZ = 50

WRAPPER = '''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.minicpu16_pkg.all;

-- Synthesis-only wrapper: the core default INSTR_INIT is a single HALT word,
-- which constant-folds, so no instruction ROM is inferred.  This wrapper
-- supplies a non-constant 2048-word image and selects the forwarding
-- configuration under test.
entity {top} is
  port (clk : in std_logic; rst : in std_logic;
        gpio_in : in std_logic_vector(15 downto 0);
        sig : out std_logic_vector(7 downto 0));
end entity;
architecture rtl of {top} is
  signal uart_tx_byte_s : std_logic_vector(7 downto 0);
  signal uart_tx_valid_s : std_logic;
  signal uart_busy_s : std_logic;
  signal gpio_out_s : std_logic_vector(15 downto 0);
  signal pc_out_s : std_logic_vector(15 downto 0);
  signal sig_r : std_logic_vector(7 downto 0) := (others => '0');
  function make_rom return instr_image_t is
    variable r : instr_image_t(0 to 2047);
    variable x : unsigned(31 downto 0) := x"12345678";
  begin
    for i in 0 to 2047 loop
      x := resize(x * 1664525 + 1013904223, 32);
      r(i) := std_logic_vector(x);
    end loop;
    return r;
  end function;
  constant IMG : instr_image_t := make_rom;
  constant DAT : byte_image_t := (0 => x"00");
begin
  u : entity work.minicpu16_core
    generic map (FORWARDING => {fwd}, INSTR_WORDS => 2048, RAM_BYTES => 4096,
                 UART_CYCLES_PER_BYTE => 10, COUNTING => false,
                 INSTR_INIT => IMG, DATA_INIT => DAT)
    port map (clk => clk, rst => rst, gpio_in => gpio_in,
              uart_tx_byte => uart_tx_byte_s, uart_tx_valid => uart_tx_valid_s,
              uart_busy => uart_busy_s, gpio_out => gpio_out_s, pc_out => pc_out_s,
              retire_valid => open, retire_pc => open, retire_instr => open,
              retire_reg_write => open, retire_rd => open, retire_value => open,
              retire_mem_kind => open, retire_mem_addr => open,
              retire_mem_size => open, retire_mem_data => open,
              retire_taken => open, retire_target => open, retire_halt => open,
              cov => open);
  process (clk)
  begin
    if rising_edge(clk) then
      sig_r(0) <= uart_tx_valid_s;
      sig_r(1) <= uart_busy_s;
      sig_r(7 downto 2) <= gpio_out_s(7 downto 2) xor pc_out_s(7 downto 2);
    end if;
  end process;
  sig <= sig_r;
end architecture;
'''


def find_ghdl() -> str:
    # The permanent local install is preferred; PATH (what CI uses) comes next
    # and the ephemeral /tmp copy is only a fallback.  GHDL overrides all.
    env = os.environ.get("GHDL")
    if env:
        return env
    cands = [str(Path.home() / "tools" / "ghdl" / "bin" / "ghdl"),
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
    cands = [
        os.environ.get("GHDL_YOSYS_PLUGIN"),
        str(Path.home() / "tools" / "ghdl-yosys-plugin" / "ghdl.so"),
        str(Path.home() / ".local" / "lib" / "yosys" / "ghdl.so"),
        "/tmp/ghdl-yosys-plugin/ghdl.so",
    ]
    for c in cands:
        if c and Path(c).is_file():
            return c
    try:
        datdir = subprocess.run(["yosys-config", "--datdir"], stdout=subprocess.PIPE,
                                text=True).stdout.strip()
        p = Path(datdir) / "plugins" / "ghdl.so"
        if p.is_file():
            return str(p)
    except OSError:
        pass
    return None


def run(cmd: List[str], cwd: Path, timeout: int, env: Dict[str, str]):
    return subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          timeout=timeout, env=env)


def parse_counts(log: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for line in log.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+)\s*$", line)
        if not m:
            continue
        n = int(m.group(1))
        k = m.group(2)
        if k == "cells" or k.startswith("SB_") or k.startswith("ICESTORM_"):
            counts[k] = max(counts.get(k, 0), n)
    return counts


def parse_nextpnr(log: str) -> Dict[str, str]:
    d: Dict[str, str] = {}
    fmax = re.findall(r"Max frequency for clock .*?:\s*([0-9.]+) MHz", log)
    if fmax:
        d["fmax_mhz"] = fmax[-1]
    at = re.findall(r"(PASS|FAIL) at ([0-9.]+) MHz", log)
    if at:
        d["at_target"] = "%s at %s MHz" % (at[-1][0], at[-1][1])
    lines = log.splitlines()
    blocks = []
    for i, line in enumerate(lines):
        if "Critical path report for clock" in line and "posedge -> posedge" in line:
            blocks.append(line.strip())
            for nxt in lines[i + 1:i + 14]:
                if nxt.strip():
                    blocks.append(nxt.strip())
            break
    d["critical_path"] = "\n".join(blocks)
    return d


def cpi_table() -> List[str]:
    """Cycle-exact CPI, produced by the same suite as docs/BENCHMARKS.md."""
    sys.path.insert(0, str(ROOT / "tools"))
    import program_suite as ps
    rows = ["", "## CPI (cycle-exact timing layer, forwarding contract)", "",
            "| program | instructions | cycles | CPI | load-use stalls |",
            "|---|---:|---:|---:|---:|"]
    for prog in ps.PROGRAMS:
        try:
            _img, res = ps.run(prog)
        except Exception as e:  # noqa: BLE001
            rows.append("| %s | - | - | - | error: %s |" % (prog.name, e))
            continue
        cpi = res.cycles / res.instructions if res.instructions else 0.0
        rows.append("| %s | %d | %d | %.3f | %d |"
                    % (prog.name, res.instructions, res.cycles, cpi, res.stalls))
    return rows


def _ghdl_version(ghdl: str) -> str:
    try:
        out = subprocess.run([ghdl, "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True).stdout
        for line in out.splitlines():
            if "GHDL" in line:
                return line.strip()
    except OSError:
        pass
    return ghdl


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Synthesis + timing measurement")
    p.add_argument("--build", default=str(ROOT / "build" / "synth"))
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--seeds", default="1,2,3")
    p.add_argument("--devices", default="hx8k:ct256,up5k:sg48")
    p.add_argument("--skip-nextpnr", action="store_true")
    p.add_argument("--cpi", action="store_true", default=True)
    ns = p.parse_args(argv)
    build = Path(ns.build)
    build.mkdir(parents=True, exist_ok=True)
    seeds = [int(s) for s in ns.seeds.split(",") if s]
    devices = []
    for d in ns.devices.split(","):
        if ":" in d:
            dev, pkg = d.split(":", 1)
            devices.append((dev, pkg))
    ghdl = find_ghdl()
    prefix = ghdl_prefix(ghdl)
    plugin = find_plugin()
    if not shutil.which("yosys"):
        print("SKIP synth-smoke: yosys not found")
        return 0
    if plugin is None:
        print("SKIP synth-smoke: ghdl-yosys-plugin ghdl.so not found.")
        print("Build it with:")
        print("  git clone --depth 1 --branch ghdl-v6.0.0 https://github.com/ghdl/ghdl-yosys-plugin")
        print("  cd ghdl-yosys-plugin && make GHDL=%s" % ghdl)
        return 0
    env = dict(os.environ)
    if prefix:
        env["GHDL_PREFIX"] = prefix

    report = ["# Synthesis + timing (Phase 5B)", "",
              "Toolchain: " + _ghdl_version(ghdl) +
              " + ghdl-yosys-plugin + yosys synth_ice40 + nextpnr-ice40.", "",
              "This is a flat, unconstrained iCE40 estimate.  nextpnr is run with "
              "the default (unconstrained) placement; Fmax is a lower-bound "
              "estimate from this flow, not a sign-off number, and the design is "
              "not tuned.", ""]

    configs = [("wrap_core_fwd", "true"), ("wrap_core_nofwd", "false")]
    all_counts: Dict[str, Dict[str, int]] = {}
    all_fmax: Dict[str, Dict[str, str]] = {}
    for top, fwd in configs:
        wrapper = build / (top + ".vhd")
        wrapper.write_text(WRAPPER.format(top=top, fwd=fwd))
        json_path = build / (top + ".json")
        log_path = build / (top + "_yosys.log")
        script = ("ghdl --std=08 " + " ".join(RTL) + " " + str(wrapper) +
                  " -e " + top + "; synth_ice40 -top " + top + " -json " +
                  str(json_path.resolve()))
        print("running yosys for %s (FORWARDING=%s)" % (top, fwd))
        try:
            rr = run(["yosys", "-m", plugin, "-p", script], ROOT, ns.timeout, env)
        except subprocess.TimeoutExpired:
            print("FAIL: yosys timeout for %s" % top)
            return 1
        log_path.write_text(rr.stdout)
        if rr.returncode != 0:
            print("FAIL: yosys exit %d for %s (see %s)" % (rr.returncode, top,
                                                           log_path))
            return 1
        all_counts[top] = parse_counts(rr.stdout)

    report.append("## Inferred cells (synth_ice40)")
    report.append("")
    for top, fwd in configs:
        counts = all_counts[top]
        report.append("### %s (FORWARDING=%s)" % (top, fwd))
        report.append("")
        report.append("| cell | count |")
        report.append("|---|---|")
        for k in sorted(counts):
            report.append("| %s | %d |" % (k, counts[k]))
        report.append("")

    report.append("## Block-RAM assertion")
    report.append("")
    bram_ok = True
    for top, fwd in configs:
        ram = all_counts[top].get("SB_RAM40_4K", 0)
        ok = (ram == BRAM_BLOCKS)
        bram_ok = bram_ok and ok
        report.append("* %s: SB_RAM40_4K = %d (expected %d) -> %s"
                      % (top, ram, BRAM_BLOCKS, "PASS" if ok else "FAIL"))
    report.append("")
    report.append("Expected: imem 2048x32 = 16 blocks, dmem 4096x8 = 8 blocks, "
                  "total %d." % BRAM_BLOCKS)
    report.append("")

    np = shutil.which("nextpnr-ice40")
    if not ns.skip_nextpnr and np:
        report.append("## nextpnr-ice40 Fmax at target %d MHz" % TARGET_MHZ)
        report.append("")
        report.append("| device | package | seed | config | Fmax (MHz) | target |")
        report.append("|---|---|---|---|---|---|")
        for dev, pkg in devices:
            for seed in seeds:
                for top, fwd in configs:
                    asc = build / ("%s_%s_%d.asc" % (top, dev, seed))
                    cmd = [np, "--" + dev, "--package", pkg,
                           "--json", str(build / (top + ".json")),
                           "--pcf-allow-unconstrained", "--freq", str(TARGET_MHZ),
                           "--seed", str(seed), "--asc", str(asc)]
                    logname = "%s_%s_%d.log" % (top, dev, seed)
                    try:
                        nr = run(cmd, ROOT, ns.timeout, env)
                    except subprocess.TimeoutExpired:
                        report.append("| %s | %s | %d | %s | timeout | - |"
                                      % (dev, pkg, seed, fwd))
                        continue
                    (build / logname).write_text(nr.stdout)
                    d = parse_nextpnr(nr.stdout)
                    all_fmax[top + "_" + dev + "_" + str(seed)] = d
                    report.append("| %s | %s | %d | %s | %s | %s |"
                                  % (dev, pkg, seed, fwd,
                                     d.get("fmax_mhz", "FAIL"),
                                     d.get("at_target", "-")))
                    if dev == "hx8k" and seed == 1 and d.get("critical_path"):
                        report.append("")
                        report.append("Critical path for " + top + ":")
                        report.append("")
                        report.append(d["critical_path"])
                        report.append("")
        report.append("")
    else:
        report.append("nextpnr-ice40 not found or disabled: no Fmax estimate.")
        report.append("")

    if ns.cpi:
        report.extend(cpi_table())

    (build / "report_5b.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    if not bram_ok:
        print("FAIL: block-RAM assertion did not hold")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())