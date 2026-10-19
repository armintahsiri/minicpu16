#!/usr/bin/env python3
"""MiniCPU-16 disassembler (Phase 4).

Decodes one 32-bit instruction word back to canonical assembly.  Branch and jump
targets are printed as absolute instruction indices, which is exactly what
tools/assembler.py expects, so "encode -> decode -> encode" is stable for every
canonical instruction.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

try:
    from assembler import OPCODE_TABLE
except ImportError:  # when imported as tools.disassembler
    from tools.assembler import OPCODE_TABLE  # type: ignore

REVERSE = {v[0]: (k, v[1]) for k, v in OPCODE_TABLE.items()}

# Formats by opcode group, independent of the assembler table's iteration order.
R_OPS = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A}
I_OPS = {0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12, 0x13,
         0x14, 0x15, 0x16, 0x20}
S_OPS = {0x17, 0x18}
B_OPS = {0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E}
J_OPS = {0x1F}


def sext16(x: int) -> int:
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def _r(n: int) -> str:
    return "r%d" % n


def decode_word(word: int, pc: int = 0) -> str:
    word &= 0xFFFFFFFF
    op = (word >> 24) & 0xFF
    rd = (word >> 20) & 0xF
    rs1 = (word >> 16) & 0xF
    rs2 = (word >> 12) & 0xF
    imm = word & 0xFFFF

    if op == 0x00:
        return "halt"
    if op not in REVERSE:
        return ".inst 0x%08X" % word
    mnemonic, fmt = REVERSE[op]
    if fmt == "R":
        if op == 0x01 and rd == 0 and rs1 == 0 and rs2 == 0 and (word & 0xFFF) == 0:
            return "nop"
        return "%s %s, %s, %s" % (mnemonic.lower(), _r(rd), _r(rs1), _r(rs2))
    if fmt == "I":
        if op in (0x14, 0x15, 0x16):  # LB, LBU, LW
            return "%s %s, 0x%04X(%s)" % (mnemonic.lower(), _r(rd), imm, _r(rs1))
        if op == 0x20:  # JALR
            return "jalr %s, %s, 0x%04X" % (_r(rd), _r(rs1), imm)
        return "%s %s, %s, 0x%04X" % (mnemonic.lower(), _r(rd), _r(rs1), imm)
    if fmt == "S":
        return "%s %s, 0x%04X(%s)" % (mnemonic.lower(), _r(rd), imm, _r(rs1))
    if fmt == "B":
        target = (pc + 1 + sext16(imm)) & 0xFFFF
        return "%s %s, %s, 0x%04X" % (mnemonic.lower(), _r(rs1), _r(rd), target)
    if fmt == "UJ":
        target = (pc + 1 + sext16(imm)) & 0xFFFF
        return "jal %s, 0x%04X" % (_r(rd), target)
    return ".inst 0x%08X" % word


def decode_image(words: Sequence[int], start_pc: int = 0) -> List[str]:
    return [decode_word(w, start_pc + i) for i, w in enumerate(words)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="MiniCPU-16 disassembler")
    p.add_argument("words", nargs="*", help="32-bit hex instruction words")
    p.add_argument("--hex", metavar="FILE", help="read one 8-hex-digit word per line")
    p.add_argument("--pc", type=lambda s: int(s, 0), default=0, help="instruction index of the first word")
    ns = p.parse_args(argv)
    words: List[int] = []
    for w in ns.words:
        words.append(int(w, 0) & 0xFFFFFFFF)
    if ns.hex:
        with open(ns.hex, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                words.append(int(s, 16) & 0xFFFFFFFF)
    if not words:
        p.error("no instruction words given")
    for i, w in enumerate(words):
        sys.stdout.write("%04X: %08X  %s\n" % ((ns.pc + i) & 0xFFFF, w, decode_word(w, ns.pc + i)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
