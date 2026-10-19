#!/usr/bin/env python3
"""Assembler tests.  Expected encodings are written by hand from docs/ISA.md,
independently of the assembler implementation."""

import random
import sys
import unittest

import assembler
from assembler import AsmError, assemble_text, OPCODE_TABLE
import disassembler

CHECKS = 0


def A(text, instr_words=2048, ram_bytes=4096):
    return assemble_text(text, instr_words=instr_words, ram_bytes=ram_bytes)


def one(text, **kw):
    return A(text, **kw).instr_words[0]


class AssemblerTests(unittest.TestCase):
    def eq(self, got, want, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertEqual(got, want, msg)

    def expect_error(self, text, pattern, **kw):
        global CHECKS
        CHECKS += 1
        with self.assertRaisesRegex(AsmError, pattern):
            A(text, **kw)

    # ---------------------------------------------------------------- R-type
    def test_r_type(self):
        cases = [
            ("add r3,r1,r2", 0x01312000),
            ("sub r3,r4,r5", 0x02345000),
            ("and r6,r7,r8", 0x03678000),
            ("or r9,r10,r11", 0x049AB000),
            ("xor r12,r13,r14", 0x05CDE000),
            ("sll r1,r2,r3", 0x06123000),
            ("srl r4,r5,r6", 0x07456000),
            ("sra r7,r8,r9", 0x08789000),
            ("slt r10,r11,r12", 0x09ABC000),
            ("sltu r13,r14,r15", 0x0ADEF000),
        ]
        for src, want in cases:
            self.eq(one(src), want, src)

    # ---------------------------------------------------------------- I-type
    def test_i_type(self):
        cases = [
            ("addi r5,r1,-1", 0x0B51FFFF),
            ("andi r2,r3,0x00FF", 0x0C2300FF),
            ("ori r4,r5,0x8000", 0x0D458000),
            ("xori r6,r7,0xFFFF", 0x0E67FFFF),
            ("slli r1,r1,3", 0x0F110003),
            ("srli r2,r3,4", 0x10230004),
            ("srai r4,r5,15", 0x1145000F),
            ("slti r6,r7,-1", 0x1267FFFF),
            ("sltiu r8,r9,1", 0x13890001),
        ]
        for src, want in cases:
            self.eq(one(src), want, src)

    def test_load_store(self):
        cases = [
            ("lb r1,0(r2)", 0x14120000),
            ("lbu r3,1(r4)", 0x15340001),
            ("lw r5,0xFFFE(r6)", 0x1656FFFE),
            ("sb r7,-2(r8)", 0x1778FFFE),
            ("sw r9,4(r10)", 0x189A0004),
            ("lw r1, 16(r0)", 0x16100010),
            ("sw r2, 0(r0)", 0x18200000),
        ]
        for src, want in cases:
            self.eq(one(src), want, src)

    def test_branches(self):
        cases = [
            ("beq r1,r2,4", 0x19210003),
            ("bne r3,r4,0", 0x1A43FFFF),
            ("blt r5,r6,2", 0x1B650001),
            ("bge r7,r8,0", 0x1C87FFFF),
            ("bltu r9,r10,1", 0x1DA90000),
            ("bgeu r11,r12,0x8000", 0x1ECB7FFF),
        ]
        for src, want in cases:
            self.eq(one(src), want, src)

    def test_jumps(self):
        self.eq(one("jal r15,0x10"), 0x1FF0000F)
        self.eq(one("jalr r0,r15,0"), 0x200F0000)
        self.eq(one("halt"), 0x00000000)
        self.eq(one("nop"), 0x01000000)

    # ---------------------------------------------------------------- pseudos
    def test_pseudos(self):
        cases = [
            ("li r1,0x1234", 0x0B101234),
            ("mv r3,r4", 0x01340000),
            ("not r1,r2", 0x0E12FFFF),
            ("neg r3,r4", 0x02304000),
            ("j 4", 0x1F000003),
            ("call 4", 0x1FF00003),
            ("ret", 0x200F0000),
            ("bgt r1,r2,4", 0x1B120003),
            ("ble r1,r2,4", 0x1C120003),
            ("bgtu r1,r2,4", 0x1D120003),
            ("bleu r1,r2,4", 0x1E120003),
            ("beqz r5,4", 0x19050003),
            ("bnez r5,4", 0x1A050003),
            ("bltz r5,4", 0x1B050003),
            ("bgez r5,4", 0x1C050003),
            ("bgtz r5,4", 0x1B500003),
            ("blez r5,4", 0x1C500003),
            ("subi r1,r2,1", 0x0B12FFFF),
            ("subi r1,r2,-32767", 0x0B127FFF),
            ("subi r1,r2,32768", 0x0B128000),
            ("seqz r1,r2", 0x13120001),
            ("snez r1,r2", 0x0A102000),
            ("sltz r1,r2", 0x09120000),
            ("sgtz r1,r2", 0x09102000),
        ]
        for src, want in cases:
            self.eq(one(src), want, src)

    def test_la(self):
        img = A(".data\nvar: .word 1\n.text\nla r1, var\nlw r2, var\n")
        self.eq(img.instr_words[0], 0x0B100000)
        self.eq(img.instr_words[1], 0x16200000)

    def test_labels_and_branch_offsets(self):
        text = """
.text
start:
    addi r1, r0, 0
    beq r1, r2, next
    addi r1, r1, 1
next:
    bne r1, r2, start
    halt
"""
        img = A(text)
        self.eq(img.instr_words[1], 0x19210001)
        self.eq(img.instr_words[3], 0x1A21FFFC)
        self.eq(img.symbols["start"].value, 0)
        self.eq(img.symbols["start"].kind, "code")
        self.eq(img.symbols["next"].value, 3)

    # ---------------------------------------------------------------- data
    def test_data_layout(self):
        text = """
.data
a:  .byte 1, -1, 0x7F
    .align 2
w:  .word 0x1234
s:  .asciz "Hi"
.text
    lw r1, w
    lbu r2, s
"""
        img = A(text)
        self.eq(img.data_size, 9)
        self.eq(list(img.data), [0x01, 0xFF, 0x7F, 0x00, 0x34, 0x12, 0x48, 0x69, 0x00])
        self.eq(img.symbols["a"].value, 0)
        self.eq(img.symbols["w"].value, 4)
        self.eq(img.symbols["s"].value, 6)
        self.eq(img.instr_words[0], 0x16100004)
        self.eq(img.instr_words[1], 0x15200006)

    def test_bss_and_symbols(self):
        img = A(".data\nx: .word 7\n.bss\nbuf: .space 8\n.text\nhalt\n")
        self.eq(img.data_size, 2)
        self.eq(img.bss_start, 2)
        self.eq(img.bss_end, 10)
        self.eq(img.symbols["x"].value, 0)
        self.eq(img.symbols["buf"].value, 2)
        self.eq(img.symbols["__bss_start"].value, 2)
        self.eq(img.symbols["__bss_end"].value, 10)

    def test_equ_and_org(self):
        img = A(".equ K, 42\n.org 3\nli r1, K\nhalt\n")
        self.eq(img.program_words, 5)
        self.eq(img.instr_words[3], 0x0B10002A)
        self.eq(img.instr_words[4], 0)
        self.eq(img.instr_words[0], 0)

    def test_inst(self):
        img = A(".inst 0xDEADBEEF\n.inst 0x00000001\n")
        self.eq(img.instr_words[0], 0xDEADBEEF)
        self.eq(img.instr_words[1], 1)

    # ---------------------------------------------------------------- errors
    def test_errors(self):
        cases = [
            ("add r1,r2,r99", "unknown register"),
            ("beq r1,r2,missing", "undefined symbol"),
            ("x: nop\nx: nop", "duplicate symbol"),
            ("li r1,70000", "out of 16-bit range"),
            ("addi r1,r2,70000", "out of 16-bit range"),
            (".data\n.byte 256", "byte value out of range"),
            (".data\n.word 70000", "word value out of 16-bit range"),
            (".data\n.space 5000", "exceeds RAM_BYTES"),
            ("nop\nnop\nnop\nnop", "program exceeds INSTR_WORDS"),
            ("subi r1,r2,-32768", "cannot negate"),
            ("slli r1,r2,16", "shift amount"),
            ("slli r1,r2,-1", "shift amount"),
            (".data\nx: .word 0\n.text\nbeq r1,r2,x", "data label cannot be used as a branch target"),
            ("main: nop\nlw r1, main", "code label cannot be used as a data address"),
            (".bss\n.space 2\n.data\n.word 1", "cannot return to .data"),
            ("frobnicate r1", "unknown instruction"),
            (".frobnicate", "unknown directive"),
            ("add r1,r2", "expects rd, rs1, rs2"),
            ("add r1,,r2", "empty operand"),
            ("lw r1, 0(r2", "unbalanced"),
            ("sb r1, 0x10000(r2)", "offset out of 16-bit range"),
            (".align 16", "align exponent"),
            ("RAM_BYTES: nop", "duplicate symbol"),
            (".org 2\n.org 1", "cannot move backwards"),
            (".data\n.org 4097", "beyond RAM_BYTES"),
            ("beq r1,r2,0x10000", "branch target out of 16-bit range"),
        ]
        for src, pat in cases:
            kw = {"instr_words": 3} if "program exceeds" in pat else {}
            if "branch offset" in pat:
                kw = {"instr_words": 65536}
            self.expect_error(src, pat, **kw)

    # ---------------------------------------------------------------- roundtrip
    def _rt(self, word, pc=0):
        src = disassembler.decode_word(word, pc)
        prefix = ".org %d\n" % pc if pc else ""
        img = A(prefix + src, instr_words=65536)
        return img.instr_words[pc]

    def test_roundtrip_random_valid(self):
        rng = random.Random(0xC0FFEE)
        count = 0
        for name, (op, fmt) in sorted(OPCODE_TABLE.items()):
            if name == "HALT":
                continue
            for _ in range(20):
                rd, rs1, rs2 = (rng.randrange(16) for _ in range(3))
                if fmt == "R":
                    word = (op << 24) | (rd << 20) | (rs1 << 16) | (rs2 << 12)
                elif fmt == "I":
                    imm = rng.randrange(0, 65536)
                    if name in ("SLLI", "SRLI", "SRAI"):
                        imm &= 0xF
                    word = (op << 24) | (rd << 20) | (rs1 << 16) | imm
                elif fmt == "S":
                    imm = rng.randrange(0, 65536)
                    word = (op << 24) | (rd << 20) | (rs1 << 16) | imm
                elif fmt == "B":
                    off = rng.randrange(-32767, 32768)
                    word = (op << 24) | (rd << 20) | (rs1 << 16) | (off & 0xFFFF)
                elif fmt == "UJ":
                    off = rng.randrange(-32767, 32768)
                    word = (op << 24) | (rd << 20) | (off & 0xFFFF)
                else:
                    continue
                self.eq(self._rt(word), word, "%s %08X" % (name, word))
                count += 1
        self.eq(count > 500, True)

    def test_roundtrip_unknown(self):
        for w in (0xDEADBEEF, 0x21000000, 0xFF000000):
            self.eq(self._rt(w), w)

    def test_boundary_immediates(self):
        for op, name in ((0x0B, "ADDI"), (0x0C, "ANDI"), (0x0D, "ORI"),
                         (0x0E, "XORI"), (0x12, "SLTI"), (0x13, "SLTIU"),
                         (0x14, "LB"), (0x15, "LBU"), (0x16, "LW")):
            for imm in (0, 1, 0x7FFF, 0x8000, 0xFFFF):
                w = (op << 24) | (1 << 20) | (2 << 16) | imm
                self.eq(self._rt(w), w, "%s imm %04X" % (name, imm))
        # branch offset extremes at the PC that makes the target reachable
        for op, name in ((0x19, "BEQ"), (0x1A, "BNE"), (0x1B, "BLT"),
                         (0x1C, "BGE"), (0x1D, "BLTU"), (0x1E, "BGEU")):
            w_hi = (op << 24) | (1 << 20) | (2 << 16) | 0x7FFF
            self.eq(self._rt(w_hi, 0), w_hi, "%s +32767" % name)
            w_lo = (op << 24) | (1 << 20) | (2 << 16) | 0x8000
            self.eq(self._rt(w_lo, 0x7FFF), w_lo, "%s -32768" % name)
        for off in (0x7FFF, 0x8000):
            w = (0x1F << 24) | (15 << 20) | (off & 0xFFFF)
            pc = 0 if off == 0x7FFF else 0x7FFF
            self.eq(self._rt(w, pc), w)

    # ---------------------------------------------------------------- outputs
    def test_vhdl_output(self):
        img = A(".data\nb: .byte 0x12, 0x34\n.text\naddi r1,r0,1\nhalt\n")
        v = img.vhdl_package()
        self.eq("package minicpu16_program_pkg is" in v, True)
        self.eq("constant PROGRAM_WORDS : integer := 2;" in v, True)
        self.eq('0 => x"0B100001"' in v, True)
        self.eq('0 => x"12"' in v, True)
        self.eq('0 => x"34"' in v, True)
        self.eq(", ," in v, False)
        self.eq(v, img.vhdl_package())

    def test_determinism(self):
        src = """
.data
x: .word 1,2,3
.text
start: li r1, 3
loop: addi r1, r1, -1
      bnez r1, loop
      la r2, x
      halt
"""
        a = A(src)
        b = A(src)
        self.eq(a.instr_words, b.instr_words)
        self.eq(a.data, b.data)
        self.eq(a.listing, b.listing)

    def test_listing(self):
        img = A("start: addi r1,r0,1\n halt\n")
        self.eq(any("0B100001" in line for line in img.listing), True)
        self.eq(any("start:" in line for line in img.listing), True)

    def test_cli_include(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            inc = os.path.join(d, "inc.s")
            main = os.path.join(d, "main.s")
            with open(inc, "w") as f:
                f.write(".equ K, 5\n")
            with open(main, "w") as f:
                f.write('.include "inc.s"\nli r1, K\nhalt\n')
            srcs = assembler._read_with_includes(main)
            img = assembler.assemble_sources(srcs)
            self.eq(img.instr_words[0], 0x0B100005)
            self.eq(img.instr_words[1], 0)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1)
    print("assembler checks: %d" % CHECKS)
    sys.exit(0 if result.result.wasSuccessful() else 1)
