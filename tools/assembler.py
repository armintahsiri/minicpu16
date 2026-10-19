#!/usr/bin/env python3
"""MiniCPU-16 assembler (Phase 4).

Python 3 standard library only.  The assembler is deliberately small and
deterministic: one pass for layout, one pass for encoding, no relocations and no
linker.  See docs/ISA.md and docs/ABI.md for the normative specification.

Usage:
    python3 tools/assembler.py prog.s --vhdl build/prog_pkg.vhd --listing build/prog.lst
    python3 tools/assembler.py crt0.s main.s --hex build/prog

Outputs:
    --vhdl FILE     VHDL package with instruction ROM and two data byte banks
    --hex PREFIX    PREFIX.hex (instructions) and PREFIX.data.hex (data bytes)
    --mem PREFIX    PREFIX.mem (combined, address-tagged image)
    --listing FILE  address / encoding / source listing
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Tables (the consistency checker cross-checks these against docs/ISA.md,
# rtl/minicpu16_pkg.vhd and tools/isa_sim.py).
# ---------------------------------------------------------------------------

REG_ALIASES: Dict[str, int] = {
    "zero": 0,
    "a0": 1, "a1": 2, "a2": 3, "a3": 4,
    "t0": 5, "t1": 6, "t2": 7, "t3": 8,
    "s0": 9, "s1": 10, "s2": 11, "s3": 12, "s4": 13,
    "sp": 14, "lr": 15, "ra": 15,
}
for _i in range(16):
    REG_ALIASES["r%d" % _i] = _i

# mnemonic -> (opcode, format)
OPCODE_TABLE: Dict[str, Tuple[int, str]] = {
    "HALT":  (0x00, "HALT"),
    "ADD":   (0x01, "R"),
    "SUB":   (0x02, "R"),
    "AND":   (0x03, "R"),
    "OR":    (0x04, "R"),
    "XOR":   (0x05, "R"),
    "SLL":   (0x06, "R"),
    "SRL":   (0x07, "R"),
    "SRA":   (0x08, "R"),
    "SLT":   (0x09, "R"),
    "SLTU":  (0x0A, "R"),
    "ADDI":  (0x0B, "I"),
    "ANDI":  (0x0C, "I"),
    "ORI":   (0x0D, "I"),
    "XORI":  (0x0E, "I"),
    "SLLI":  (0x0F, "I"),
    "SRLI":  (0x10, "I"),
    "SRAI":  (0x11, "I"),
    "SLTI":  (0x12, "I"),
    "SLTIU": (0x13, "I"),
    "LB":    (0x14, "I"),
    "LBU":   (0x15, "I"),
    "LW":    (0x16, "I"),
    "SB":    (0x17, "S"),
    "SW":    (0x18, "S"),
    "BEQ":   (0x19, "B"),
    "BNE":   (0x1A, "B"),
    "BLT":   (0x1B, "B"),
    "BGE":   (0x1C, "B"),
    "BLTU":  (0x1D, "B"),
    "BGEU":  (0x1E, "B"),
    "JAL":   (0x1F, "UJ"),
    "JALR":  (0x20, "I"),
}

# Pseudo-instructions: name -> expansion text used in docs/ISA.md.  The actual
# expansion is implemented in _expand_pseudo; the strings are kept in sync by
# tools/check_consistency.py.
PSEUDO_TABLE: Dict[str, str] = {
    "LI":   "ADDI rd,R0,imm",
    "LA":   "ADDI rd,R0,sym",
    "MV":   "ADD rd,rs,R0",
    "NOT":  "XORI rd,rs,0xFFFF",
    "NEG":  "SUB rd,R0,rs",
    "NOP":  "ADD R0,R0,R0",
    "J":    "JAL R0,target",
    "CALL": "JAL R15,target",
    "RET":  "JALR R0,R15,0",
    "BGT":  "BLT rt,rs,target",
    "BLE":  "BGE rt,rs,target",
    "BGTU": "BLTU rt,rs,target",
    "BLEU": "BGEU rt,rs,target",
    "BEQZ": "BEQ rs,R0,target",
    "BNEZ": "BNE rs,R0,target",
    "BLTZ": "BLT rs,R0,target",
    "BGEZ": "BGE rs,R0,target",
    "BGTZ": "BLT R0,rs,target",
    "BLEZ": "BGE R0,rs,target",
    "SUBI": "ADDI rd,rs,(-imm mod 2^16)",
    "SEQZ": "SLTIU rd,rs,1",
    "SNEZ": "SLTU rd,R0,rs",
    "SLTZ": "SLT rd,rs,R0",
    "SGTZ": "SLT rd,R0,rs",
}

IO_SYMBOLS: Dict[str, int] = {
    "UART_TX": 0xFF00,
    "UART_STATUS": 0xFF02,
    "GPIO_OUT": 0xFF04,
    "GPIO_IN": 0xFF06,
    "TIMER": 0xFF08,
    "MMIO_BASE": 0xFF00,
    "MMIO_LAST": 0xFFFF,
}

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class AsmError(Exception):
    def __init__(self, message: str, file: Optional[str] = None, line: Optional[int] = None):
        self.message = message
        self.file = file
        self.line = line
        super().__init__(self.__str__())

    def __str__(self) -> str:
        if self.file is not None and self.line is not None:
            return "%s:%d: error: %s" % (self.file, self.line, self.message)
        if self.file is not None:
            return "%s: error: %s" % (self.file, self.message)
        return "error: %s" % self.message


@dataclass
class Symbol:
    name: str
    value: int
    kind: str          # const | code | data
    file: Optional[str] = None
    line: Optional[int] = None


@dataclass
class InstrItem:
    pc: int
    mnemonic: str
    operands: List[str]
    source: str
    file: str
    line: int


@dataclass
class DataPatch:
    addr: int
    expr: str
    width: int
    source: str
    file: str
    line: int


@dataclass
class Image:
    instr_words: List[int]
    program_words: int
    data: bytes
    data_size: int
    bss_start: int
    bss_end: int
    symbols: Dict[str, Symbol]
    listing: List[str]
    ram_bytes: int
    instr_capacity: int
    source_names: List[str] = field(default_factory=list)

    # -- textual outputs ---------------------------------------------------
    def hex_text(self) -> str:
        return "".join("%08X\n" % w for w in self.instr_words[: self.program_words])

    def data_hex_text(self) -> str:
        return "".join("%02X\n" % b for b in self.data[: self.data_size])

    def mem_text(self) -> str:
        out = ["# MiniCPU-16 image v1", "# instructions (32-bit hex)"]
        for i, w in enumerate(self.instr_words[: self.program_words]):
            out.append("@I%04X %08X" % (i, w))
        out.append("# data (8-bit hex)")
        for i, b in enumerate(self.data[: self.data_size]):
            out.append("@D%04X %02X" % (i, b))
        return "\n".join(out) + "\n"

    def vhdl_package(self, package_name: str = "minicpu16_program_pkg") -> str:
        lines: List[str] = []
        lines.append("-- Generated by tools/assembler.py.  Do not edit.")
        lines.append("-- MiniCPU-16 program image: %d instruction(s), %d data byte(s)." %
                     (self.program_words, self.data_size))
        lines.append("library ieee;")
        lines.append("use ieee.std_logic_1164.all;")
        lines.append("use work.minicpu16_pkg.all;")
        lines.append("")
        lines.append("package %s is" % package_name)
        lines.append("  constant PROGRAM_WORDS : integer := %d;" % self.program_words)
        lines.append("  constant DATA_BYTES    : integer := %d;" % self.data_size)
        lines.append("  constant BSS_START     : integer := %d;" % self.bss_start)
        lines.append("  constant BSS_END       : integer := %d;" % self.bss_end)
        lines.append("  constant RAM_BYTES_CFG : integer := %d;" % self.ram_bytes)
        lines.append("  constant INSTR_INIT : instr_rom_t := (")
        entries = ["    %d => x\"%08X\"" % (i, w) for i, w in enumerate(self.instr_words[: self.program_words]) if w != 0]
        entries.append("    others => x\"00000000\"")
        lines.append(",\n".join(entries) + ");")
        for name, offset, default in (("DATA_EVEN", 0, "x\"00\""), ("DATA_ODD", 1, "x\"00\"")):
            lines.append("  constant %s : byte_bank_t := (" % name)
            ents = []
            for addr in range(offset, self.data_size, 2):
                b = self.data[addr]
                if b != 0:
                    ents.append("    %d => x\"%02X\"" % (addr // 2, b))
            ents.append("    others => %s" % default)
            lines.append(",\n".join(ents) + ");")
        lines.append("end package %s;" % package_name)
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lexing helpers
# ---------------------------------------------------------------------------

def strip_comment(line: str) -> str:
    """Remove # or // comments, respecting double-quoted strings."""
    out: List[str] = []
    in_str = False
    esc = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
                out.append(c)
            elif c == "#":
                break
            elif c == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            else:
                out.append(c)
        i += 1
    return "".join(out)


