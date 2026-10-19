#!/usr/bin/env python3
"""Golden-model tests: ISA semantics, timing contract, MMIO, properties."""

import random
import sys
import unittest

import assembler
from assembler import assemble_text
from isa_sim import (Config, WatchdogError, alu_op, branch_taken, decode,
                     run_arch, run_pipeline, s16)

CHECKS = 0


class SimTests(unittest.TestCase):
    def eq(self, got, want, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertEqual(got, want, msg)

    def check(self, cond, msg=""):
        global CHECKS
        CHECKS += 1
        self.assertTrue(cond, msg)

    def run_p(self, src, regs=None, **cfg):
        img = assemble_text(src)
        return run_pipeline(img.instr_words, Config(ram_init=img.data, **cfg), init_regs=regs)

    def run_a(self, src, regs=None, **cfg):
        img = assemble_text(src)
        return run_arch(img.instr_words, Config(ram_init=img.data, **cfg), init_regs=regs)

    # ------------------------------------------------------------------ ALU
    def test_alu_register_program(self):
        r = self.run_p("""
li r1, 0x1234
li r2, 0x00FF
add r3, r1, r2
sub r4, r1, r2
and r5, r1, r2
or  r6, r1, r2
xor r7, r1, r2
slli r8, r1, 4
srli r9, r1, 1
srai r10, r1, 1
slt r11, r2, r1
sltu r12, r1, r2
addi r13, r1, 0xFF
andi r14, r1, 0x00FF
ori r15, r1, 0x8000
halt
""")
        self.eq(r.regs[3], 0x1333)
        self.eq(r.regs[4], 0x1135)
        self.eq(r.regs[5], 0x0034)
        self.eq(r.regs[6], 0x12FF)
        self.eq(r.regs[7], 0x12CB)
        self.eq(r.regs[8], 0x2340)
        self.eq(r.regs[9], 0x091A)
        self.eq(r.regs[10], 0x091A)
        self.eq(r.regs[11], 1)
        self.eq(r.regs[12], 0)
        self.eq(r.regs[13], 0x1333)
        self.eq(r.regs[14], 0x0034)
        self.eq(r.regs[15], 0x9234)

    def test_sra_and_shift_amount(self):
        r = self.run_p("""
li r1, 0x8001
srai r2, r1, 1
srli r3, r1, 1
li r4, 17
sll r5, r1, r4
srl r6, r1, r4
sra r7, r1, r4
halt
""")
        self.eq(r.regs[2], 0xC000)
        self.eq(r.regs[3], 0x4000)
        self.eq(r.regs[5], (0x8001 << 1) & 0xFFFF)
        self.eq(r.regs[6], 0x8001 >> 1)
        self.eq(r.regs[7], (s16(0x8001) >> 1) & 0xFFFF)

    def test_alu_immediates(self):
        r = self.run_p("""
li r1, 0xFFFF
xori r2, r1, 0xFFFF
slti r3, r1, 0
sltiu r4, r1, 0
slti r5, r0, -1
sltiu r6, r0, 0xFFFF
li r7, 0x8000
slti r8, r7, 0
sltiu r9, r7, 0xFFFF
halt
""")
        self.eq(r.regs[2], 0)
        self.eq(r.regs[3], 1)
        self.eq(r.regs[4], 0)
        self.eq(r.regs[5], 0)
        self.eq(r.regs[6], 1)
        self.eq(r.regs[8], 1)
        self.eq(r.regs[9], 1)

    def test_compare_table(self):
        rows = [
            (0x8000, 0x7FFF, 1, 0, 1, 0, 0, 1),
            (0x7FFF, 0x8000, 0, 1, 0, 1, 1, 0),
            (0xFFFF, 0x0000, 1, 0, 1, 0, 0, 1),
            (0x0000, 0xFFFF, 0, 1, 0, 1, 1, 0),
            (0x1234, 0x1234, 0, 0, 0, 0, 1, 1),
            (0x8000, 0x8000, 0, 0, 0, 0, 1, 1),
            (0x00FF, 0x0100, 1, 1, 1, 1, 0, 0),
            (0xFF00, 0x00FF, 1, 0, 1, 0, 0, 1),
        ]
        for a, b, slt, sltu, blt, bltu, bge, bgeu in rows:
            self.eq(alu_op("SLT", a, b, 0), slt, "SLT %04X %04X" % (a, b))
            self.eq(alu_op("SLTU", a, b, 0), sltu, "SLTU %04X %04X" % (a, b))
            self.eq(1 if branch_taken("BLT", a, b) else 0, blt, "BLT")
            self.eq(1 if branch_taken("BLTU", a, b) else 0, bltu, "BLTU")
            self.eq(1 if branch_taken("BGE", a, b) else 0, bge, "BGE")
            self.eq(1 if branch_taken("BGEU", a, b) else 0, bgeu, "BGEU")

    # --------------------------------------------------------------- memory
    def test_memory_registers_explicit(self):
        r = self.run_p("""
.data
var: .word 0xABCD
.text
lw s0, var          # r9
li t0, 0            # r5
li t1, 0x1234       # r6
sw t1, 0(t0)
lw t2, 0(t0)        # r7
lb t3, 0(t0)        # r8
lbu s1, 1(t0)       # r10
lb s2, 1(t0)        # r11
lw s3, 1(t0)        # r12
sw t1, 3(t0)
lw s4, 2(t0)        # r13
li t1, 0x80
sb t1, 4(t0)
lb t2, 4(t0)        # r7
lbu t3, 4(t0)       # r8
halt
""")
        self.eq(r.regs[9], 0xABCD)
        self.eq(r.regs[7], 0xFF80)
        self.eq(r.regs[10], 0x12)
        self.eq(r.regs[11], 0x12)
        self.eq(r.regs[12], 0x1234)
        self.eq(r.regs[13], 0x1234)
        self.eq(r.regs[8], 0x0080)
        self.eq(r.ram[0], 0x34)
        self.eq(r.ram[1], 0x12)
        self.eq(r.ram[2], 0x34)
        self.eq(r.ram[3], 0x12)

    def test_address_wrap(self):
        r = self.run_p("""
.data
var: .word 0xABCD
.text
li t0, 0xFFFF
lw t1, 1(t0)
halt
""")
        self.eq(r.regs[6], 0xABCD)

    def test_unmapped_region(self):
        r = self.run_p("""
li t0, 0x2000
lw t1, 0(t0)
li t2, 0x1234
sw t2, 0(t0)
lw t3, 0(t0)
lbu s0, 1(t0)
halt
""")
        self.eq(r.regs[6], 0)
        self.eq(r.regs[8], 0)
        self.eq(r.regs[9], 0)

    # ----------------------------------------------------------------- MMIO
    def test_gpio_mmio(self):
        r = self.run_p("""
li t0, GPIO_OUT
li t1, 0x1234
sw t1, 0(t0)
li t2, 0xAA
sb t2, 0(t0)
li t2, 0xBB
sb t2, 1(t0)
lbu t3, 0(t0)
lb s0, 1(t0)
lw s1, 0(t0)
halt
""", gpio_in=0xBEEF)
        self.eq(r.gpio_out, 0x12BB)
        self.eq(r.regs[8], 0xBB)
        self.eq(r.regs[9], 0xFFBB)
        self.eq(r.regs[10], 0x12BB)

    def test_gpio_in(self):
        r = self.run_p("""
li t0, GPIO_IN
lw t1, 0(t0)
lbu t2, 0(t0)
halt
""", gpio_in=0xBEEF)
        self.eq(r.regs[6], 0xBEEF)
        self.eq(r.regs[7], 0xEF)

    def test_timer(self):
        r = self.run_p("""
li t0, TIMER
lw t1, 0(t0)
nop
nop
lw t2, 0(t0)
halt
""")
        self.check(r.regs[7] > r.regs[6], "timer must advance")
        self.eq(r.regs[6] & 0xFFFF, (r.trace[1].retire_cycle - 1) & 0xFFFF)

    def test_uart_drop_when_busy(self):
        r = self.run_p("""
li t0, UART_TX
li t1, 0x41
sw t1, 0(t0)
li t1, 0x42
sw t1, 0(t0)
halt
""", uart_cycles_per_byte=10)
        self.eq(r.uart, [0x41])

    def test_uart_polling(self):
        r = self.run_p("""
li t0, UART_TX
li t1, 0x41
sw t1, 0(t0)
li t2, UART_STATUS
wait:
lw t3, 0(t2)
andi t3, t3, 1
bnez t3, wait
li t1, 0x42
sw t1, 0(t0)
halt
""", uart_cycles_per_byte=4)
        self.eq(r.uart, [0x41, 0x42])
        self.eq(r.regs[8] & 1, 0)

    def test_uart_status_busy(self):
        r = self.run_p("""
li t0, UART_TX
li t1, 0x41
sw t1, 0(t0)
li t2, UART_STATUS
lw t3, 0(t2)
halt
""", uart_cycles_per_byte=10)
        self.eq(r.regs[8], 1)

    def test_mmio_reserved(self):
        r = self.run_p("""
li t0, 0xFF0A
lw t1, 0(t0)
li t2, 0x1234
sw t2, 0(t0)
lw t3, 0(t0)
li t0, UART_TX
lw s0, 0(t0)
halt
""")
        self.eq(r.regs[6], 0)
        self.eq(r.regs[8], 0)
        self.eq(r.regs[9], 0)

    # ------------------------------------------------------------------ R0
    def test_r0_invariant(self):
        r = self.run_p("""
addi r0, r0, 5
add r1, r0, r0
li t0, 0
lw r0, 0(t0)
addi r2, r0, 7
halt
""")
        self.eq(r.regs[0], 0)
        self.eq(r.regs[1], 0)
        self.eq(r.regs[2], 7)

    # --------------------------------------------------- control / HALT
    def test_halt_and_fetch_beyond(self):
        r = self.run_p("nop\n")
        self.eq(r.cycles, 6)
        self.eq(r.instructions, 2)
        self.eq(r.pc, 1)
        self.eq(r.trace[1].instr, 0)

    def test_explicit_halt_only(self):
        r = self.run_p("halt\n")
        self.eq(r.cycles, 5)
        self.eq(r.instructions, 1)
        self.eq(r.pc, 0)

    def test_empty_program(self):
        r = run_pipeline([], Config())
        self.eq(r.cycles, 5)
        self.eq(r.instructions, 1)
        self.eq(r.pc, 0)

    def test_unassigned_opcode_is_nop(self):
        r = self.run_p(".inst 0xDEADBEEF\nli r1, 7\nhalt\n")
        self.eq(r.regs[1], 7)
        self.eq(r.instructions, 3)
        self.eq(r.trace[0].rd, None)

    def test_reserved_opcode_is_nop(self):
        r = self.run_p(".inst 0x21000000\nli r1, 9\nhalt\n")
        self.eq(r.regs[1], 9)
        self.eq(r.instructions, 3)

    def test_jalr_rd_equals_rs1(self):
        r = self.run_p("li t0, 3\njalr t0, t0, 0\naddi r1, r1, 1\nhalt\n")
        self.eq(r.regs[5], 2)
        self.eq(r.regs[1], 0)
        self.eq(r.pc, 3)

    def test_jalr_target_ffff(self):
        r = self.run_p("li t0, 0xFFFF\njalr r0, t0, 0\nhalt\n")
        self.eq(r.pc, 0xFFFF)
        self.eq(r.instructions, 3)

    def test_branch_target_wrap(self):
        # branch at pc 0 to 0xFFFF via a 16-bit target expression
        r = self.run_p("beq r0, r0, 0xFFFF\nhalt\n")
        self.eq(r.pc, 0xFFFF)
        self.eq(r.trace[0].taken, 1)

    def test_store_load_same_address(self):
        r = self.run_p("""
li t0, 0
li t1, 0x1234
sw t1, 0(t0)
lw t2, 0(t0)
halt
""")
        self.eq(r.regs[7], 0x1234)

    # --------------------------------------------------------------- timing
    def test_timing_examples(self):
        A = "addi r1,r0,1\naddi r2,r0,2\nadd r3,r1,r2\nhalt\n"
        B = "lw r1,0(r2)\nadd r3,r1,r1\nadd r4,r4,r4\nhalt\n"
        C = "beq r1,r2,3\naddi r3,r3,1\naddi r4,r4,1\nhalt\n"
        D = "beq r1,r2,3\nhalt\n"
        E = "jal r15,2\naddi r3,r3,1\nhalt\n"
        F = "jalr r0,t0,0\naddi r3,r3,1\nhalt\n"
        G = "addi r1,r0,3\naddi r2,r2,1\nbne r1,r2,1\nhalt\n"
        self.eq(self.run_p(A).cycles, 8)
        self.eq(self.run_p(B).cycles, 9)
        self.eq(self.run_p(B).stalls, 1)
        self.eq(self.run_p(C).cycles, 8)
        self.eq(self.run_p(D, regs=[0, 0, 1]).cycles, 6)
        self.eq(self.run_p(E).cycles, 8)
        regs = [0] * 16
        regs[5] = 3
        self.eq(self.run_p(F, regs=regs).cycles, 8)
        self.eq(self.run_p(G).cycles, 16)
        self.eq([t.retire_cycle for t in self.run_p(B).trace], [5, 7, 8, 9])
        self.eq([t.retire_cycle for t in self.run_p(C).trace], [5, 8])
        self.eq([t.retire_cycle for t in self.run_p(G).trace], [5, 6, 7, 10, 11, 14, 15, 16])

    def test_load_use_store_data_stalls(self):
        r = self.run_p("li t0,0\nlw t1,0(t0)\nsw t1,4(t0)\nhalt\n")
        self.eq(r.stalls, 1)
        r = self.run_p("li t0,0\nlw t1,0(t0)\nsw t2,0(t1)\nhalt\n")
        self.eq(r.stalls, 1)
        r = self.run_p("li t0,0\nlw t1,0(t0)\nbeq t1,t0,4\nnop\nhalt\n")
        self.eq(r.stalls, 1)

    def test_no_stall_at_distance_two_or_three(self):
        r = self.run_p("li t0,0\nlw t1,0(t0)\nnop\nadd t2,t1,t1\nhalt\n")
        self.eq(r.stalls, 0)
        r = self.run_p("li t0,0\nlw t1,0(t0)\nnop\nnop\nadd t2,t1,t1\nhalt\n")
        self.eq(r.stalls, 0)
        self.eq(r.regs[7], 0)

    def test_watchdog(self):
        global CHECKS
        CHECKS += 1
        with self.assertRaises(WatchdogError):
            self.run_p("j 0\n", max_cycles=50)
        CHECKS += 1
        with self.assertRaises(WatchdogError):
            self.run_p("j 0\n", max_instructions=20)

    # --------------------------------------------------------------- traces
    def test_trace_memory_fields(self):
        r = self.run_p("""
.data
var: .word 0x1234
.text
lw t1, var
sw t1, 0(r0)
halt
""")
        lw = r.trace[0]
        self.eq(lw.mem_kind, "L")
        self.eq(lw.mem_addr, 0)
        self.eq(lw.mem_size, 2)
        self.eq(lw.mem_data, 0x1234)
        self.eq(lw.rd, 6)
        sw = r.trace[1]
        self.eq(sw.mem_kind, "S")
        self.eq(sw.mem_data, 0x1234)
        self.eq(sw.rd, None)

    def test_trace_header_and_columns(self):
        r = self.run_p("li r1,1\nhalt\n")
        text = r.trace_text()
        self.check(text.startswith("# minicpu16-retirement-trace v1\n"))
        lines = [l for l in text.splitlines() if l and not l.startswith("#")]
        self.eq(len(lines), 2)
        self.eq(len(lines[0].split()), 12)

    # ------------------------------------------------------------ properties
    def test_property_alu_compare(self):
        rng = random.Random(0x5EED)
        for _ in range(2000):
            a = rng.randrange(0x10000)
            b = rng.randrange(0x10000)
            sa = a - 0x10000 if a & 0x8000 else a
            sb = b - 0x10000 if b & 0x8000 else b
            self.eq(alu_op("SLT", a, b, 0), 1 if sa < sb else 0)
            self.eq(alu_op("SLTU", a, b, 0), 1 if a < b else 0)
            self.eq(branch_taken("BLT", a, b), sa < sb)
            self.eq(branch_taken("BGE", a, b), sa >= sb)
            self.eq(branch_taken("BLTU", a, b), a < b)
            self.eq(branch_taken("BGEU", a, b), a >= b)
            self.eq(alu_op("ADD", a, b, 0), (a + b) & 0xFFFF)
            self.eq(alu_op("SUB", a, b, 0), (a - b) & 0xFFFF)
            sh = b & 0xF
            self.eq(alu_op("SLL", a, b, 0), (a << sh) & 0xFFFF)
            self.eq(alu_op("SRL", a, b, 0), a >> sh)
            self.eq(alu_op("SRA", a, b, 0), (sa >> sh) & 0xFFFF)

    def test_arch_and_pipeline_agree(self):
        programs = [
            "addi r1,r0,3\naddi r2,r2,1\nbne r1,r2,1\nhalt\n",
            "beq r1,r2,3\naddi r3,r3,1\naddi r4,r4,1\nhalt\n",
            "jal r15,2\naddi r3,r3,1\nhalt\n",
            ".data\nvar: .word 0xABCD\n.text\nli t0,0\nlw t1,var\nsw t1,4(t0)\nhalt\n",
            "li t0, GPIO_OUT\nli t1, 0x1234\nsw t1,0(t0)\ni\nhalt\n".replace("i\n", ""),
        ]
        for src in programs:
            a = self.run_a(src)
            p = self.run_p(src)
            self.eq(a.regs, p.regs, src)
            self.eq(a.ram, p.ram, src)
            self.eq(a.pc, p.pc, src)
            self.eq(a.instructions, p.instructions, src)
            self.eq(a.uart, p.uart, src)
            self.eq(a.gpio_out, p.gpio_out, src)

    def test_json(self):
        import json
        r = self.run_p("li r1,1\nhalt\n")
        d = json.loads(r.to_json())
        self.eq(d["regs"][1], "0x0001")
        self.eq(d["pc"], "0x0001")
        self.eq(d["halted"], True)
        self.eq(d["instructions"], 2)

    def test_instruction_mix(self):
        r = self.run_p("li r1,1\nlw r2,0(r0)\nsw r2,4(r0)\nbeq r1,r1,5\nhalt\n")
        mix = r.instruction_mix()
        self.eq(mix.get("alu", 0) >= 1, True)
        self.eq(mix.get("load", 0), 1)
        self.eq(mix.get("store", 0), 1)
        self.eq(mix.get("branch", 0), 1)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1)
    print("isa_sim checks: %d" % CHECKS)
    sys.exit(0 if result.result.wasSuccessful() else 1)
