#!/usr/bin/env python3
"""Cross-check the ISA documentation, VHDL constants package, assembler and
golden model.  Any disagreement is a hard failure.

Usage:
    python3 tools/check_consistency.py
    python3 tools/check_consistency.py --self-test

The negative self-test copies the relevant tree to a temporary directory,
mutates one opcode in the VHDL package, and verifies that the checker fails.
"""

from __future__ import annotations

import argparse
import inspect
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import assembler  # noqa: E402
import isa_sim  # noqa: E402

CHECKS = 0
ERRORS: List[str] = []


def check(cond: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        ERRORS.append(message)


def check_eq(got, want, message: str) -> None:
    check(got == want, "%s: got %r, want %r" % (message, got, want))


def read(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def md_table(path: Path, begin: str, end: str) -> List[Dict[str, str]]:
    text = read(path)
    if begin not in text or end not in text:
        raise SystemExit("missing marker %s or %s in %s" % (begin, end, path))
    block = text.split(begin, 1)[1].split(end, 1)[0]
    lines = [l.strip() for l in block.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        raise SystemExit("empty table between %s and %s in %s" % (begin, end, path))
    def cells(line: str) -> List[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]
    header = cells(lines[0])
    rows: List[Dict[str, str]] = []
    for line in lines[2:]:
        c = cells(line)
        if len(c) != len(header):
            raise SystemExit("bad table row in %s: %r" % (path, line))
        rows.append(dict(zip(header, c)))
    return rows


def vhdl_constants(path: Path) -> Dict[str, str]:
    text = read(path)
    out: Dict[str, str] = {}
    for m in re.finditer(r"constant\s+([A-Za-z0-9_]+)\s*:\s*([^;]*?):=\s*([^;]+);", text, re.S):
        out[m.group(1)] = m.group(3).strip()
    return out


def vhdl_int(v: str) -> Optional[int]:
    v = v.strip()
    m = re.fullmatch(r'x"([0-9A-Fa-f]+)"', v)
    if m:
        return int(m.group(1), 16)
    m = re.fullmatch(r"16#([0-9A-Fa-f]+)#", v)
    if m:
        return int(m.group(1), 16)
    m = re.fullmatch(r"0x([0-9A-Fa-f]+)", v)
    if m:
        return int(m.group(1), 16)
    m = re.fullmatch(r"[0-9]+", v)
    if m:
        return int(v)
    return None


def parse_hex_cell(s: str) -> Optional[int]:
    s = s.strip()
    if s.startswith("0x"):
        return int(s, 16)
    if s == "RAM_BYTES-1":
        return None
    return None


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def run_checks() -> None:
    isa = ROOT / "docs" / "ISA.md"
    abi = ROOT / "docs" / "ABI.md"
    pkg = ROOT / "rtl" / "minicpu16_pkg.vhd"

    # ---------------------------------------------------------------- opcodes
    op_rows = md_table(isa, "<!-- BEGIN OPCODES -->", "<!-- END OPCODES -->")
    doc_ops: Dict[str, Tuple[int, str]] = {}
    for r in op_rows:
        mnem = r["Mnemonic"]
        if re.fullmatch(r"[A-Z][A-Z0-9]*", mnem) and r["Format"] in ("R", "I", "S", "B", "UJ", "HALT"):
            doc_ops[mnem] = (int(r["Opcode"], 16), r["Format"])
    asm_ops = {k: (v[0], v[1]) for k, v in assembler.OPCODE_TABLE.items()}
    sim_ops = {v[0]: (k, v[1]) for k, v in isa_sim.OPCODE_TABLE.items()}
    check_eq(sorted(doc_ops), sorted(asm_ops), "opcode mnemonic set: docs vs assembler")
    check_eq(sorted(doc_ops), sorted(sim_ops), "opcode mnemonic set: docs vs golden model")
    for name in sorted(doc_ops):
        check_eq(doc_ops[name][0], asm_ops[name][0], "opcode %s: docs vs assembler" % name)
        check_eq(doc_ops[name][1], asm_ops[name][1], "format %s: docs vs assembler" % name)
        check_eq(doc_ops[name][0], sim_ops[name][0], "opcode %s: docs vs golden model" % name)
        check_eq(doc_ops[name][1], sim_ops[name][1], "format %s: docs vs golden model" % name)

    # ---------------------------------------------------------------- fields
    fld_rows = md_table(isa, "<!-- BEGIN FIELDS -->", "<!-- END FIELDS -->")
    doc_fields = {r["Field"]: (int(r["MSB"]), int(r["LSB"])) for r in fld_rows}
    vc = vhdl_constants(pkg)
    for f, (msb, lsb) in doc_fields.items():
        vf = "IMM" if f == "IMM16" else f
        check_eq(vhdl_int(vc.get(vf + "_MSB", "")), msb, "VHDL %s_MSB vs docs" % vf)
        check_eq(vhdl_int(vc.get(vf + "_LSB", "")), lsb, "VHDL %s_LSB vs docs" % vf)

    # ---------------------------------------------------------------- formats
    fmt_rows = md_table(isa, "<!-- BEGIN FORMATS -->", "<!-- END FORMATS -->")
    doc_formats = {r["Format"] for r in fmt_rows}
    check_eq(doc_formats, {"R", "I", "S", "B", "UJ"}, "format table names")
    for name, (_, fmt) in asm_ops.items():
        check(fmt in doc_formats or fmt == "HALT", "assembler format %s for %s not documented" % (fmt, name))
        check(fmt in doc_formats or fmt == "HALT", "model format %s for %s not documented" % (fmt, name))

    # ---------------------------------------------------------------- VHDL opcodes
    for name, (op, _) in asm_ops.items():
        key = "OP_" + name
        check(key in vc, "VHDL constant %s missing" % key)
        if key in vc:
            check_eq(vhdl_int(vc[key]), op, "VHDL %s vs assembler %s" % (key, name))
    check_eq(vhdl_int(vc.get("OP_RESERVED_LAST", "")), 0x2F, "VHDL OP_RESERVED_LAST")
    check_eq(vhdl_int(vc.get("INSTR_HALT", "")), 0x00000000, "VHDL INSTR_HALT")
    check_eq(vhdl_int(vc.get("INSTR_NOP", "")), 0x01000000, "VHDL INSTR_NOP")

    # HALT / NOP words through the assembler and the model
    img = assembler.assemble_text("halt\nnop\n")
    check_eq(img.instr_words[0], 0x00000000, "assembler HALT word")
    check_eq(img.instr_words[1], 0x01000000, "assembler NOP word")
    check_eq(isa_sim.HALT_WORD, 0x00000000, "golden model HALT word")
    check_eq(isa_sim.decode(0x00000000).name, "HALT", "golden model decodes HALT")

    # ---------------------------------------------------------------- registers
    abi_rows = md_table(abi, "<!-- BEGIN ABI_REGISTERS -->", "<!-- END ABI_REGISTERS -->")
    check_eq(len(abi_rows), 16, "ABI register table has 16 rows")
    for r in abi_rows:
        regno = int(r["Register"].lstrip("Rr"))
        aliases = [a.strip() for a in r["Alias"].split("/")]
        for a in aliases:
            check(a in assembler.REG_ALIASES, "ABI alias %r missing from assembler" % a)
            if a in assembler.REG_ALIASES:
                check_eq(assembler.REG_ALIASES[a], regno, "ABI alias %r register" % a)
            key = "REG_" + a.upper()
            check(key in vc, "VHDL constant %s missing" % key)
            if key in vc:
                check_eq(vhdl_int(vc[key]), regno, "VHDL %s vs ABI" % key)
    role_text = {int(r["Register"].lstrip("Rr")): r for r in abi_rows}
    check("constant 0" in role_text[0]["Role"], "R0 role text")
    for reg in (1, 2, 3, 4):
        check("argument" in role_text[reg]["Role"], "R%d role mentions argument" % reg)
    check("return value" in role_text[1]["Role"], "R1 role mentions return value")
    for reg in range(9, 14):
        check("callee-saved" in role_text[reg]["Role"], "R%d role callee-saved" % reg)
        check("yes" in role_text[reg]["Preserved across a call"], "R%d preserved yes" % reg)
    check("stack pointer" in role_text[14]["Role"], "SP role")
    check("link" in role_text[15]["Role"], "LR role")

    # ---------------------------------------------------------------- pseudos
    ps_rows = md_table(isa, "<!-- BEGIN PSEUDOS -->", "<!-- END PSEUDOS -->")
    doc_ps = {r["Pseudo"].split()[0].upper(): r["Expansion"] for r in ps_rows}
    check_eq(sorted(doc_ps), sorted(assembler.PSEUDO_TABLE), "pseudo-instruction list")
    for name, expansion in doc_ps.items():
        check_eq(norm(expansion), norm(assembler.PSEUDO_TABLE[name]),
                 "pseudo expansion %s" % name)

    # ---------------------------------------------------------------- memory map
    mm_rows = md_table(isa, "<!-- BEGIN MEMORY_MAP -->", "<!-- END MEMORY_MAP -->")
    mm = {r["Name"]: r for r in mm_rows}
    expected = {
        "UART_TX": 0xFF00, "UART_STATUS": 0xFF02, "GPIO_OUT": 0xFF04,
        "GPIO_IN": 0xFF06, "TIMER": 0xFF08, "MMIO_RESERVED": 0xFF0A,
    }
    for name, addr in expected.items():
        check(name in mm, "memory map missing %s" % name)
        if name in mm:
            check_eq(parse_hex_cell(mm[name]["Start"]), addr, "memory map %s start" % name)
    check_eq(parse_hex_cell(mm["UNMAPPED"]["End"]), 0xFEFF, "unmapped end")
    check_eq(parse_hex_cell(mm["MMIO_RESERVED"]["End"]), 0xFFFF, "MMIO reserved end")
    check_eq(mm["RAM"]["Start"], "0x0000", "RAM start")
    check_eq(mm["RAM"]["End"], "RAM_BYTES-1", "RAM end expression")
    check_eq(mm["UNMAPPED"]["Start"], "RAM_BYTES", "unmapped start expression")
    for name, addr in expected.items():
        check_eq(assembler.IO_SYMBOLS.get(name), addr, "assembler IO symbol %s" % name)
    for name, addr in expected.items():
        check_eq(isa_sim.MMIO.get(name), addr, "golden model MMIO %s" % name)
    check_eq(vhdl_int(vc.get("UART_TX_ADDR", "")), 0xFF00, "VHDL UART_TX_ADDR")
    check_eq(vhdl_int(vc.get("UART_STATUS_ADDR", "")), 0xFF02, "VHDL UART_STATUS_ADDR")
    check_eq(vhdl_int(vc.get("GPIO_OUT_ADDR", "")), 0xFF04, "VHDL GPIO_OUT_ADDR")
    check_eq(vhdl_int(vc.get("GPIO_IN_ADDR", "")), 0xFF06, "VHDL GPIO_IN_ADDR")
    check_eq(vhdl_int(vc.get("TIMER_ADDR", "")), 0xFF08, "VHDL TIMER_ADDR")
    check_eq(vhdl_int(vc.get("MMIO_RSVD_START", "")), 0xFF0A, "VHDL MMIO_RSVD_START")
    check_eq(vhdl_int(vc.get("MMIO_BASE", "")), 0xFF00, "VHDL MMIO_BASE")
    check_eq(vhdl_int(vc.get("MMIO_LAST", "")), 0xFFFF, "VHDL MMIO_LAST")
    check_eq(vhdl_int(vc.get("RAM_BYTES_MAX", "")), 0xFF00, "VHDL RAM_BYTES_MAX")

    # ---------------------------------------------------------------- defaults
    check_eq(vhdl_int(vc.get("INSTR_WORDS", "")), isa_sim.Config().instr_words, "INSTR_WORDS default")
    check_eq(vhdl_int(vc.get("RAM_BYTES", "")), isa_sim.Config().ram_bytes, "RAM_BYTES default")
    check_eq(inspect.signature(assembler.assemble_text).parameters["instr_words"].default,
             isa_sim.Config().instr_words, "assembler INSTR_WORDS default")
    check_eq(inspect.signature(assembler.assemble_text).parameters["ram_bytes"].default,
             isa_sim.Config().ram_bytes, "assembler RAM_BYTES default")
    check_eq(vhdl_int(vc.get("UART_CYCLES_PER_BYTE_DEFAULT", "")),
             isa_sim.UART_CYCLES_DEFAULT, "UART cycles default")
    check_eq(assembler.IO_SYMBOLS.get("UART_CYCLES_PER_BYTE"), None, "IO table has no cycle constant")

    # ---------------------------------------------------------------- compare table
    cmp_rows = md_table(isa, "<!-- BEGIN COMPARE_TABLE -->", "<!-- END COMPARE_TABLE -->")
    check_eq(len(cmp_rows), 8, "compare table rows")
    for r in cmp_rows:
        a = int(r["a"], 16); b = int(r["b"], 16)
        check_eq(isa_sim.alu_op("SLT", a, b, 0), int(r["SLT"]), "compare SLT %s %s" % (r["a"], r["b"]))
        check_eq(isa_sim.alu_op("SLTU", a, b, 0), int(r["SLTU"]), "compare SLTU")
        check_eq(1 if isa_sim.branch_taken("BLT", a, b) else 0, int(r["BLT"]), "compare BLT")
        check_eq(1 if isa_sim.branch_taken("BLTU", a, b) else 0, int(r["BLTU"]), "compare BLTU")
        check_eq(1 if isa_sim.branch_taken("BGE", a, b) else 0, int(r["BGE"]), "compare BGE")
        check_eq(1 if isa_sim.branch_taken("BGEU", a, b) else 0, int(r["BGEU"]), "compare BGEU")

    # ---------------------------------------------------------------- reserved rows
    reserved = {r["Opcode"] for r in op_rows if "reserved" in r["Mnemonic"].lower()}
    check_eq(reserved, {"0x21", "0x22", "0x23", "0x24", "0x25", "0x26", "0x2F"},
             "reserved opcode rows")


def self_test() -> int:
    print("negative self-test: copying tree and mutating OP_ADD...")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for sub in ("docs", "rtl", "tools"):
            shutil.copytree(ROOT / sub, tmp / sub,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        pkg = tmp / "rtl" / "minicpu16_pkg.vhd"
        text = pkg.read_text(encoding="utf-8")
        new = text.replace('constant OP_ADD   : std_logic_vector(7 downto 0) := x"01";',
                           'constant OP_ADD   : std_logic_vector(7 downto 0) := x"03";', 1)
        if new == text:
            print("negative self-test: FAILED to apply mutation")
            return 1
        pkg.write_text(new, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(tmp / "tools" / "check_consistency.py")],
                              cwd=str(tmp), capture_output=True, text=True)
        if proc.returncode == 0:
            print("negative self-test: FAILED (checker did not notice the mutation)")
            print(proc.stdout)
            return 1
        if "OP_ADD" not in proc.stdout:
            print("negative self-test: FAILED (wrong failure reason)")
            print(proc.stdout)
            return 1
        print("negative self-test: PASS (mutation of OP_ADD detected, exit %d)" % proc.returncode)
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="MiniCPU-16 consistency checker")
    p.add_argument("--self-test", action="store_true", help="run the negative mutation self-test")
    ns = p.parse_args(argv)
    if ns.self_test:
        return self_test()

    run_checks()
    if ERRORS:
        print("consistency: FAIL (%d error(s), %d checks)" % (len(ERRORS), CHECKS))
        for e in ERRORS:
            print("  - " + e)
        return 1
    print("consistency: PASS (%d checks)" % CHECKS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