def split_operands(text: str, file: str, line_no: int) -> List[str]:
    if not text.strip():
        return []
    parts: List[str] = []
    depth = 0
    in_str = False
    esc = False
    start = 0
    for i, c in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                raise AsmError("unbalanced ')'", file, line_no)
        elif c == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    if depth != 0:
        raise AsmError("unbalanced '('", file, line_no)
    parts.append(text[start:].strip())
    if any(p == "" for p in parts):
        raise AsmError("empty operand", file, line_no)
    return parts


def parse_string_literal(text: str, file: str, line_no: int) -> str:
    s = text.strip()
    if not s.startswith('"'):
        raise AsmError("expected a quoted string", file, line_no)
    out: List[str] = []
    i = 1
    while i < len(s):
        c = s[i]
        if c == '"':
            if s[i + 1:].strip():
                raise AsmError("junk after string literal", file, line_no)
            return "".join(out)
        if c == "\\":
            i += 1
            if i >= len(s):
                raise AsmError("bad escape at end of string", file, line_no)
            e = s[i]
            if e == "n":
                out.append("\n")
            elif e == "r":
                out.append("\r")
            elif e == "t":
                out.append("\t")
            elif e == "0":
                out.append("\0")
            elif e == "\\":
                out.append("\\")
            elif e == '"':
                out.append('"')
            elif e == "x":
                h = s[i + 1:i + 3]
                if len(h) != 2 or not re.fullmatch(r"[0-9A-Fa-f]{2}", h):
                    raise AsmError("bad \\xNN escape", file, line_no)
                out.append(chr(int(h, 16)))
                i += 2
            else:
                raise AsmError("unknown escape \\%s" % e, file, line_no)
        else:
            out.append(c)
        i += 1
    raise AsmError("unterminated string literal", file, line_no)


