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
    def __init__(self, seed: int):
        self.seed = seed
        self.rng = random.Random(seed)
        self.lines: List[str] = []
        self.n = 0
        self.hazards: Set[str] = set()

    def emit(self, text: str) -> None:
        self.lines.append("    " + text)

    def label(self, prefix: str = "Lg") -> str:
        self.n += 1
        return "%s_%d" % (prefix, self.n)

    def place(self, label: str) -> None:
        self.lines.append(label + ":")

    def source(self) -> str:
        return "\n".join(self.lines) + "\n"

    def reg(self, exclude: Sequence[str] = ()) -> str:
        choices = [r for r in SAFE if r not in exclude]
        return self.rng.choice(choices)

    def imm(self) -> int:
        return self.rng.randrange(-32768, 32768)

    def addr(self) -> int:
        return 0x0100 + (self.rng.randrange(0, 0x0E00) & 0xFFFE)

    def random_alu(self, exclude: Sequence[str] = (), allow_mem: bool = True) -> None:
        kind = self.rng.randrange(4)
        if kind == 0:
            rd = self.reg(exclude); rs1 = self.reg(exclude); rs2 = self.reg(exclude)
            self.emit("%s %s, %s, %s" % (self.rng.choice(ALU_R), rd, rs1, rs2))
        elif kind == 1:
            rd = self.reg(exclude); rs1 = self.reg(exclude)
            op = self.rng.choice(ALU_I)
            imm = self.rng.randrange(0, 16) if op in ("slli", "srli", "srai") else self.imm()
            self.emit("%s %s, %s, %d" % (op, rd, rs1, imm))
        elif kind == 2 and allow_mem:
            rd = self.reg(exclude)
            self.emit("li t2, 0x%04X" % self.addr())
            self.emit("%s %s, 0(t2)" % (self.rng.choice(["lb", "lbu", "lw"]), rd))
        elif kind == 3 and allow_mem:
            rd = self.reg(exclude)
            self.emit("li t2, 0x%04X" % self.addr())
            self.emit("li t3, 0x%04X" % (self.rng.randrange(0x10000)))
            self.emit("%s %s, 0(t2)" % (self.rng.choice(["sb", "sw"]), rd if self.rng.randrange(2) else "t3"))

    # ---------------------------------------------------------------- modes
    def straight(self) -> None:
        for _ in range(self.rng.randrange(8, 18)):
            self.random_alu()

    def loop(self) -> None:
        n = self.rng.randrange(1, 6)
        self.emit("li s4, %d" % n)
        top = self.label("Lloop")
        self.place(top)
        for _ in range(self.rng.randrange(3, 8)):
            self.random_alu(exclude=("s4",))
        self.emit("addi s4, s4, -1")
        self.emit("bnez s4, %s" % top)

    def calls(self) -> None:
        self.emit("call fn0")
        self.emit("call fn1")
        self.emit("la t0, fn0")
        self.emit("jalr lr, t0, 0")
        self.emit("la t0, fn1")
        self.emit("jalr lr, t0, 0")
        # functions are appended after main by the caller

    def hazard_templates(self) -> None:
        # 1. distance-1 ALU dependency chain
        self.emit("li t0, 5")
        self.emit("add t1, t0, t0")
        self.emit("add t2, t1, t1")
        self.emit("add t3, t2, t2")
        self.hazards.add("dep_d1")
        # 2. load-use ALU
        self.emit("li t0, 0x0100")
        self.emit("lw t1, 0(t0)")
        self.emit("add t2, t1, t1")
        self.hazards.add("load_use_alu")
        # 3. load-use store data
        self.emit("li t0, 0x0100")
        self.emit("lw t1, 0(t0)")
        self.emit("sw t1, 4(t0)")
        self.hazards.add("load_use_store_data")
        # 4. load-use store address
        self.emit("li t0, 0x0100")
        self.emit("li t2, 0x1234")
        self.emit("lw t1, 0(t0)")
        self.emit("sw t2, 0(t1)")
        self.hazards.add("load_use_store_addr")
        # 5. load-use branch
        self.emit("li t0, 0x0100")
        self.emit("lw t1, 0(t0)")
        lab = self.label("Lbranch")
        self.emit("beq t1, t0, %s" % lab)
        self.place(lab)
        self.hazards.add("load_use_branch")
        # 6. load-use JALR base (value stored in RAM first)
        self.emit("li t0, 0x0100")
        target = self.label("Ljalr")
        self.emit("la t2, %s" % target)
        self.emit("sw t2, 0(t0)")
        self.emit("lw t1, 0(t0)")
        self.emit("jalr r0, t1, 0")
        junk = self.label("Ljunk")
        self.place(junk)
        self.emit("addi s0, s0, 1")
        self.place(target)
        self.hazards.add("load_use_jalr")
        # 7. taken branch over two side effects
        self.emit("li t0, 1")
        over = self.label("Lover")
        self.emit("beq t0, t0, %s" % over)
        self.emit("addi s0, s0, 1")
        self.emit("addi s1, s1, 1")
        self.place(over)
        self.hazards.add("branch_over_side_effect")
        # 8. JAL dependents
        self.emit("jal r15, fn0")
        self.emit("add t0, r15, r15")
        self.hazards.add("jal_dependent")
        # 9. JALR dependents
        self.emit("la t0, fn1")
        self.emit("jalr r15, t0, 0")
        self.emit("add t1, r15, r15")
        self.hazards.add("jalr_dependent")
        # 10. R0 writes
        self.emit("add r0, t0, t1")
        self.emit("addi r0, r0, 1")
        self.hazards.add("r0_write")
        # 11. store followed by load of the same address
        self.emit("li t0, 0x0200")
        self.emit("li t1, 0x1234")
        self.emit("sw t1, 0(t0)")
        self.emit("lw t2, 0(t0)")
        self.hazards.add("store_load_same_addr")
        # 12. distance 2 and 3 dependencies
        self.emit("li t0, 7")
        self.emit("nop")
        self.emit("add t1, t0, t0")
        self.hazards.add("dep_d2")
        self.emit("nop")
        self.emit("nop")
        self.emit("add t2, t0, t0")
        self.hazards.add("dep_d3")

    def mmio(self) -> None:
        self.emit("li t0, GPIO_OUT")
        self.emit("li t1, 0x1234")
        self.emit("sw t1, 0(t0)")
        self.emit("lw t2, 0(t0)")
        self.emit("li t0, GPIO_IN")
        self.emit("lw t3, 0(t0)")
        self.emit("li t0, TIMER")
        self.emit("lw t1, 0(t0)")
        self.emit("li t0, UART_TX")
        self.emit("li t1, 0x41")
        self.emit("sw t1, 0(t0)")
        self.emit("li t0, UART_STATUS")
        self.emit("lw t2, 0(t0)")

    def coverage(self) -> None:
        self.emit("li t0, 1")
        self.emit("li t1, 2")
        self.emit("li t2, 0x0100")
        self.emit("li t3, 0x1234")
        for op in ALU_R:
            self.emit("%s t0, t1, t2" % op)
        for op in ALU_I:
            imm = 3 if op in ("slli", "srli", "srai") else -7
            self.emit("%s t0, t1, %d" % (op, imm))
        self.emit("sw t3, 0(t2)")
        self.emit("lw t0, 0(t2)")
        self.emit("sb t3, 2(t2)")
        self.emit("lb t0, 2(t2)")
        self.emit("lbu t1, 2(t2)")
        for op in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            lab = self.label("Lcov")
            self.emit("%s t0, t1, %s" % (op, lab))
            self.place(lab)
        lab = self.label("Ljal")
        self.emit("jal r15, %s" % lab)
        self.place(lab)
        lab = self.label("Ljalr")
        self.emit("la t0, %s" % lab)
        self.emit("jalr r0, t0, 0")
        self.place(lab)
        self.emit("halt")


def _append_functions(g: Gen, names: Sequence[str]) -> None:
    for i, name in enumerate(names):
        g.lines.append("")
        g.lines.append("%s:" % name)
        g.emit("li a0, %d" % (i + 1))
        g.emit("ret")


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
    elif mode == "coverage":
        g.coverage()
    else:
        raise ValueError("unknown mode %r" % mode)
    if mode != "coverage":
        g.emit("li a0, 0")
        g.emit("lw lr, 0(sp)")
        g.emit("addi sp, sp, 2")
        g.emit("ret")
    if mode == "calls":
        _append_functions(g, ["fn0", "fn1"])
    if mode in ("hazards",):
        _append_functions(g, ["fn0", "fn1"])
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
