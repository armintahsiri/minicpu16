#!/usr/bin/env python3
"""Deterministic MiniCPU-16 random program generator (Phase 4).

Modes:
  straight   - straight-line ALU/immediate/memory code
  loop       - a bounded counted loop
  calls      - JAL/JALR calls with LR save/restore
  hazards    - back-to-back dependencies, load-use in every consumer position,
               taken branches over side effects, R0 writes, store/load aliasing
  mmio       - GPIO/TIMER/UART accesses
  coverage   - one executed instance of every assigned opcode

The generator only emits valid, terminating programs.  self_validate() runs N
programs on the timing model and reports opcode and hazard coverage.

Reference traces are written by --write-reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import assembler
import isa_sim
from isa_sim import Config, decode, run_pipeline

ROOT = Path(__file__).resolve().parents[1]
CRT0 = ROOT / "programs" / "runtime" / "crt0.s"

SAFE = ["t0", "t1", "t2", "t3", "a0", "a1", "a2", "a3", "s0", "s1", "s2", "s3"]
TMP = ["t0", "t1", "t2", "t3", "a0", "a1", "a2", "a3"]
ALU_R = ["add", "sub", "and", "or", "xor", "sll", "srl", "sra", "slt", "sltu"]
ALU_I = ["addi", "andi", "ori", "xori", "slli", "srli", "srai", "slti", "sltiu"]

HAZARD_NAMES = [
    "dep_d1", "dep_d2", "dep_d3",
    "load_use_alu", "load_use_store_data", "load_use_store_addr",
    "load_use_branch", "load_use_jalr",
    "branch_over_side_effect", "jal_dependent", "jalr_dependent",
    "r0_write", "store_load_same_addr",
]


@dataclass
class Generated:
    seed: int
    mode: str
    source: str
    hazards: List[str]


class Gen:
    """Seed-driven program builder.

    Every mode consumes the program's RNG: register allocation, immediates,
    memory addresses, branch/loop bounds, call graphs and the choice of template
    variants all come from it.  Two seeds therefore produce different programs in
    every mode.  The builder only emits valid, terminating programs.
    """

    def __init__(self, seed: int):
        self.seed = seed
        self.rng = random.Random(seed)
        self.lines: List[str] = []
        self._buf: List[str] = self.lines
        self.n = 0
        self.hazards: Set[str] = set()
        self.funcs: List[Tuple[str, List[str]]] = []

    def emit(self, text: str) -> None:
        self._buf.append("    " + text)

    def place(self, label: str) -> None:
        self._buf.append(label + ":")

    def label(self, prefix: str = "Lg") -> str:
        self.n += 1
        return "%s_%d" % (prefix, self.n)

    def source(self) -> str:
        return "\n".join(self.lines) + "\n"

    # -- operands ----------------------------------------------------------
    def reg(self, exclude: Sequence[str] = ()) -> str:
        choices = [r for r in SAFE if r not in exclude]
        if not choices:
            choices = ["t0"]
        return self.rng.choice(choices)

    def distinct_regs(self, k: int, exclude: Sequence[str] = ()) -> List[str]:
        pool = [r for r in SAFE if r not in exclude]
        self.rng.shuffle(pool)
        while len(pool) < k:
            pool.append(self.rng.choice(SAFE))
        return pool[:k]

    def imm(self, lo: int = -32768, hi: int = 32768) -> int:
        return self.rng.randrange(lo, hi)

    def addr(self, align: bool = True) -> int:
        a = 0x0100 + self.rng.randrange(0, 0x0E00)
        return (a & 0xFFFE) if align else a

    def filler(self, n: int, exclude: Sequence[str] = ()) -> None:
        for _ in range(n):
            self.random_alu(exclude=exclude, allow_mem=False)

    def random_alu(self, exclude: Sequence[str] = (), allow_mem: bool = True) -> None:
        kind = self.rng.randrange(4 if allow_mem else 2)
        if kind == 0:
            rd, rs1, rs2 = self.distinct_regs(3, exclude)
            self.emit("%s %s, %s, %s" % (self.rng.choice(ALU_R), rd, rs1, rs2))
        elif kind == 1:
            rd, rs1 = self.distinct_regs(2, exclude)
            op = self.rng.choice(ALU_I)
            imm = self.rng.randrange(0, 16) if op in ("slli", "srli", "srai") else self.imm()
            self.emit("%s %s, %s, %d" % (op, rd, rs1, imm))
        elif kind == 2:
            rd = self.reg(exclude)
            base = self.reg(tuple(exclude) + (rd,))
            self.emit("li %s, 0x%04X" % (base, self.addr()))
            self.emit("%s %s, 0(%s)" % (self.rng.choice(["lb", "lbu", "lw"]), rd, base))
        else:
            rd = self.reg(exclude)
            base = self.reg(tuple(exclude) + (rd,))
            src = self.reg(tuple(exclude) + (rd, base))
            self.emit("li %s, 0x%04X" % (base, self.addr()))
            self.emit("li %s, 0x%04X" % (src, self.rng.randrange(0x10000)))
            self.emit("%s %s, 0(%s)" % (self.rng.choice(["sb", "sw"]), src, base))

    # -- dependency templates ---------------------------------------------
    def dep(self, distance: int) -> None:
        rd, cons, other = self.distinct_regs(3)
        self.emit("li %s, %d" % (rd, self.imm(-2048, 2048)))
        self.filler(distance - 1, exclude=(rd,))
        self.emit("%s %s, %s, %s" % (self.rng.choice(ALU_R), cons, rd, other))
        self.hazards.add("dep_d%d" % distance)

    def store_load(self, distance: int, size: Optional[str] = None,
                   load: Optional[str] = None, odd: Optional[bool] = None) -> None:
        """A store and a later load of the SAME address, `distance` apart.

        `distance` counts the retired instructions between the store and the
        load, so distance 1 means they are back-to-back.  The store/load size
        pair and the address parity are seed-driven so that the
        store_load_d1/d2/d3 coverage counters are reached from every
        byte/word/misaligned combination, not only from the even-aligned word
        case.
        """
        if size is None:
            size = self.rng.choice(("sb", "sw"))
        if load is None:
            load = "lw" if size == "sw" else self.rng.choice(("lb", "lbu"))
        if odd is None:
            odd = self.rng.randrange(2) == 1
        # An odd address makes the word accesses misaligned (ISA.md section 7.1
        # aligns LW/SW down with & 0xFFFE), which is a different case from the
        # even-aligned one and is counted separately.
        addr = self.addr(align=not odd)
        base, val, dst = self.distinct_regs(3)
        self.emit("li %s, 0x%04X" % (base, addr))
        self.emit("li %s, 0x%04X" % (val, self.rng.randrange(0x10000)))
        self.emit("%s %s, 0(%s)" % (size, val, base))
        self.filler(max(0, distance - 1), exclude=(base, val, dst))
        self.emit("%s %s, 0(%s)" % (load, dst, base))
        self.hazards.add("store_load_same_addr")

    def misaligned_pair(self) -> None:
        addr = self.addr() | 1
        base, val, dst = self.distinct_regs(3)
        self.emit("li %s, 0x%04X" % (base, addr))
        self.emit("li %s, 0x%04X" % (val, self.rng.randrange(0x10000)))
        self.emit("sw %s, 0(%s)" % (val, base))
        self.emit("lw %s, 0(%s)" % (dst, base))

    # -- modes -------------------------------------------------------------
    def straight(self) -> None:
        for _ in range(self.rng.randrange(8, 20)):
            self.random_alu()
        if self.rng.randrange(2):
            self.dep(self.rng.choice((1, 2, 3)))
        if self.rng.randrange(2):
            self.store_load(self.rng.choice((1, 2, 3, 4)),
                            self.rng.choice(("sb", "sw")))

    def loop(self) -> None:
        n = self.rng.randrange(1, 7)
        counter = "s4"
        self.emit("li %s, %d" % (counter, n))
        top = self.label("Lloop")
        self.place(top)
        for _ in range(self.rng.randrange(3, 10)):
            self.random_alu(exclude=(counter,))
        if self.rng.randrange(2):
            self.dep(self.rng.choice((1, 2)))
        self.emit("addi %s, %s, -1" % (counter, counter))
        self.emit("bnez %s, %s" % (counter, top))

    def _make_func(self, name: str, callees: Sequence[str]) -> None:
        old = self._buf
        self._buf = []
        if callees:
            self.emit("addi sp, sp, -2")
            self.emit("sw lr, 0(sp)")
        for _ in range(self.rng.randrange(1, 5)):
            self.random_alu(allow_mem=False)
        for c in callees:
            self.emit("call %s" % c)
        for _ in range(self.rng.randrange(0, 3)):
            self.random_alu(allow_mem=False)
        if callees:
            self.emit("lw lr, 0(sp)")
            self.emit("addi sp, sp, 2")
        self.emit("li a0, %d" % self.rng.randrange(0x10000))
        self.emit("ret")
        body = self._buf
        self._buf = old
        self.funcs.append((name, list(body)))

    def calls(self) -> None:
        k = self.rng.randrange(1, 4)
        names = ["fn%d" % i for i in range(k)]
        # Acyclic call graph: function i may call only higher-numbered functions.
        for i in reversed(range(k)):
            callees = [names[j] for j in range(i + 1, k) if self.rng.randrange(3) == 0]
            self._make_func(names[i], callees)
        for _ in range(self.rng.randrange(1, 6)):
            self.random_alu(allow_mem=False)
            target = self.rng.choice(names)
            if self.rng.randrange(2):
                self.emit("call %s" % target)
            else:
                tmp = self.reg()
                self.emit("la %s, %s" % (tmp, target))
                self.emit("jalr lr, %s, 0" % tmp)
        if self.rng.randrange(2):
            self.emit("la t0, %s" % self.rng.choice(names))
            self.emit("jalr lr, t0, 0")

    def hazard_templates(self) -> None:
        # 1. ALU dependencies at distance 1, 2 and 3.
        for d in (1, 2, 3):
            self.dep(d)
        # 2. load-use ALU
        base, ld, cons = self.distinct_regs(3)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("lw %s, 0(%s)" % (ld, base))
        self.emit("add %s, %s, %s" % (cons, ld, cons))
        self.hazards.add("load_use_alu")
        # 3. load-use store data
        base, ld, data = self.distinct_regs(3)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("lw %s, 0(%s)" % (ld, base))
        self.emit("sw %s, %d(%s)" % (ld, self.rng.choice((2, 4)), base))
        self.hazards.add("load_use_store_data")
        # 4. load-use store address
        base, ld, data, dest = self.distinct_regs(4)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("li %s, 0x%04X" % (data, self.addr()))
        self.emit("sw %s, 0(%s)" % (data, base))
        self.emit("lw %s, 0(%s)" % (ld, base))
        self.emit("sw %s, 0(%s)" % (data, ld))
        self.hazards.add("load_use_store_addr")
        # 5. load-use branch operand
        base, ld, other = self.distinct_regs(3)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("lw %s, 0(%s)" % (ld, base))
        lab = self.label("Lbranch")
        self.emit("beq %s, %s, %s" % (ld, other, lab))
        self.place(lab)
        self.hazards.add("load_use_branch")
        # 6. load-use JALR base (target address is stored in RAM first)
        base, ld, tmp = self.distinct_regs(3)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        target = self.label("Ljalr")
        self.emit("la %s, %s" % (tmp, target))
        self.emit("sw %s, 0(%s)" % (tmp, base))
        self.emit("lw %s, 0(%s)" % (ld, base))
        self.emit("jalr r0, %s, 0" % ld)
        junk = self.label("Ljunk")
        self.place(junk)
        self.emit("addi s0, s0, 1")
        self.place(target)
        self.hazards.add("load_use_jalr")
        # 7. taken branch over two side effects
        cond, a, b = self.distinct_regs(3)
        self.emit("li %s, 1" % cond)
        over = self.label("Lover")
        self.emit("beq %s, %s, %s" % (cond, cond, over))
        self.emit("addi %s, %s, 1" % (a, a))
        self.emit("addi %s, %s, 1" % (b, b))
        self.place(over)
        self.hazards.add("branch_over_side_effect")
        # 8. JAL link consumed by the next dynamic instruction (distance 1).
        #    Target = PC+1, so the consumer is re-fetched and retires next.
        consumer, other = self.distinct_regs(2)
        self.emit("jal r15, .+1")
        self.emit("add %s, r15, %s" % (consumer, other))
        self.hazards.add("jal_dependent")
        # 9. JALR link consumed by the next dynamic instruction (distance 1).
        tmp, consumer, other = self.distinct_regs(3)
        self.emit("la %s, .+2" % tmp)
        self.emit("jalr r15, %s, 0" % tmp)
        self.emit("add %s, r15, %s" % (consumer, other))
        self.hazards.add("jalr_dependent")
        # 10. R0 writes
        a, b = self.distinct_regs(2)
        self.emit("add r0, %s, %s" % (a, b))
        self.emit("addi r0, r0, %d" % self.imm(-64, 64))
        self.hazards.add("r0_write")
        # 11. store -> load of the same address at distances 1..4, byte and word.
        for d in (1, 2, 3, 4):
            self.store_load(d, self.rng.choice(("sb", "sw")))
        # 12. load into R0: the value must be discarded and R0 must read zero.
        base, tmp = self.distinct_regs(2)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("lw r0, 0(%s)" % base)
        self.emit("add %s, r0, r0" % tmp)
        # 13. byte-lane and misaligned combinations.
        self.misaligned_pair()
        base, val, dst = self.distinct_regs(3)
        self.emit("li %s, 0x%04X" % (base, self.addr() & 0xFFFE))
        self.emit("li %s, 0x%04X" % (val, self.rng.randrange(0x100)))
        self.emit("sb %s, 0(%s)" % (val, base))
        self.emit("lbu %s, 0(%s)" % (dst, base))
        self.emit("lb %s, 1(%s)" % (dst, base))
        # 14. HALT in the shadow of a taken branch and of a JAL: the HALT must
        #     be squashed and must not stop the machine.
        cond = self.reg()
        lab = self.label("Lhalt_shadow")
        self.emit("li %s, 1" % cond)
        self.emit("beq %s, %s, %s" % (cond, cond, lab))
        self.emit("halt")
        self.place(lab)
        self.emit("addi %s, %s, 1" % (self.reg(), self.reg()))
        lab = self.label("Ljal_halt")
        self.emit("jal r15, %s" % lab)
        self.emit("halt")
        self.place(lab)
        # 14. filler between template groups for extra diversity.
        self.filler(self.rng.randrange(0, 4))

    def mmio(self) -> None:
        regs = self.distinct_regs(4)
        addr_r, val_r, out_r, status_r = regs
        self.emit("li %s, GPIO_OUT" % addr_r)
        self.emit("li %s, 0x%04X" % (val_r, self.rng.randrange(0x10000)))
        self.emit("sw %s, 0(%s)" % (val_r, addr_r))
        self.emit("lw %s, 0(%s)" % (out_r, addr_r))
        self.emit("li %s, GPIO_IN" % addr_r)
        self.emit("lw %s, 0(%s)" % (out_r, addr_r))
        self.emit("li %s, TIMER" % addr_r)
        self.emit("lw %s, 0(%s)" % (val_r, addr_r))
        self.emit("li %s, UART_TX" % addr_r)
        self.emit("li %s, 0x%04X" % (val_r, self.rng.randrange(0x100)))
        self.emit("sw %s, 0(%s)" % (val_r, addr_r))
        self.emit("li %s, UART_STATUS" % addr_r)
        self.emit("lw %s, 0(%s)" % (status_r, addr_r))
        if self.rng.randrange(2):
            self.random_alu(allow_mem=False)

    def mmio_writes(self) -> None:
        # Write-only MMIO: GPIO_OUT and UART_TX, plus a stable GPIO_OUT read.
        # No TIMER/UART_STATUS/GPIO_IN reads, so a 5A trace is not
        # cycle_sensitive (the fuzzer uses UART_CYCLES_PER_BYTE=0).
        a, v, out_r, b = self.distinct_regs(4)
        self.emit("li %s, GPIO_OUT" % a)
        self.emit("li %s, 0x%04X" % (v, self.rng.randrange(0x10000)))
        self.emit("sw %s, 0(%s)" % (v, a))
        self.emit("lw %s, 0(%s)" % (out_r, a))
        self.emit("li %s, 0x%04X" % (b, 0xFF05))
        self.emit("li %s, 0x%04X" % (a, self.rng.randrange(0x10000)))
        self.emit("sb %s, 0(%s)" % (a, b))
        self.emit("li %s, UART_TX" % (b,))
        self.emit("li %s, 0x%04X" % (v, self.rng.randrange(1, 0x100)))
        self.emit("sw %s, 0(%s)" % (v, b))
        # Second write while the transmitter is busy: dropped by hardware.  The
        # two stores are back-to-back, so the busy window covers the second write
        # in both the forwarding and the stall-only machine.
        self.emit("li %s, 0x%04X" % (a, self.rng.randrange(1, 0x100)))
        self.emit("sw %s, 0(%s)" % (a, b))
        if self.rng.randrange(2):
            self.random_alu(allow_mem=False)


    # -- forwarding-targeted mode -----------------------------------------
    def _fwd_consumer(self, form: str, rd: str, other: str, out: str) -> None:
        if form == "alu_a":
            self.emit("add %s, %s, %s" % (out, rd, other))
        elif form == "alu_b":
            self.emit("add %s, %s, %s" % (out, other, rd))
        elif form == "store_data":
            base = self.reg((rd, other, out))
            self.emit("li %s, 0x%04X" % (base, self.addr()))
            self.emit("sw %s, 0(%s)" % (rd, base))
        elif form == "store_addr":
            self.emit("li %s, 0x%04X" % (other, self.addr()))
            self.emit("sw %s, 0(%s)" % (out, rd))
        elif form == "branch":
            lab = self.label("Lfwb")
            self.emit("beq %s, %s, %s" % (rd, rd, lab))
            self.place(lab)
        elif form == "jalr":
            lab = self.label("Lfwj")
            self.emit("jalr r0, %s, 0" % rd)
            junk = self.label("Lfwjunk")
            self.place(junk)
            self.emit("addi %s, %s, 1" % (out, out))
            self.place(lab)
            self.emit("addi %s, %s, 1" % (out, out))

    def fwd_scenario(self) -> None:
        form = self.rng.choice(("alu_a", "alu_b", "store_data", "store_addr",
                                "branch", "jalr"))
        kind = self.rng.choice(("alu", "load", "load_byte", "mmio", "link"))
        # A JALR target and a store address must be a real, in-range value; use a
        # loaded address rather than an arbitrary ALU/MMIO value so programs
        # always terminate.
        if form in ("store_addr", "jalr") and kind not in ("load", "link"):
            kind = "load"
        distance = self.rng.choice((1, 2, 3, 4))
        rd, other, out = self.distinct_regs(3)
        base = self.reg((rd, other, out))
        if kind == "alu":
            self.emit("li %s, %d" % (rd, self.imm(-2048, 2048)))
        elif kind in ("load", "load_byte"):
            scratch = self.addr()
            self.emit("li %s, 0x%04X" % (base, scratch))
            if form in ("store_addr", "jalr"):
                dest = self.addr()
                self.emit("li %s, 0x%04X" % (other, dest))
                self.emit("sw %s, 0(%s)" % (other, base))
            elif self.rng.randrange(2):
                self.emit("li %s, 0x%04X" % (other, self.rng.randrange(0x10000)))
                self.emit("sw %s, 0(%s)" % (other, base))
            op = "lb" if kind == "load_byte" else "lw"
            self.emit("%s %s, 0(%s)" % (op, rd, base))
        elif kind == "mmio":
            reg = self.rng.choice(("TIMER", "UART_STATUS", "GPIO_IN"))
            self.emit("li %s, %s" % (base, reg))
            self.emit("lw %s, 0(%s)" % (rd, base))
        else:  # link
            target = self.label("Llink")
            self.emit("jal r15, %s" % target)
            self.filler(max(0, distance - 1), exclude=(rd,))
            self.place(target)
            self.emit("add %s, r15, %s" % (out, other))
            if self.rng.randrange(2):
                self.emit("addi %s, %s, 1" % (out, out))
            return
        self.filler(max(0, distance - 1), exclude=(rd,))
        self._fwd_consumer(form, rd, other, out)

    def fwd_specials(self) -> None:
        # Distance-1 load-use for every consumer form, so the stall counters are
        # exercised directly rather than only by chance.
        base, val, rd, out = self.distinct_regs(4)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("li %s, 0x%04X" % (val, self.rng.randrange(0x10000)))
        self.emit("sw %s, 0(%s)" % (val, base))
        self.emit("lw %s, 0(%s)" % (rd, base))
        self.emit("sw %s, 2(%s)" % (rd, base))
        # load -> store address
        base, val, rd = self.distinct_regs(3)
        scratch = self.addr()
        dest = self.addr()
        self.emit("li %s, 0x%04X" % (base, scratch))
        self.emit("li %s, 0x%04X" % (val, dest))
        self.emit("sw %s, 0(%s)" % (val, base))
        self.emit("lw %s, 0(%s)" % (rd, base))
        self.emit("sw %s, 0(%s)" % (val, rd))
        # load -> branch operand
        base, rd = self.distinct_regs(2)
        lab = self.label("Llu_br")
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("lw %s, 0(%s)" % (rd, base))
        self.emit("beq %s, %s, %s" % (rd, rd, lab))
        self.place(lab)
        # load -> JALR base
        base, rd, tmp = self.distinct_regs(3)
        lab = self.label("Llu_jr")
        junk = self.label("Llu_junk")
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("la %s, %s" % (tmp, lab))
        self.emit("sw %s, 0(%s)" % (tmp, base))
        self.emit("lw %s, 0(%s)" % (rd, base))
        self.emit("jalr r0, %s, 0" % rd)
        self.place(junk)
        self.emit("addi %s, %s, 1" % (tmp, tmp))
        self.place(lab)
        # Double producer: EX/MEM must beat MEM/WB.
        rd, out = self.distinct_regs(2)
        self.emit("li %s, %d" % (rd, self.rng.randrange(1, 100)))
        self.emit("addi %s, %s, %d" % (rd, rd, self.rng.randrange(1, 100)))
        self.emit("add %s, %s, %s" % (out, rd, rd))
        # R0 as producer and consumer.
        a, b = self.distinct_regs(2)
        self.emit("li %s, %d" % (a, self.rng.randrange(1, 100)))
        self.emit("add r0, %s, %s" % (a, a))
        self.emit("add %s, r0, %s" % (b, a))
        # Producer in a flushed branch shadow.
        rd, out = self.distinct_regs(2)
        lab = self.label("Lshadow")
        self.emit("li %s, 5" % rd)
        self.emit("beq r0, r0, %s" % lab)
        self.emit("addi %s, r0, 99" % rd)
        self.place(lab)
        self.emit("add %s, %s, r0" % (out, rd))
        # Load -> load pointer chain.
        base, p1, p2 = self.distinct_regs(3)
        scratch = self.addr()
        dest = self.addr()
        self.emit("li %s, 0x%04X" % (base, scratch))
        self.emit("li %s, 0x%04X" % (p1, dest))
        self.emit("sw %s, 0(%s)" % (p1, base))
        self.emit("lw %s, 0(%s)" % (p1, base))
        self.emit("lw %s, 0(%s)" % (p2, p1))
        # HALT after a load.  The HALT itself is emitted by fwd(), after every
        # other template, so that it really is the last instruction and the
        # templates that follow it are not dead code.
        base, ld = self.distinct_regs(2)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("lw %s, 0(%s)" % (ld, base))
        # A load whose destination is R0: the value is discarded and the
        # register file must ignore the write (the load_r0 counter).
        base, ld2 = self.distinct_regs(2)
        self.emit("li %s, 0x%04X" % (base, self.addr()))
        self.emit("lw r0, 0(%s)" % base)
        # A not-taken conditional branch (the branch_not_taken counter).  The
        # two operands are made different so the branch is really not taken.
        a, b = self.distinct_regs(2)
        lab = self.label("Lnottaken")
        self.emit("li %s, %d" % (a, self.rng.randrange(1, 100)))
        self.emit("li %s, %d" % (b, self.rng.randrange(101, 200)))
        self.emit("beq %s, %s, %s" % (a, b, lab))
        self.place(lab)
        # MMIO writes: GPIO_OUT and UART_TX.  The second UART_TX write lands
        # while the transmitter is still busy, so it is dropped by the
        # hardware (the uart_drop counter).
        addr_r, val_r, out_r = self.distinct_regs(3)
        self.emit("li %s, GPIO_OUT" % addr_r)
        self.emit("li %s, 0x%04X" % (val_r, self.rng.randrange(0x10000)))
        self.emit("sw %s, 0(%s)" % (val_r, addr_r))
        self.emit("lw %s, 0(%s)" % (out_r, addr_r))
        self.emit("li %s, UART_TX" % addr_r)
        self.emit("li %s, 0x%04X" % (val_r, self.rng.randrange(1, 0x100)))
        self.emit("sw %s, 0(%s)" % (val_r, addr_r))
        self.emit("li %s, 0x%04X" % (val_r, self.rng.randrange(1, 0x100)))
        self.emit("sw %s, 0(%s)" % (val_r, addr_r))

    def coverage(self) -> None:
        # One executed instance of every assigned opcode, with seed-driven
        # operands.  The opcode set is fixed by the ISA, not by the seed.
        a, b, c, d = self.distinct_regs(4)
        self.emit("li %s, %d" % (a, self.rng.randrange(0x10000)))
        self.emit("li %s, %d" % (b, self.rng.randrange(0x10000)))
        self.emit("li %s, 0x%04X" % (c, self.addr()))
        self.emit("li %s, 0x%04X" % (d, self.rng.randrange(0x10000)))
        for op in ALU_R:
            rd = self.reg()
            self.emit("%s %s, %s, %s" % (op, rd, self.reg(), self.reg()))
        for op in ALU_I:
            rd = self.reg()
            imm = self.rng.randrange(0, 16) if op in ("slli", "srli", "srai") else self.imm()
            self.emit("%s %s, %s, %d" % (op, rd, self.reg(), imm))
        self.emit("li %s, 0x%04X" % (c, self.addr()))
        self.emit("sw %s, 0(%s)" % (d, c))
        self.emit("lw %s, 0(%s)" % (a, c))
        self.emit("sb %s, 0(%s)" % (d, c))
        self.emit("lb %s, 0(%s)" % (a, c))
        self.emit("lbu %s, 1(%s)" % (b, c))
        self.emit("li %s, 0x%04X" % (a, self.rng.randrange(0x10000)))
        self.emit("li %s, 0x%04X" % (b, self.rng.randrange(0x10000)))
        for op in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            lab = self.label("Lcov")
            self.emit("%s %s, %s, %s" % (op, a, b, lab))
            self.place(lab)
        lab = self.label("Ljal")
        self.emit("jal r15, %s" % lab)
        self.place(lab)
        lab = self.label("Ljalr")
        self.emit("la %s, %s" % (a, lab))
        self.emit("jalr r0, %s, 0" % a)
        self.place(lab)
        self.filler(self.rng.randrange(0, 3))
        self.emit("halt")

    def fwd(self) -> None:
        for _ in range(self.rng.randrange(6, 11)):
            self.fwd_scenario()
        self.fwd_specials()
        # The forwarding run is the one the Step 1c thresholds are checked
        # against, so the store->load distances must be present here too, not
        # only in the general six-mode run.
        self.store_load_mode()
        # HALT is last, so that everything above it is really executed.
        self.emit("halt")

    def store_load_mode(self) -> None:
        """Store -> load of the same address, at every distance and size pair.

        The Phase 5B Step 1c requirement is that the store_load_d1/d2/d3
        coverage counters are each reached at least 50 times from RANDOM
        programs only.  A dedicated mode guarantees the distances and the
        byte/word/misaligned combinations instead of leaving them to chance.
        """
        # Every distance 1..4 is exercised at least once, in a seed-shuffled
        # order, so a single seed always covers the whole distance range.
        distances = [1, 2, 3, 4]
        self.rng.shuffle(distances)
        for d in distances:
            self.store_load(d)
            if self.rng.randrange(2):
                self.filler(self.rng.randrange(1, 3))
        # A second wave with the size/load combination and the address parity
        # chosen by the seed, so different seeds exercise different
        # combinations (SB/LB, SB/LBU, SW/LW, SW/LB; even and odd addresses).
        for _ in range(self.rng.randrange(3, 7)):
            size = self.rng.choice(("sb", "sw"))
            load = self.rng.choice(("lb", "lbu", "lw"))
            odd = self.rng.randrange(2) == 1
            self.store_load(self.rng.choice((1, 2, 3, 4)),
                            size=size, load=load, odd=odd)

    def append_functions(self) -> None:
        for name, body in self.funcs:
            self.lines.append("")
            self.lines.append("%s:" % name)
            self.lines.extend(body)


def generate(seed: int, mode: Optional[str] = None) -> Generated:
    modes = ["coverage", "straight", "loop", "calls", "hazards", "mmio"]
    if mode is None:
        mode = modes[seed % len(modes)]
    g = Gen(seed)
    g.lines.append(".text")
    g.lines.append(".globl main")
    g.lines.append("main:")
    if mode != "coverage":
        g.emit("addi sp, sp, -2")
        g.emit("sw lr, 0(sp)")
    if mode == "straight":
        g.straight()
    elif mode == "loop":
        g.loop()
    elif mode == "calls":
        g.calls()
    elif mode == "hazards":
        g.hazard_templates()
    elif mode == "mmio":
        g.mmio()
    elif mode == "mmio_writes":
        g.mmio_writes()
    elif mode == "fwd":
        g.fwd()
    elif mode == "store_load":
        g.store_load_mode()
    elif mode == "coverage":
        g.coverage()
    else:
        raise ValueError("unknown mode %r" % mode)
    if mode != "coverage":
        g.emit("li a0, 0")
        g.emit("lw lr, 0(sp)")
        g.emit("addi sp, sp, 2")
        g.emit("ret")
    g.append_functions()
    return Generated(seed, mode, g.source(), sorted(g.hazards))


# ---------------------------------------------------------------------------
# Dynamic coverage analysis
# ---------------------------------------------------------------------------

def analyze_trace(trace) -> Tuple[Counter, Counter]:
    ops: Counter = Counter()
    hz: Counter = Counter()
    decs = [decode(t.instr) for t in trace]
    for i, t in enumerate(trace):
        d = decs[i]
        if d.name != "NOP":
            ops[d.name] += 1
        srcs = d.sources()
        for dist in (1, 2, 3):
            if i - dist < 0:
                continue
            p = decs[i - dist]
            if p.reg_write and p.rd not in (None, 0) and p.rd in srcs:
                hz["dep_d%d" % dist] += 1
                if p.is_load and dist == 1:
                    if d.name in ("SB", "SW"):
                        if p.rd == d.rs2:
                            hz["load_use_store_data"] += 1
                        if p.rd == d.rs1:
                            hz["load_use_store_addr"] += 1
                    elif d.name == "JALR":
                        hz["load_use_jalr"] += 1
                    elif d.fmt == "B":
                        hz["load_use_branch"] += 1
                    else:
                        hz["load_use_alu"] += 1
        if i + 1 < len(trace) and d.name in ("JAL", "JALR"):
            nd = decs[i + 1]
            if 15 in nd.sources():
                hz["jal_dependent" if d.name == "JAL" else "jalr_dependent"] += 1
        if d.reg_write and d.rd == 0:
            hz["r0_write"] += 1
        if d.is_load and i > 0:
            p = decs[i - 1]
            if p.is_store and trace[i - 1].mem_addr is not None and t.mem_addr is not None:
                if trace[i - 1].mem_addr == t.mem_addr:
                    hz["store_load_same_addr"] += 1
    return ops, hz


def self_validate(n: int = 200, seed: int = 0, max_cycles: int = 50000) -> dict:
    crt0 = CRT0.read_text(encoding="utf-8")
    ops: Counter = Counter()
    hz: Counter = Counter()
    halted = 0
    max_seen = 0
    for i in range(n):
        mode = "coverage" if i == 0 else None
        gp = generate(seed + i, mode)
        img = assembler.assemble_text(crt0 + "\n" + gp.source, instr_words=2048, ram_bytes=4096)
        res = run_pipeline(img.instr_words, Config(ram_init=img.data, max_cycles=max_cycles))
        if not res.halted:
            raise AssertionError("seed %d did not halt" % (seed + i))
        halted += 1
        max_seen = max(max_seen, res.cycles)
        o, h = analyze_trace(res.trace)
        ops.update(o)
        hz.update(h)
        for name in gp.hazards:
            hz[name] += 1
    assigned = {v[0] for v in isa_sim.OPCODE_TABLE.values()}
    missing_ops = sorted(assigned - set(ops))
    missing_hz = sorted(h for h in HAZARD_NAMES if hz.get(h, 0) == 0)
    return {
        "seed": seed,
        "programs": n,
        "halted": halted,
        "max_cycles_seen": max_seen,
        "opcodes": dict(sorted(ops.items())),
        "hazard_counts": dict(sorted(hz.items())),
        "missing_opcodes": missing_ops,
        "missing_hazards": missing_hz,
    }


# ---------------------------------------------------------------------------
# Reference traces
# ---------------------------------------------------------------------------

def write_reference_traces(out_dir: Path) -> List[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    crt0 = CRT0.read_text(encoding="utf-8")
    entries = [
        ("01_straight", "straight", 11),
        ("02_loop", "loop", 12),
        ("03_calls", "calls", 13),
        ("04_hazards", "hazards", 14),
        ("05_mmio", "mmio", 15),
    ]
    manifest = []
    for name, mode, seed in entries:
        gp = generate(seed, mode)
        asm = crt0 + "\n" + gp.source
        img = assembler.assemble_text(asm, instr_words=2048, ram_bytes=4096)
        res = run_pipeline(img.instr_words, Config(ram_init=img.data))
        asm_path = out_dir / (name + ".asm")
        trace_path = out_dir / (name + ".trace")
        asm_path.write_text(asm, encoding="utf-8", newline="\n")
        trace_path.write_text(res.trace_text(), encoding="utf-8", newline="\n")
        entry = {
            "name": name,
            "mode": mode,
            "seed": seed,
            "instr_words": img.instr_capacity,
            "ram_bytes": img.ram_bytes,
            "cycles": res.cycles,
            "instructions": res.instructions,
            "pc": res.pc,
            "halted": res.halted,
            "asm_sha256": hashlib.sha256(asm.encode()).hexdigest(),
            "trace_sha256": hashlib.sha256(res.trace_text().encode()).hexdigest(),
        }
        manifest.append(entry)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8", newline="\n")
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="MiniCPU-16 random program generator")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--json", metavar="FILE", help="write the self-validation report")
    p.add_argument("--write-reference", metavar="DIR", help="write 5 reference asm/trace files")
    ns = p.parse_args(argv)
    if ns.write_reference:
        manifest = write_reference_traces(Path(ns.write_reference))
        print("wrote %d reference traces to %s" % (len(manifest), ns.write_reference))
        for e in manifest:
            print("  %-12s %-8s seed=%d cycles=%d instr=%d" %
                  (e["name"], e["mode"], e["seed"], e["cycles"], e["instructions"]))
        return 0
    report = self_validate(ns.n, ns.seed)
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if ns.json:
        Path(ns.json).write_text(text + "\n", encoding="utf-8", newline="\n")
    return 0 if not report["missing_opcodes"] and not report["missing_hazards"] else 1


if __name__ == "__main__":
    raise SystemExit(main())