# ---------------------------------------------------------------------------
# Expression parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"\s*(0[xX][0-9A-Fa-f]+|0[bB][01]+|[0-9]+|[A-Za-z_.$][A-Za-z0-9_.$]*|<<|>>|[-+*/%&|^~()])"
)


class ExprParser:
    def __init__(self, text: str, symbols: Dict[str, Symbol], current: int, current_kind: str,
                 file: str, line: int):
        self.text = text
        self.symbols = symbols
        self.current = current
        self.current_kind = current_kind
        self.file = file
        self.line = line
        self.tokens = self._tokenize(text)
        self.pos = 0

    def _tokenize(self, text: str) -> List[str]:
        toks: List[str] = []
        i = 0
        while i < len(text):
            if text[i].isspace():
                i += 1
                continue
            m = _TOKEN_RE.match(text, i)
            if not m:
                raise AsmError("bad character %r in expression %r" % (text[i], text), self.file, self.line)
            toks.append(m.group(1))
            i = m.end()
        return toks

    def peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        t = self.peek()
        if t is None:
            raise AsmError("unexpected end of expression", self.file, self.line)
        self.pos += 1
        return t

    def parse(self) -> Tuple[int, Set[str]]:
        if not self.tokens:
            raise AsmError("empty expression", self.file, self.line)
        v, kinds = self._or()
        if self.peek() is not None:
            raise AsmError("unexpected token %r in expression %r" % (self.peek(), self.text),
                           self.file, self.line)
        return v, kinds

    def _or(self):
        v, k = self._xor()
        while self.peek() == "|":
            self.take()
            r, k2 = self._xor()
            v = v | r
            k |= k2
        return v, k

    def _xor(self):
        v, k = self._and()
        while self.peek() == "^":
            self.take()
            r, k2 = self._and()
            v = v ^ r
            k |= k2
        return v, k

    def _and(self):
        v, k = self._shift()
        while self.peek() == "&":
            self.take()
            r, k2 = self._shift()
            v = v & r
            k |= k2
        return v, k

    def _shift(self):
        v, k = self._add()
        while self.peek() in ("<<", ">>"):
            op = self.take()
            r, k2 = self._add()
            if r < 0:
                raise AsmError("negative shift count", self.file, self.line)
            v = (v << r) if op == "<<" else (v >> r)
            k |= k2
        return v, k

    def _add(self):
        v, k = self._mul()
        while self.peek() in ("+", "-"):
            op = self.take()
            r, k2 = self._mul()
            v = v + r if op == "+" else v - r
            k |= k2
        return v, k

    def _mul(self):
        v, k = self._unary()
        while self.peek() in ("*", "/", "%"):
            op = self.take()
            r, k2 = self._unary()
            if op == "*":
                v = v * r
            elif r == 0:
                raise AsmError("division by zero in expression", self.file, self.line)
            else:
                q = int(v / r)  # truncate toward zero
                v = q if op == "/" else v - q * r
            k |= k2
        return v, k

    def _unary(self):
        t = self.peek()
        if t in ("-", "+", "~"):
            self.take()
            v, k = self._unary()
            if t == "-":
                v = -v
            elif t == "~":
                v = ~v
            return v, k
        return self._primary()

    def _primary(self):
        t = self.take()
        if t == "(":
            v, k = self._or()
            if self.take() != ")":
                raise AsmError("expected ')'", self.file, self.line)
            return v, k
        if t == ".":
            return self.current, {self.current_kind}
        if re.fullmatch(r"0[xX][0-9A-Fa-f]+", t):
            return int(t, 16), set()
        if re.fullmatch(r"0[bB][01]+", t):
            return int(t, 2), set()
        if re.fullmatch(r"[0-9]+", t):
            return int(t, 10), set()
        if re.fullmatch(r"[A-Za-z_.$][A-Za-z0-9_.$]*", t):
            sym = self.symbols.get(t)
            if sym is None:
                raise AsmError("undefined symbol %r" % t, self.file, self.line)
            kinds = set() if sym.kind == "const" else {sym.kind}
            return sym.value, kinds
        raise AsmError("unexpected token %r" % t, self.file, self.line)


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

