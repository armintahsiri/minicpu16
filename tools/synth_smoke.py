#!/usr/bin/env python3
"""Synthesis inference smoke test (time-boxed, non-blocking).

Runs GHDL + ghdl-yosys-plugin + Yosys synth_ice40 on the Phase 5A core with
default generics, reports SB_RAM40_4K/LUT/FF counts, and optionally runs
nextpnr-ice40 for HX8K/UP5K Fmax estimates with a fixed seed.

If the plugin or tools are missing, prints a SKIP with the exact commands and
returns 0 (this target is not part of make test).
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

WRAPPER = '''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.minicpu16_pkg.all;

-- Synthesis-only wrapper: the core's default INSTR_INIT is a single HALT word,
-- which is a valid design but is constant-folded by synthesis (no ROM to infer).
-- This wrapper supplies a non-constant 2048-word image so the instruction BRAM
-- inference can be measured.  RAM_BYTES/UART/COUNTING are the defaults.
entity wrap_core is
  port (clk : in std_logic; rst : in std_logic;
        gpio_in : in std_logic_vector(15 downto 0);
        sig : out std_logic_vector(7 downto 0));
end entity;
architecture rtl of wrap_core is
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
    generic map (FORWARDING => false, INSTR_WORDS => 2048, RAM_BYTES => 4096,
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
    env = os.environ.get("GHDL")
    if env:
        return env
    cands = [shutil.which("ghdl"),
             str(Path.home() / "tools" / "ghdl" / "bin" / "ghdl"),
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
    cands = [
        os.environ.get("GHDL_YOSYS_PLUGIN"),
        "/tmp/ghdl-yosys-plugin/ghdl.so",
        str(Path.home() / ".local" / "lib" / "yosys" / "ghdl.so"),
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


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Synthesis inference smoke test")
    p.add_argument("--build", default=str(ROOT / "build" / "synth"))
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--nextpnr", action="store_true", default=True)
    ns = p.parse_args(argv)
    build = Path(ns.build)
    build.mkdir(parents=True, exist_ok=True)
    ghdl = find_ghdl()
    prefix = ghdl_prefix(ghdl)
    plugin = find_plugin()
    if not shutil.which("yosys"):
        print("SKIP synth-smoke: yosys not found")
        return 0
    if plugin is None:
        print("SKIP synth-smoke: ghdl-yosys-plugin ghdl.so not found.")
        print("Install/build it with:")
        print("  git clone --depth 1 --branch ghdl-v6.0.0 https://github.com/ghdl/ghdl-yosys-plugin")
        print("  cd ghdl-yosys-plugin && make GHDL=%s" % ghdl)
        print("  yosys -m /tmp/ghdl-yosys-plugin/ghdl.so ...")
        return 0
    env = dict(os.environ)
    if prefix:
        env["GHDL_PREFIX"] = prefix
    wrapper = build / "wrap_core.vhd"
    wrapper.write_text(WRAPPER)
    report = ["# Synthesis inference smoke test", ""]

    def synth(top: str, json_name: str, log_name: str) -> Tuple[Optional[Dict[str, int]], str]:
        script = ("ghdl --std=08 " + " ".join(RTL) + " " + str(wrapper) +
                  " -e " + top + "; synth_ice40 -top " + top + " -json " +
                  str((build / json_name).resolve()))
        print("running: yosys -m %s -p '%s'" % (plugin, script))
        try:
            rr = run(["yosys", "-m", plugin, "-p", script], ROOT, ns.timeout, env)
        except subprocess.TimeoutExpired:
            return None, "timeout after %d s" % ns.timeout
        (build / log_name).write_text(rr.stdout)
        if rr.returncode != 0:
            return None, "Yosys exit %d" % rr.returncode
        return parse_counts(rr.stdout), script

    counts_wrap, script_wrap = synth("wrap_core", "core.json", "yosys_wrap.log")
    if counts_wrap is None:
        print("FAIL synth-smoke: wrapper synthesis failed: %s" % script_wrap)
        return 1
    counts_def, script_def = synth("minicpu16_core", "core_default.json", "yosys_default.log")

    report.append("Two syntheses are reported, both with `synth_ice40`:")
    report.append("")
    report.append("1. `minicpu16_core` with the true default generics.  The default")
    report.append("   `INSTR_INIT` is one HALT word, so the ROM constant-folds and no")
    report.append("   instruction BRAM can be inferred; this is a valid but empty design.")
    report.append("2. `wrap_core`: the same core with a non-constant 2048-word image")
    report.append("   (`INSTR_WORDS=2048`, `RAM_BYTES=4096`, UART=10, COUNTING=false).")
    report.append("   This is the meaningful block-RAM inference measurement.")
    report.append("")
    report.append("Commands:")
    report.append("---")
    report.append("GHDL_PREFIX=%s yosys -m %s -p \"%s\"" % (prefix, plugin, script_wrap))
    report.append("---")
    report.append("")
    if counts_def is not None:
        report.append("Default core counts:")
        report.append("")
        for k in sorted(counts_def):
            report.append("* %s: %d" % (k, counts_def[k]))
        report.append("")
    report.append("Wrapper counts (non-constant image):")
    report.append("")
    for k in sorted(counts_wrap):
        report.append("* %s: %d" % (k, counts_wrap[k]))
    ram = counts_wrap.get("SB_RAM40_4K", 0)
    report.append("")
    report.append("SB_RAM40_4K count: %d. Expected order: imem 2048x32 = 16 "
                  "blocks, dmem 4096x8 = 8 blocks (two byte banks)." % ram)
    if ram == 0:
        report.append("")
        report.append("WARNING: no block RAM inferred. See docs/RTL_FINDINGS.md.")
    counts = counts_wrap
    np = shutil.which("nextpnr-ice40")
    if np:
        for dev, pkg, freq in (("hx8k", "ct256", 50), ("up5k", "sg48", 50)):
            asc = build / ("core_%s.asc" % dev)
            cmd = [np, "--" + dev, "--package", pkg, "--json", str(build / "core.json"),
                   "--pcf-allow-unconstrained", "--freq", str(freq), "--seed", "1",
                   "--asc", str(asc)]
            try:
                nr = run(cmd, ROOT, ns.timeout, env)
            except subprocess.TimeoutExpired:
                report.append("* nextpnr %s: timed out" % dev)
                continue
            (build / ("nextpnr_%s.log" % dev)).write_text(nr.stdout)
            d = parse_nextpnr(nr.stdout)
            if nr.returncode != 0 and not d.get("fmax_mhz"):
                report.append("* nextpnr %s: FAILED (see nextpnr_%s.log)" % (dev, dev))
                continue
            report.append("* nextpnr %s (%s, target %d MHz, seed 1): Fmax %s MHz, %s"
                          % (dev, pkg, freq, d.get("fmax_mhz", "?"), d.get("at_target", "")))
            if nr.returncode != 0:
                report.append("  (nextpnr exits nonzero on a timing failure; this is an estimate, not a tuned result.)")
            if d.get("critical_path"):
                report.append("---")
                report.append(d["critical_path"])
                report.append("---")
    else:
        report.append("* nextpnr-ice40 not found: no Fmax estimate.")
    (build / "report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