class Assembler:
    def __init__(self, instr_words: int = 2048, ram_bytes: int = 4096):
        if instr_words <= 0:
            raise AsmError("INSTR_WORDS must be positive")
        if ram_bytes <= 0 or ram_bytes > 0xFF00:
            raise AsmError("RAM_BYTES must be in 1..0xFF00")
        if ram_bytes % 2 != 0:
            raise AsmError("RAM_BYTES must be even")
        self.instr_capacity = instr_words
        self.ram_bytes = ram_bytes
        self.text_slots: List[Optional[int]] = [None] * instr_words
        self.text_addr = 0
        self.max_text = 0
        self.data = bytearray(ram_bytes)
        self.data_addr = 0
        self.max_data = 0
        self.bss_start: Optional[int] = None
        self.bss_addr = 0
        self.bss_end = 0
        self.section = "text"
        self.symbols: Dict[str, Symbol] = {}
        self.items: List[InstrItem] = []
        self.patches: List[DataPatch] = []
        self.listing: List[str] = []
        self.source_names: List[str] = []
        self._define_builtins()

    # -- symbols -----------------------------------------------------------
    def _define_builtins(self) -> None:
        builtins = {
            "RAM_BYTES": self.ram_bytes,
            "INSTR_WORDS": self.instr_capacity,
            "UART_CYCLES_PER_BYTE": 10,
        }
        builtins.update(IO_SYMBOLS)
        for name, value in builtins.items():
            self.symbols[name] = Symbol(name, value, "const")

    def define(self, name: str, value: int, kind: str, file: str, line: int) -> None:
        if name in self.symbols:
            old = self.symbols[name]
            raise AsmError("duplicate symbol %r (first defined at %s:%s)" %
                           (name, old.file, old.line), file, line)
        self.symbols[name] = Symbol(name, value, kind, file, line)

    def _eval(self, expr: str, file: str, line: int, current: Optional[int] = None) -> Tuple[int, Set[str]]:
        if current is None:
            current = self._current_addr()
        kind = "code" if self.section == "text" else "data"
        return ExprParser(expr, self.symbols, current, kind, file, line).parse()

    def _current_addr(self) -> int:
        if self.section == "text":
            return self.text_addr
        if self.section == "bss":
            return self.bss_addr
        return self.data_addr

    # -- parsing -----------------------------------------------------------
    def parse(self, name: str, text: str) -> None:
        self.source_names.append(name)
        for line_no, raw in enumerate(text.splitlines(), 1):
            line = strip_comment(raw)
            if not line.strip():
                continue
            # labels (possibly several) at the start of the line
            while True:
                m = re.match(r"^\s*([A-Za-z_.$][A-Za-z0-9_.$]*):(.*)$", line)
                if not m:
                    break
                label = m.group(1)
                kind = "code" if self.section == "text" else "data"
                self.define(label, self._current_addr(), kind, name, line_no)
                line = m.group(2)
                if not line.strip():
                    break
            s = line.strip()
            if not s:
                continue
            if s.startswith("."):
                self._directive(s, name, line_no, raw)
            else:
                self._instruction(s, name, line_no, raw)

    def _directive(self, s: str, file: str, line: int, raw: str) -> None:
        m = re.match(r"^(\.[A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", s)
        if not m:
            raise AsmError("bad directive", file, line)
        d = m.group(1).lower()
        rest = m.group(2).strip()
        if d in (".text", ".data", ".bss"):
            self._switch_section(d[1:], file, line)
            if rest:
                raise AsmError("junk after %s" % d, file, line)
            return
        if d == ".section":
            if not rest:
                raise AsmError(".section needs a name", file, line)
            nm = rest.strip().lower()
            if nm in (".text", "text"):
                self._switch_section("text", file, line)
            elif nm in (".data", "data", ".rodata", "rodata"):
                self._switch_section("data", file, line)
            elif nm in (".bss", "bss"):
                self._switch_section("bss", file, line)
            else:
                raise AsmError("unknown section %r" % rest, file, line)
            return
        if d in (".globl", ".global", ".type", ".size", ".file", ".ident"):
            return
        if d == ".equ":
            m2 = re.match(r"^([A-Za-z_.$][A-Za-z0-9_.$]*)\s*,?\s*(.+)$", rest)
            if not m2:
                raise AsmError(".equ needs NAME, value", file, line)
            val, _ = self._eval(m2.group(2), file, line)
            self.define(m2.group(1), val, "const", file, line)
            return
        if d == ".org":
            if not rest:
                raise AsmError(".org needs an address", file, line)
            val, _ = self._eval(rest, file, line)
            self._org(val, file, line)
            return
        if d == ".align":
            if not rest:
                raise AsmError(".align needs a power of two exponent", file, line)
            n, _ = self._eval(rest, file, line)
            if n < 0 or n > 15:
                raise AsmError(".align exponent must be 0..15", file, line)
            self._align(1 << n, file, line)
            return
        if d in (".byte", ".word"):
            if not rest:
                raise AsmError("%s needs at least one value" % d, file, line)
            width = 1 if d == ".byte" else 2
            for op in split_operands(rest, file, line):
                if self.section == "text":
                    raise AsmError("%s is not allowed in .text; use .inst" % d, file, line)
                if self.section == "bss":
                    raise AsmError("%s is not allowed in .bss" % d, file, line)
                addr = self.data_addr
                self._reserve_data(width, file, line)
                self.patches.append(DataPatch(addr, op, width, raw.strip(), file, line))
            return
        if d in (".ascii", ".asciz"):
            text = parse_string_literal(rest, file, line)
            if self.section == "text":
                raise AsmError("%s is not allowed in .text" % d, file, line)
            if self.section == "bss":
                raise AsmError("%s is not allowed in .bss" % d, file, line)
            payload = text.encode("latin-1")
            if d == ".asciz":
                payload += b"\x00"
            for b in payload:
                if self.data_addr >= self.ram_bytes:
                    raise AsmError("data image exceeds RAM_BYTES (%d)" % self.ram_bytes, file, line)
                self.data[self.data_addr] = b
                self.data_addr += 1
            self.max_data = max(self.max_data, self.data_addr)
            return
        if d == ".space":
            if not rest:
                raise AsmError(".space needs a byte count", file, line)
            n, _ = self._eval(rest, file, line)
            if n < 0:
                raise AsmError(".space count must be non-negative", file, line)
            if self.section == "text":
                raise AsmError(".space is not allowed in .text", file, line)
            if self.section == "bss":
                self._reserve_bss(n, file, line)
            else:
                self._reserve_data(n, file, line)
            return
        if d == ".inst":
            if self.section != "text":
                raise AsmError(".inst is only allowed in .text", file, line)
            if not rest:
                raise AsmError(".inst needs a 32-bit value", file, line)
            val, _ = self._eval(rest, file, line)
            if val < 0 or val > 0xFFFFFFFF:
                raise AsmError(".inst value must be 0..0xFFFFFFFF", file, line)
            if self.text_addr >= self.instr_capacity:
                raise AsmError("program exceeds INSTR_WORDS (%d)" % self.instr_capacity, file, line)
            self.text_slots[self.text_addr] = val
            self.text_addr += 1
            self.max_text = max(self.max_text, self.text_addr)
            return
        raise AsmError("unknown directive %r" % d, file, line)

    def _switch_section(self, section: str, file: str, line: int) -> None:
        if section == "data" and self.bss_start is not None:
            raise AsmError("cannot return to .data after .bss (data and bss would overlap)", file, line)
        if section == "bss" and self.bss_start is None:
            self.bss_start = self.data_addr
            self.bss_addr = self.data_addr
        self.section = section

    def _org(self, value: int, file: str, line: int) -> None:
        if value < 0:
            raise AsmError(".org address must be non-negative", file, line)
        if self.section == "text":
            if value < self.text_addr:
                raise AsmError(".org cannot move backwards in .text", file, line)
            if value > self.instr_capacity:
                raise AsmError(".org beyond INSTR_WORDS (%d)" % self.instr_capacity, file, line)
            self.text_addr = value
            self.max_text = max(self.max_text, value)
        elif self.section == "data":
            if value < self.data_addr:
                raise AsmError(".org cannot move backwards in .data", file, line)
            if value > self.ram_bytes:
                raise AsmError(".org beyond RAM_BYTES (%d)" % self.ram_bytes, file, line)
            self.data_addr = value
            self.max_data = max(self.max_data, value)
        else:
            if value < self.bss_addr:
                raise AsmError(".org cannot move backwards in .bss", file, line)
            if value > self.ram_bytes:
                raise AsmError(".org beyond RAM_BYTES (%d)" % self.ram_bytes, file, line)
            self.bss_addr = value
            self.bss_end = max(self.bss_end, value)

    def _align(self, alignment: int, file: str, line: int) -> None:
        addr = self._current_addr()
        new = (addr + alignment - 1) // alignment * alignment
        if self.section == "text":
            if new > self.instr_capacity:
                raise AsmError(".align beyond INSTR_WORDS", file, line)
            self.text_addr = new
            self.max_text = max(self.max_text, new)
        elif self.section == "data":
            if new > self.ram_bytes:
                raise AsmError(".align beyond RAM_BYTES", file, line)
            self.data_addr = new
            self.max_data = max(self.max_data, new)
        else:
            if new > self.ram_bytes:
                raise AsmError(".align beyond RAM_BYTES", file, line)
            self.bss_addr = new
            self.bss_end = max(self.bss_end, new)

    def _reserve_data(self, n: int, file: str, line: int) -> None:
        if self.data_addr + n > self.ram_bytes:
            raise AsmError("data image exceeds RAM_BYTES (%d)" % self.ram_bytes, file, line)
        self.data_addr += n
        self.max_data = max(self.max_data, self.data_addr)

    def _reserve_bss(self, n: int, file: str, line: int) -> None:
        if self.bss_addr + n > self.ram_bytes:
            raise AsmError(".bss exceeds RAM_BYTES (%d)" % self.ram_bytes, file, line)
        self.bss_addr += n
        self.bss_end = max(self.bss_end, self.bss_addr)

    def _instruction(self, s: str, file: str, line: int, raw: str) -> None:
        if self.section != "text":
            raise AsmError("instruction outside .text", file, line)
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", s)
        if not m:
            raise AsmError("cannot parse instruction", file, line)
        mnemonic = m.group(1).upper()
        operands = split_operands(m.group(2), file, line)
        if self.text_addr >= self.instr_capacity:
            raise AsmError("program exceeds INSTR_WORDS (%d)" % self.instr_capacity, file, line)
        self.items.append(InstrItem(self.text_addr, mnemonic, operands, raw.strip(), file, line))
        self.text_addr += 1
        self.max_text = max(self.max_text, self.text_addr)

    # -- encoding ----------------------------------------------------------
    def _reg(self, text: str, file: str, line: int) -> int:
        key = text.strip().lower()
        if key not in REG_ALIASES:
            raise AsmError("unknown register %r" % text.strip(), file, line)
        return REG_ALIASES[key]

    def _imm(self, expr: str, file: str, line: int, current: Optional[int] = None) -> int:
        value, _ = self._eval(expr, file, line, current)
        if value < -32768 or value > 65535:
            raise AsmError("immediate out of 16-bit range: %d" % value, file, line)
        return value & 0xFFFF

    def _mem_operand(self, text: str, file: str, line: int, pc: int) -> Tuple[int, int]:
        """Return (base_reg, offset16) for 'off(reg)' or 'symbol'."""
        t = text.strip()
        m = re.match(r"^(.*)\(([^()]*)\)$", t)
        if m:
            off_text = m.group(1).strip() or "0"
            base_text = m.group(2).strip()
            base = self._reg(base_text, file, line)
            value, kinds = self._eval(off_text, file, line, pc)
            if "code" in kinds:
                raise AsmError("code label cannot be used as a data offset", file, line)
            if value < -32768 or value > 65535:
                raise AsmError("offset out of 16-bit range: %d" % value, file, line)
            return base, value & 0xFFFF
        value, kinds = self._eval(t, file, line, pc)
        if "code" in kinds:
            raise AsmError("code label cannot be used as a data address", file, line)
        if value < -32768 or value > 65535:
            raise AsmError("address out of 16-bit range: %d" % value, file, line)
        return 0, value & 0xFFFF

    def _branch(self, mnemonic: str, operands: List[str], file: str, line: int, pc: int) -> int:
        if len(operands) != 3:
            raise AsmError("%s expects rs, rt, target" % mnemonic, file, line)
        rs1 = self._reg(operands[0], file, line)
        rs2 = self._reg(operands[1], file, line)
        target, kinds = self._eval(operands[2], file, line, pc)
        if "data" in kinds:
            raise AsmError("data label cannot be used as a branch target", file, line)
        if kinds and "code" not in kinds:
            raise AsmError("branch target must be a code label or number", file, line)
        if target < 0 or target > 0xFFFF:
            raise AsmError("branch target out of 16-bit range: %d" % target, file, line)
        # PC and targets are 16-bit and wrap mod 2^16.  Every 16-bit target is
        # reachable by exactly one signed 16-bit offset, so compute the signed
        # representative of (target - (PC+1)) mod 2^16.
        off = ((target - (pc + 1) + 0x8000) & 0xFFFF) - 0x8000
        op = OPCODE_TABLE[mnemonic][0]
        return (op << 24) | (rs2 << 20) | (rs1 << 16) | (off & 0xFFFF)

    def _jal(self, operands: List[str], file: str, line: int, pc: int) -> int:
        if len(operands) != 2:
            raise AsmError("JAL expects rd, target", file, line)
        rd = self._reg(operands[0], file, line)
        target, kinds = self._eval(operands[1], file, line, pc)
        if "data" in kinds:
            raise AsmError("data label cannot be used as a jump target", file, line)
        if kinds and "code" not in kinds:
            raise AsmError("jump target must be a code label or number", file, line)
        if target < 0 or target > 0xFFFF:
            raise AsmError("jump target out of 16-bit range: %d" % target, file, line)
        off = ((target - (pc + 1) + 0x8000) & 0xFFFF) - 0x8000
        return (0x1F << 24) | (rd << 20) | (off & 0xFFFF)

    def _encode(self, item: InstrItem) -> int:
        m = item.mnemonic
        ops = item.operands
        file, line, pc = item.file, item.line, item.pc

        # Expand pseudos first.
        if m in ("LI", "LA"):
            if len(ops) != 2:
                raise AsmError("%s expects rd, value" % m, file, line)
            rd = self._reg(ops[0], file, line)
            val, kinds = self._eval(ops[1], file, line, pc)
            if m == "LA" and "code" in kinds:
                pass
            if val < -32768 or val > 65535:
                raise AsmError("value out of 16-bit range: %d" % val, file, line)
            return (0x0B << 24) | (rd << 20) | (val & 0xFFFF)
        if m == "MV":
            if len(ops) != 2:
                raise AsmError("MV expects rd, rs", file, line)
            rd = self._reg(ops[0], file, line); rs = self._reg(ops[1], file, line)
            return (0x01 << 24) | (rd << 20) | (rs << 16)
        if m == "NOT":
            if len(ops) != 2:
                raise AsmError("NOT expects rd, rs", file, line)
            rd = self._reg(ops[0], file, line); rs = self._reg(ops[1], file, line)
            return (0x0E << 24) | (rd << 20) | (rs << 16) | 0xFFFF
        if m == "NEG":
            if len(ops) != 2:
                raise AsmError("NEG expects rd, rs", file, line)
            rd = self._reg(ops[0], file, line); rs = self._reg(ops[1], file, line)
            return (0x02 << 24) | (rd << 20) | (rs << 12)
        if m == "NOP":
            if ops:
                raise AsmError("NOP takes no operands", file, line)
            return 0x01000000
        if m == "J":
            if len(ops) != 1:
                raise AsmError("J expects a target", file, line)
            return self._jal(["r0", ops[0]], file, line, pc)
        if m == "CALL":
            if len(ops) != 1:
                raise AsmError("CALL expects a target", file, line)
            return self._jal(["r15", ops[0]], file, line, pc)
        if m == "RET":
            if ops:
                raise AsmError("RET takes no operands", file, line)
            return (0x20 << 24) | (0 << 20) | (15 << 16)
        if m in ("SUBI",):
            if len(ops) != 3:
                raise AsmError("SUBI expects rd, rs, imm", file, line)
            rd = self._reg(ops[0], file, line); rs = self._reg(ops[1], file, line)
            val, _ = self._eval(ops[2], file, line, pc)
            if val == -32768:
                raise AsmError("SUBI cannot negate -32768", file, line)
            if val < -32767 or val > 32768:
                raise AsmError("SUBI immediate out of range: %d" % val, file, line)
            return (0x0B << 24) | (rd << 20) | (rs << 16) | ((-val) & 0xFFFF)
        if m in ("SEQZ", "SNEZ", "SLTZ", "SGTZ"):
            if len(ops) != 2:
                raise AsmError("%s expects rd, rs" % m, file, line)
            rd = self._reg(ops[0], file, line); rs = self._reg(ops[1], file, line)
            if m == "SEQZ":
                return (0x13 << 24) | (rd << 20) | (rs << 16) | 1
            if m == "SNEZ":
                return (0x0A << 24) | (rd << 20) | (0 << 16) | (rs << 12)
            if m == "SLTZ":
                return (0x09 << 24) | (rd << 20) | (rs << 16)
            return (0x09 << 24) | (rd << 20) | (0 << 16) | (rs << 12)
        if m in ("BEQZ", "BNEZ", "BLTZ", "BGEZ", "BGTZ", "BLEZ"):
            if len(ops) != 2:
                raise AsmError("%s expects rs, target" % m, file, line)
            rs = ops[0]
            mapping = {
                "BEQZ": ("BEQ", rs, "r0"),
                "BNEZ": ("BNE", rs, "r0"),
                "BLTZ": ("BLT", rs, "r0"),
                "BGEZ": ("BGE", rs, "r0"),
                "BGTZ": ("BLT", "r0", rs),
                "BLEZ": ("BGE", "r0", rs),
            }
            base, a, b = mapping[m]
            return self._branch(base, [a, b, ops[1]], file, line, pc)
        if m in ("BGT", "BLE", "BGTU", "BLEU"):
            if len(ops) != 3:
                raise AsmError("%s expects rs, rt, target" % m, file, line)
            base = {"BGT": "BLT", "BLE": "BGE", "BGTU": "BLTU", "BLEU": "BGEU"}[m]
            return self._branch(base, [ops[1], ops[0], ops[2]], file, line, pc)

        if m not in OPCODE_TABLE:
            raise AsmError("unknown instruction %r" % item.mnemonic, file, line)
        op, fmt = OPCODE_TABLE[m]
        if m == "HALT":
            if ops:
                raise AsmError("HALT takes no operands", file, line)
            return 0
        if fmt == "R":
            if len(ops) != 3:
                raise AsmError("%s expects rd, rs1, rs2" % m, file, line)
            rd = self._reg(ops[0], file, line)
            rs1 = self._reg(ops[1], file, line)
            rs2 = self._reg(ops[2], file, line)
            return (op << 24) | (rd << 20) | (rs1 << 16) | (rs2 << 12)
        if fmt == "I":
            if m in ("LB", "LBU", "LW"):
                if len(ops) != 2:
                    raise AsmError("%s expects rd, offset(rs1)" % m, file, line)
                rd = self._reg(ops[0], file, line)
                base, off = self._mem_operand(ops[1], file, line, pc)
                return (op << 24) | (rd << 20) | (base << 16) | off
            if len(ops) != 3:
                raise AsmError("%s expects rd, rs1, imm" % m, file, line)
            rd = self._reg(ops[0], file, line)
            rs1 = self._reg(ops[1], file, line)
            imm = self._imm(ops[2], file, line, pc)
            if m in ("SLLI", "SRLI", "SRAI"):
                if imm > 15:
                    raise AsmError("shift amount must be 0..15", file, line)
            return (op << 24) | (rd << 20) | (rs1 << 16) | imm
        if fmt == "S":
            if len(ops) != 2:
                raise AsmError("%s expects rs2, offset(rs1)" % m, file, line)
            rs2 = self._reg(ops[0], file, line)
            base, off = self._mem_operand(ops[1], file, line, pc)
            return (op << 24) | (rs2 << 20) | (base << 16) | off
        if fmt == "B":
            return self._branch(m, ops, file, line, pc)
        if fmt == "UJ":
            return self._jal(ops, file, line, pc)
        raise AsmError("internal: unhandled format %r" % fmt, file, line)

    # -- finalisation ------------------------------------------------------
    def finalize(self) -> Image:
        # data patches
        for patch in self.patches:
            value, _ = ExprParser(patch.expr, self.symbols, patch.addr,
                                  "data", patch.file, patch.line).parse()
            if patch.width == 1:
                if value < -128 or value > 255:
                    raise AsmError("byte value out of range: %d" % value, patch.file, patch.line)
                self.data[patch.addr] = value & 0xFF
            else:
                if value < -32768 or value > 65535:
                    raise AsmError("word value out of 16-bit range: %d" % value, patch.file, patch.line)
                v = value & 0xFFFF
                self.data[patch.addr] = v & 0xFF
                self.data[patch.addr + 1] = (v >> 8) & 0xFF

        # bss symbols
        data_end = self.max_data
        if self.bss_start is None:
            bss_start = data_end
            bss_end = data_end
        else:
            bss_start = min(self.bss_start, data_end) if self.bss_start <= data_end else self.bss_start
            bss_start = self.bss_start
            bss_end = max(self.bss_end, bss_start)
        if bss_end > self.ram_bytes:
            raise AsmError(".bss exceeds RAM_BYTES (%d)" % self.ram_bytes)
        self.symbols["__bss_start"] = Symbol("__bss_start", bss_start, "data")
        self.symbols["__bss_end"] = Symbol("__bss_end", bss_end, "data")

        # encode instructions
        for item in self.items:
            try:
                word = self._encode(item)
            except AsmError:
                raise
            if word < 0 or word > 0xFFFFFFFF:
                raise AsmError("encoded instruction out of 32-bit range", item.file, item.line)
            self.text_slots[item.pc] = word

        words: List[int] = []
        for i in range(self.max_text):
            w = self.text_slots[i]
            words.append(0 if w is None else w)
        # Build a listing for instructions and data patches.
        listing: List[str] = []
        by_pc = {it.pc: it for it in self.items}
        for i, w in enumerate(words):
            src = by_pc[i].source if i in by_pc else ""
            listing.append("%04X  %08X  %s" % (i, w, src))
        for patch in self.patches:
            listing.append("D%04X  %s" % (patch.addr, patch.source))
        return Image(words, self.max_text, bytes(self.data[: self.max_data]), self.max_data,
                     bss_start, bss_end, self.symbols, listing, self.ram_bytes,
                     self.instr_capacity, list(self.source_names))


def assemble_sources(sources: Sequence[Tuple[str, str]], instr_words: int = 2048,
                     ram_bytes: int = 4096) -> Image:
    asm = Assembler(instr_words=instr_words, ram_bytes=ram_bytes)
    for name, text in sources:
        asm.parse(name, text)
    return asm.finalize()


def assemble_text(text: str, filename: str = "<text>", instr_words: int = 2048,
                  ram_bytes: int = 4096) -> Image:
    return assemble_sources([(filename, text)], instr_words=instr_words, ram_bytes=ram_bytes)


def _read_with_includes(path: str, stack: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    stack = stack or []
    ap = os.path.abspath(path)
    if ap in stack:
        raise AsmError("recursive .include of %s" % path, path, 1)
    if len(stack) > 20:
        raise AsmError("too many nested .include levels", path, 1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise AsmError("cannot read %s: %s" % (path, e), path, 1)
    out: List[Tuple[str, str]] = []
    kept: List[str] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        s = strip_comment(raw).strip()
        m = re.match(r"^\.include\s+(.+)$", s)
        if m:
            # Flush the lines before the include, then splice the included file in
            # at this exact position, then continue with the current file.
            if kept:
                out.append((path, "\n".join(kept)))
                kept = []
            inc = m.group(1).strip()
            if inc.startswith('"') and inc.endswith('"'):
                inc = inc[1:-1]
            inc_path = inc if os.path.isabs(inc) else os.path.join(os.path.dirname(path), inc)
            out.extend(_read_with_includes(inc_path, stack + [ap]))
        else:
            kept.append(raw)
    if kept:
        out.append((path, "\n".join(kept)))
    return out


def _write(path: str, text: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="MiniCPU-16 assembler")
    p.add_argument("sources", nargs="+", help="assembly source files")
    p.add_argument("--vhdl", metavar="FILE", help="write a VHDL program-image package")
    p.add_argument("--hex", metavar="PREFIX", help="write PREFIX.hex and PREFIX.data.hex")
    p.add_argument("--mem", metavar="PREFIX", help="write PREFIX.mem")
    p.add_argument("--listing", metavar="FILE", help="write an address/encoding/source listing")
    p.add_argument("--instr-words", type=int, default=2048)
    p.add_argument("--ram-bytes", type=int, default=4096)
    ns = p.parse_args(argv)
    try:
        sources: List[Tuple[str, str]] = []
        for path in ns.sources:
            sources.extend(_read_with_includes(path))
        img = assemble_sources(sources, instr_words=ns.instr_words, ram_bytes=ns.ram_bytes)
        if ns.vhdl:
            _write(ns.vhdl, img.vhdl_package())
        if ns.hex:
            _write(ns.hex + ".hex", img.hex_text())
            _write(ns.hex + ".data.hex", img.data_hex_text())
        if ns.mem:
            _write(ns.mem + ".mem", img.mem_text())
        if ns.listing:
            _write(ns.listing, "\n".join(img.listing) + "\n")
        if not (ns.vhdl or ns.hex or ns.mem or ns.listing):
            sys.stdout.write("\n".join(img.listing) + "\n")
        sys.stderr.write("assembled %d instruction(s), %d data byte(s), bss %d..%d\n" %
                         (img.program_words, img.data_size, img.bss_start, img.bss_end))
        return 0
    except AsmError as e:
        sys.stderr.write(str(e) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
