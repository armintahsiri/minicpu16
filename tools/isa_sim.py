#!/usr/bin/env python3
"""MiniCPU-16 golden model (Phase 4).

Two layers share one ISA implementation:

  run_arch()      architectural layer: one instruction per cycle, exact ISA
                  semantics, used for functional tests and fast checks.
  run_pipeline()  timing layer: the 5-stage contract in
                  docs/MICROARCH_CONTRACT.md, used for cycle-exact traces.

The retirement trace is the Phase 5 comparison target.  See
docs/TRACE_FORMAT.md.

Cycle-dependent peripherals (UART busy, TIMER) are driven by the cycle number.
Only the timing layer is the cycle-accurate contract; see tools/isa_sim.py
docstrings and docs/MICROARCH_CONTRACT.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

# ---------------------------------------------------------------------------
# ISA tables (cross-checked by tools/check_consistency.py)
# ---------------------------------------------------------------------------

OPCODE_TABLE: Dict[int, Tuple[str, str]] = {
    0x00: ("HALT", "HALT"),
    0x01: ("ADD", "R"),
    0x02: ("SUB", "R"),
    0x03: ("AND", "R"),
    0x04: ("OR", "R"),
    0x05: ("XOR", "R"),
    0x06: ("SLL", "R"),
    0x07: ("SRL", "R"),
    0x08: ("SRA", "R"),
    0x09: ("SLT", "R"),
    0x0A: ("SLTU", "R"),
    0x0B: ("ADDI", "I"),
    0x0C: ("ANDI", "I"),
    0x0D: ("ORI", "I"),
    0x0E: ("XORI", "I"),
    0x0F: ("SLLI", "I"),
    0x10: ("SRLI", "I"),
    0x11: ("SRAI", "I"),
    0x12: ("SLTI", "I"),
    0x13: ("SLTIU", "I"),
    0x14: ("LB", "I"),
    0x15: ("LBU", "I"),
    0x16: ("LW", "I"),
    0x17: ("SB", "S"),
    0x18: ("SW", "S"),
    0x19: ("BEQ", "B"),
    0x1A: ("BNE", "B"),
    0x1B: ("BLT", "B"),
    0x1C: ("BGE", "B"),
    0x1D: ("BLTU", "B"),
    0x1E: ("BGEU", "B"),
    0x1F: ("JAL", "UJ"),
    0x20: ("JALR", "I"),
}

MMIO: Dict[str, int] = {
    "UART_TX": 0xFF00,
    "UART_STATUS": 0xFF02,
    "GPIO_OUT": 0xFF04,
    "GPIO_IN": 0xFF06,
    "TIMER": 0xFF08,
    "MMIO_BASE": 0xFF00,
    "MMIO_RESERVED": 0xFF0A,
}

RAM_BYTES_DEFAULT = 4096
RAM_BYTES_MAX = 0xFF00
INSTR_WORDS_DEFAULT = 2048
UART_CYCLES_DEFAULT = 10
HALT_WORD = 0x00000000


def s16(x: int) -> int:
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def z16(x: int) -> int:
    return x & 0xFFFF


def alu_op(name: str, a: int, b: int, imm: int) -> int:
    """Pure ALU semantics.  a and b are 16-bit patterns; b is used for R ops,
    imm for I ops."""
    a &= 0xFFFF
    b &= 0xFFFF
    imm &= 0xFFFF
    if name == "ADD":
        return (a + b) & 0xFFFF
    if name == "SUB":
        return (a - b) & 0xFFFF
    if name == "AND":
        return a & b
    if name == "OR":
        return a | b
    if name == "XOR":
        return a ^ b
    if name == "SLL":
        return (a << (b & 0xF)) & 0xFFFF
    if name == "SRL":
        return (a >> (b & 0xF)) & 0xFFFF
    if name == "SRA":
        return (s16(a) >> (b & 0xF)) & 0xFFFF
    if name == "SLT":
        return 1 if s16(a) < s16(b) else 0
    if name == "SLTU":
        return 1 if a < b else 0
    if name == "ADDI":
        return (a + imm) & 0xFFFF
    if name == "ANDI":
        return a & imm
    if name == "ORI":
        return a | imm
    if name == "XORI":
        return a ^ imm
    if name == "SLLI":
        return (a << (imm & 0xF)) & 0xFFFF
    if name == "SRLI":
        return (a >> (imm & 0xF)) & 0xFFFF
    if name == "SRAI":
        return (s16(a) >> (imm & 0xF)) & 0xFFFF
    if name == "SLTI":
        return 1 if s16(a) < s16(imm) else 0
    if name == "SLTIU":
        return 1 if a < imm else 0
    raise ValueError("not an ALU operation: %s" % name)


def imm_of(dec: "Decoded") -> int:
    return s16(dec.imm)


@dataclass(frozen=True)
class Decoded:
    word: int
    op: int
    name: str
    fmt: str
    rd: Optional[int]
    rs1: Optional[int]
    rs2: Optional[int]
    imm: int

    @property
    def is_load(self) -> bool:
        return self.name in ("LB", "LBU", "LW")

    @property
    def is_store(self) -> bool:
        return self.name in ("SB", "SW")

    @property
    def is_branch(self) -> bool:
        return self.fmt == "B"

    @property
    def reg_write(self) -> bool:
        return self.fmt in ("R", "I", "UJ") and self.rd is not None

    def sources(self) -> List[int]:
        if self.fmt == "R":
            return [self.rs1, self.rs2]  # type: ignore[list-item]
        if self.fmt == "I":
            return [self.rs1]  # type: ignore[list-item]
        if self.fmt in ("S", "B"):
            return [self.rs1, self.rs2]  # type: ignore[list-item]
        return []


def decode(word: int) -> Decoded:
    word &= 0xFFFFFFFF
    op = (word >> 24) & 0xFF
    if op == 0:
        return Decoded(word, 0, "HALT", "HALT", None, None, None, 0)
    info = OPCODE_TABLE.get(op)
    if info is None:
        return Decoded(word, op, "NOP", "NOP", None, None, None, 0)
    name, fmt = info
    rd = (word >> 20) & 0xF
    rs1 = (word >> 16) & 0xF
    rs2: Optional[int]
    if fmt == "R":
        rs2 = (word >> 12) & 0xF
    elif fmt in ("S", "B"):
        rs2 = (word >> 20) & 0xF
        rd = None
    else:
        rs2 = None
    return Decoded(word, op, name, fmt, rd, rs1, rs2, word & 0xFFFF)


# ---------------------------------------------------------------------------
# Configuration and results
# ---------------------------------------------------------------------------

GpioSchedule = Union[int, Sequence[Tuple[int, int]], Callable[[int], int], None]


@dataclass
class Config:
    instr_words: int = INSTR_WORDS_DEFAULT
    ram_bytes: int = RAM_BYTES_DEFAULT
    uart_cycles_per_byte: int = UART_CYCLES_DEFAULT
    gpio_in: GpioSchedule = 0
    max_cycles: int = 200000
    max_instructions: int = 200000
    ram_init: bytes = b""

    def validate(self) -> None:
        if self.instr_words <= 0:
            raise ValueError("instr_words must be positive")
        if self.ram_bytes <= 0 or self.ram_bytes > RAM_BYTES_MAX:
            raise ValueError("ram_bytes must be in 1..0xFF00")
        if self.ram_bytes % 2:
            raise ValueError("ram_bytes must be even")
        if self.uart_cycles_per_byte < 0:
            raise ValueError("uart_cycles_per_byte must be >= 0")
        if len(self.ram_init) > self.ram_bytes:
            raise ValueError("ram_init is larger than ram_bytes")


class WatchdogError(RuntimeError):
    pass


@dataclass
class TraceRecord:
    seq: int
    retire_cycle: int
    pc: int
    instr: int
    rd: Optional[int] = None
    value: Optional[int] = None
    mem_kind: Optional[str] = None
    mem_addr: Optional[int] = None
    mem_size: Optional[int] = None
    mem_data: Optional[int] = None
    taken: int = 0
    target: Optional[int] = None

    def line(self) -> str:
        def hx(v: Optional[int], width: int) -> str:
            return "-" if v is None else ("%0*X" % (width, v & ((1 << (4 * width)) - 1)))
        return " ".join([
            str(self.seq),
            str(self.retire_cycle),
            "%04X" % (self.pc & 0xFFFF),
            "%08X" % (self.instr & 0xFFFFFFFF),
            "-" if self.rd is None else "r%d" % self.rd,
            hx(self.value, 4),
            self.mem_kind or "-",
            hx(self.mem_addr, 4),
            "-" if self.mem_size is None else str(self.mem_size),
            hx(self.mem_data, 4),
            "1" if self.taken else "0",
            hx(self.target, 4),
        ])


TRACE_HEADER = "\n".join([
    "# minicpu16-retirement-trace v1",
    "# seq retire pc instr rd value memop memaddr memsize memdata taken target",
])


@dataclass
class Result:
    regs: List[int]
    pc: int
    ram: bytes
    uart: List[int]
    gpio_out: int
    cycles: int
    instructions: int
    halted: bool
    trace: List[TraceRecord]
    stalls: int
    flushes: int
    instr_words: int
    ram_bytes: int
    uart_cycles_per_byte: int

    def to_dict(self) -> dict:
        return {
            "regs": ["0x%04X" % (r & 0xFFFF) for r in self.regs],
            "pc": "0x%04X" % (self.pc & 0xFFFF),
            "ram_hex": self.ram.hex(),
            "ram_bytes": self.ram_bytes,
            "uart": list(self.uart),
            "gpio_out": "0x%04X" % (self.gpio_out & 0xFFFF),
            "cycles": self.cycles,
            "instructions": self.instructions,
            "halted": self.halted,
            "stalls": self.stalls,
            "flushes": self.flushes,
            "instr_words": self.instr_words,
            "uart_cycles_per_byte": self.uart_cycles_per_byte,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def trace_text(self) -> str:
        return TRACE_HEADER + "\n" + "\n".join(r.line() for r in self.trace) + ("\n" if self.trace else "")

    def instruction_mix(self) -> Dict[str, int]:
        mix: Dict[str, int] = {}
        for r in self.trace:
            d = decode(r.instr)
            if d.fmt in ("R", "I") and d.name not in ("LB", "LBU", "LW", "JALR"):
                k = "alu"
            elif d.name in ("LB", "LBU", "LW"):
                k = "load"
            elif d.name in ("SB", "SW"):
                k = "store"
            elif d.fmt == "B":
                k = "branch"
            elif d.name in ("JAL", "JALR"):
                k = "jump"
            elif d.name == "HALT":
                k = "halt"
            else:
                k = "nop"
            mix[k] = mix.get(k, 0) + 1
        return mix


# ---------------------------------------------------------------------------
# Machine state and memory
# ---------------------------------------------------------------------------

class Machine:
    def __init__(self, cfg: Config, words: Sequence[int]):
        cfg.validate()
        self.cfg = cfg
        self.words = [w & 0xFFFFFFFF for w in words]
        self.regs = [0] * 16
        self.ram = bytearray(cfg.ram_bytes)
        if cfg.ram_init:
            self.ram[:len(cfg.ram_init)] = cfg.ram_init
        self.pc = 0
        self.cycle = 0
        self.instructions = 0
        self.halted = False
        self.stop_fetch = False
        self.uart: List[int] = []
        self.uart_busy_until = 0
        self.gpio_out = 0
        self.trace: List[TraceRecord] = []
        self.stalls = 0
        self.flushes = 0
        self.final_pc = 0
        self._gpio_schedule = cfg.gpio_in
        self._gpio_initial = 0
        if isinstance(self._gpio_schedule, int):
            self._gpio_initial = self._gpio_schedule & 0xFFFF
            self._gpio_schedule = None
        elif isinstance(self._gpio_schedule, (list, tuple)):
            self._gpio_initial = 0

    # -- fetch -------------------------------------------------------------
    def fetch(self, pc: int) -> int:
        pc &= 0xFFFF
        if pc >= self.cfg.instr_words or pc >= len(self.words):
            return HALT_WORD
        return self.words[pc]

    # -- GPIO --------------------------------------------------------------
    def gpio_in_value(self) -> int:
        s = self._gpio_schedule
        if s is None:
            return self._gpio_initial & 0xFFFF
        if callable(s):
            return int(s(self.cycle)) & 0xFFFF
        val = self._gpio_initial
        for c, v in s:  # type: ignore[misc]
            if c <= self.cycle:
                val = v
            else:
                break
        return int(val) & 0xFFFF

    # -- UART --------------------------------------------------------------
    def uart_busy(self) -> bool:
        return self.cycle <= self.uart_busy_until

    def uart_tx(self, byte: int) -> None:
        if self.uart_busy():
            return
        self.uart.append(byte & 0xFF)
        self.uart_busy_until = self.cycle + self.cfg.uart_cycles_per_byte

    # -- byte/word memory --------------------------------------------------
    def _ram_read_byte(self, addr: int) -> int:
        addr &= 0xFFFF
        if addr < self.cfg.ram_bytes:
            return self.ram[addr]
        return 0

    def _ram_write_byte(self, addr: int, value: int) -> None:
        addr &= 0xFFFF
        if addr < self.cfg.ram_bytes:
            self.ram[addr] = value & 0xFF

    def _mmio_read_byte(self, addr: int) -> int:
        reg = addr & 0xFFFE
        if reg == MMIO["UART_TX"]:
            return 0
        if reg == MMIO["UART_STATUS"]:
            return 1 if self.uart_busy() else 0
        if reg == MMIO["GPIO_OUT"]:
            return self.gpio_out & 0xFF
        if reg == MMIO["GPIO_IN"]:
            return self.gpio_in_value() & 0xFF
        if reg == MMIO["TIMER"]:
            return self.cycle & 0xFF
        return 0

    def _mmio_read_word(self, addr: int) -> int:
        reg = addr & 0xFFFE
        if reg == MMIO["UART_TX"]:
            return 0
        if reg == MMIO["UART_STATUS"]:
            return 1 if self.uart_busy() else 0
        if reg == MMIO["GPIO_OUT"]:
            return self.gpio_out & 0xFFFF
        if reg == MMIO["GPIO_IN"]:
            return self.gpio_in_value() & 0xFFFF
        if reg == MMIO["TIMER"]:
            return self.cycle & 0xFFFF
        return 0

    def _mmio_write_byte(self, addr: int, value: int) -> None:
        reg = addr & 0xFFFE
        value &= 0xFF
        if reg == MMIO["UART_TX"]:
            self.uart_tx(value)
        elif reg == MMIO["GPIO_OUT"]:
            self.gpio_out = (self.gpio_out & 0xFF00) | value
        # all other MMIO byte writes are ignored

    def _mmio_write_word(self, addr: int, value: int) -> None:
        reg = addr & 0xFFFE
        value &= 0xFFFF
        if reg == MMIO["UART_TX"]:
            self.uart_tx(value & 0xFF)
        elif reg == MMIO["GPIO_OUT"]:
            self.gpio_out = value
        # status/gpio_in/timer/reserved are read-only

    def is_mmio(self, addr: int) -> bool:
        return 0xFF00 <= (addr & 0xFFFF) <= 0xFFFF

    def load(self, name: str, addr: int) -> Tuple[int, int, int]:
        """Return (effective_address, size, value)."""
        addr &= 0xFFFF
        if name == "LW":
            ea = addr & 0xFFFE
            if self.is_mmio(ea):
                return ea, 2, self._mmio_read_word(ea)
            lo = self._ram_read_byte(ea)
            hi = self._ram_read_byte(ea + 1)
            return ea, 2, (lo | (hi << 8)) & 0xFFFF
        val = self._mmio_read_byte(addr) if self.is_mmio(addr) else self._ram_read_byte(addr)
        if name == "LB":
            byte = val & 0xFF
            val = (byte | 0xFF00) if (byte & 0x80) else byte
        else:
            val &= 0xFF
        return addr, 1, val

    def store(self, name: str, addr: int, value: int) -> Tuple[int, int]:
        """Return (effective_address, size)."""
        addr &= 0xFFFF
        value &= 0xFFFF
        if name == "SW":
            ea = addr & 0xFFFE
            if self.is_mmio(ea):
                self._mmio_write_word(ea, value)
            else:
                self._ram_write_byte(ea, value & 0xFF)
                self._ram_write_byte(ea + 1, (value >> 8) & 0xFF)
            return ea, 2
        if self.is_mmio(addr):
            self._mmio_write_byte(addr, value)
        else:
            self._ram_write_byte(addr, value)
        return addr, 1

    # -- trace -------------------------------------------------------------
    def add_trace(self, pc: int, word: int, retire_cycle: int, rd: Optional[int],
                  value: Optional[int], mem: Optional[Tuple[str, int, int, int]],
                  taken: int, target: Optional[int]) -> None:
        self.instructions += 1
        seq = self.instructions
        mem_kind = mem[0] if mem else None
        mem_addr = mem[1] if mem else None
        mem_size = mem[2] if mem else None
        mem_data = mem[3] if mem else None
        self.trace.append(TraceRecord(seq, retire_cycle, pc, word,
                                      rd=rd if rd != 0 else None,
                                      value=value if rd != 0 else None,
                                      mem_kind=mem_kind, mem_addr=mem_addr,
                                      mem_size=mem_size, mem_data=mem_data,
                                      taken=taken, target=target))

    def result(self) -> Result:
        return Result(self.regs[:], self.final_pc, bytes(self.ram), list(self.uart),
                      self.gpio_out, self.cycle, self.instructions, self.halted,
                      self.trace, self.stalls, self.flushes, self.cfg.instr_words,
                      self.cfg.ram_bytes, self.cfg.uart_cycles_per_byte)


# ---------------------------------------------------------------------------
# Architectural layer
# ---------------------------------------------------------------------------

def run_arch(words: Sequence[int], cfg: Optional[Config] = None,
             init_regs: Optional[Sequence[int]] = None) -> Result:
    cfg = cfg or Config()
    m = Machine(cfg, words)
    if init_regs:
        for i, v in enumerate(init_regs[:16]):
            m.regs[i] = int(v) & 0xFFFF
            if i == 0:
                m.regs[0] = 0
    pc = 0
    while True:
        m.cycle += 1
        if m.cycle > cfg.max_cycles:
            raise WatchdogError("cycle limit %d exceeded at instruction index 0x%04X" % (cfg.max_cycles, pc))
        if m.instructions >= cfg.max_instructions:
            raise WatchdogError("instruction limit %d exceeded at instruction index 0x%04X" % (cfg.max_instructions, pc))
        word = m.fetch(pc)
        d = decode(word)
        rd: Optional[int] = None
        value: Optional[int] = None
        mem: Optional[Tuple[str, int, int, int]] = None
        taken = 0
        target: Optional[int] = None
        next_pc = (pc + 1) & 0xFFFF

        if d.name == "HALT":
            m.final_pc = pc
            m.add_trace(pc, word, m.cycle, None, None, None, 0, None)
            m.halted = True
            break
        elif d.name == "NOP":
            m.add_trace(pc, word, m.cycle, None, None, None, 0, None)
        elif d.fmt in ("R", "I") and d.name not in ("LB", "LBU", "LW", "JALR"):
            a = m.regs[d.rs1] if d.rs1 is not None else 0
            b = m.regs[d.rs2] if d.rs2 is not None else d.imm
            r = alu_op(d.name, a, b, d.imm)
            rd = d.rd
            value = r
            if rd is not None and rd != 0:
                m.regs[rd] = r
            m.add_trace(pc, word, m.cycle, rd, value, None, 0, None)
        elif d.is_load:
            base = m.regs[d.rs1] if d.rs1 is not None else 0
            ea, size, val = m.load(d.name, (base + s16(d.imm)) & 0xFFFF)
            rd = d.rd
            value = val
            if rd is not None and rd != 0:
                m.regs[rd] = val
            m.add_trace(pc, word, m.cycle, rd, value, ("L", ea, size, val), 0, None)
        elif d.is_store:
            base = m.regs[d.rs1] if d.rs1 is not None else 0
            data = m.regs[d.rs2] if d.rs2 is not None else 0
            ea, size = m.store(d.name, (base + s16(d.imm)) & 0xFFFF, data)
            m.add_trace(pc, word, m.cycle, None, None, ("S", ea, size, data & 0xFFFF), 0, None)
        elif d.is_branch:
            a = m.regs[d.rs1] if d.rs1 is not None else 0
            b = m.regs[d.rs2] if d.rs2 is not None else 0
            t = branch_taken(d.name, a, b)
            if t:
                target = (pc + 1 + s16(d.imm)) & 0xFFFF
                next_pc = target
                taken = 1
            m.add_trace(pc, word, m.cycle, None, None, None, taken, target)
        elif d.name == "JAL":
            rd = d.rd
            value = (pc + 1) & 0xFFFF
            if rd is not None and rd != 0:
                m.regs[rd] = value
            target = (pc + 1 + s16(d.imm)) & 0xFFFF
            next_pc = target
            taken = 1
            m.add_trace(pc, word, m.cycle, rd, value, None, 1, target)
        elif d.name == "JALR":
            base = m.regs[d.rs1] if d.rs1 is not None else 0
            rd = d.rd
            value = (pc + 1) & 0xFFFF
            target = (base + s16(d.imm)) & 0xFFFF
            if rd is not None and rd != 0:
                m.regs[rd] = value
            next_pc = target
            taken = 1
            m.add_trace(pc, word, m.cycle, rd, value, None, 1, target)
        else:
            m.add_trace(pc, word, m.cycle, None, None, None, 0, None)

        pc = next_pc
    m.pc = m.final_pc
    return m.result()


def branch_taken(name: str, a: int, b: int) -> bool:
    a &= 0xFFFF
    b &= 0xFFFF
    if name == "BEQ":
        return a == b
    if name == "BNE":
        return a != b
    if name == "BLT":
        return s16(a) < s16(b)
    if name == "BGE":
        return s16(a) >= s16(b)
    if name == "BLTU":
        return a < b
    if name == "BGEU":
        return a >= b
    raise ValueError("not a branch: %s" % name)


# ---------------------------------------------------------------------------
# Timing layer (5-stage pipeline)
# ---------------------------------------------------------------------------

@dataclass
class IfId:
    valid: bool = False
    pc: int = 0
    word: int = 0
    dec: Optional[Decoded] = None


@dataclass
class IdEx:
    valid: bool = False
    pc: int = 0
    dec: Optional[Decoded] = None
    rs1_val: int = 0
    rs2_val: int = 0


@dataclass
class ExMem:
    valid: bool = False
    pc: int = 0
    dec: Optional[Decoded] = None
    result: int = 0
    store_data: int = 0
    addr: int = 0
    reg_write: bool = False
    is_load: bool = False
    is_store: bool = False
    size: int = 0
    taken: bool = False
    target: Optional[int] = None
    is_halt: bool = False


@dataclass
class MemWb:
    valid: bool = False
    pc: int = 0
    dec: Optional[Decoded] = None
    wb_value: int = 0
    reg_write: bool = False
    mem: Optional[Tuple[str, int, int, int]] = None
    taken: bool = False
    target: Optional[int] = None
    is_halt: bool = False


def run_pipeline(words: Sequence[int], cfg: Optional[Config] = None,
                 init_regs: Optional[Sequence[int]] = None) -> Result:
    cfg = cfg or Config()
    m = Machine(cfg, words)
    if init_regs:
        for i, v in enumerate(init_regs[:16]):
            m.regs[i] = int(v) & 0xFFFF
            if i == 0:
                m.regs[0] = 0
    pc = 0
    if_id = IfId()
    id_ex = IdEx()
    ex_mem = ExMem()
    mem_wb = MemWb()

    def read_reg(reg: Optional[int]) -> int:
        if reg is None or reg == 0:
            return 0
        if mem_wb.valid and mem_wb.reg_write and mem_wb.dec is not None and mem_wb.dec.rd == reg:
            return mem_wb.wb_value & 0xFFFF
        return m.regs[reg] & 0xFFFF

    def forward(reg: Optional[int], value: int) -> int:
        if reg is None or reg == 0:
            return 0
        if ex_mem.valid and ex_mem.reg_write and ex_mem.dec is not None and ex_mem.dec.rd == reg:
            if not ex_mem.is_load:
                return ex_mem.result & 0xFFFF
            # A load in MEM cannot forward; the hazard logic must have stalled.
            return value & 0xFFFF
        if mem_wb.valid and mem_wb.reg_write and mem_wb.dec is not None and mem_wb.dec.rd == reg:
            return mem_wb.wb_value & 0xFFFF
        return value & 0xFFFF

    while True:
        m.cycle += 1
        if m.cycle > cfg.max_cycles:
            raise WatchdogError("cycle limit %d exceeded at pc 0x%04X" % (cfg.max_cycles, pc))
        if m.instructions >= cfg.max_instructions:
            raise WatchdogError("instruction limit %d exceeded at pc 0x%04X" % (cfg.max_instructions, pc))

        # ---- WB ----------------------------------------------------------
        if mem_wb.valid and mem_wb.dec is not None:
            d = mem_wb.dec
            if mem_wb.reg_write and d.rd is not None and d.rd != 0:
                m.regs[d.rd] = mem_wb.wb_value & 0xFFFF
            m.add_trace(mem_wb.pc, d.word, m.cycle,
                        d.rd if mem_wb.reg_write else None,
                        mem_wb.wb_value if mem_wb.reg_write else None,
                        mem_wb.mem, 1 if mem_wb.taken else 0, mem_wb.target)
            if mem_wb.is_halt:
                m.final_pc = mem_wb.pc
                m.halted = True
                break

        # ---- MEM ---------------------------------------------------------
        mw_next = MemWb()
        if ex_mem.valid and ex_mem.dec is not None:
            d = ex_mem.dec
            wb_value = ex_mem.result & 0xFFFF
            mem: Optional[Tuple[str, int, int, int]] = None
            if ex_mem.is_load:
                ea, size, val = m.load(d.name, ex_mem.addr)
                wb_value = val
                mem = ("L", ea, size, val)
            elif ex_mem.is_store:
                ea, size = m.store(d.name, ex_mem.addr, ex_mem.store_data)
                mem = ("S", ea, size, ex_mem.store_data & (0xFF if size == 1 else 0xFFFF))
            mw_next = MemWb(True, ex_mem.pc, d, wb_value, ex_mem.reg_write,
                            mem, ex_mem.taken, ex_mem.target, ex_mem.is_halt)

        # ---- EX ----------------------------------------------------------
        exm_next = ExMem()
        redirect = False
        target: Optional[int] = None
        halt_ex = False
        if id_ex.valid and id_ex.dec is not None:
            d = id_ex.dec
            a = forward(d.rs1, id_ex.rs1_val)
            b = forward(d.rs2, id_ex.rs2_val)
            imm = s16(d.imm)
            result = 0
            store_data = 0
            addr = 0
            reg_write = d.reg_write
            is_load = d.is_load
            is_store = d.is_store
            size = 0
            taken = False
            if d.name == "HALT":
                halt_ex = True
            elif d.name == "NOP":
                pass
            elif d.fmt in ("R", "I") and d.name not in ("LB", "LBU", "LW", "JALR"):
                result = alu_op(d.name, a, b, d.imm)
            elif d.is_load or d.is_store:
                addr = (a + imm) & 0xFFFF
                size = 2 if d.name in ("LW", "SW") else 1
                if d.is_store:
                    store_data = b & 0xFFFF
                    result = addr
                else:
                    result = addr
            elif d.is_branch:
                if branch_taken(d.name, a, b):
                    target = (id_ex.pc + 1 + imm) & 0xFFFF
                    taken = True
                    redirect = True
            elif d.name == "JAL":
                result = (id_ex.pc + 1) & 0xFFFF
                target = (id_ex.pc + 1 + imm) & 0xFFFF
                taken = True
                redirect = True
            elif d.name == "JALR":
                result = (id_ex.pc + 1) & 0xFFFF
                target = (a + imm) & 0xFFFF
                taken = True
                redirect = True
            exm_next = ExMem(True, id_ex.pc, d, result, store_data, addr,
                             reg_write, is_load, is_store, size, taken, target, halt_ex)

        # ---- ID / hazard -------------------------------------------------
        stall = False
        if id_ex.valid and id_ex.dec is not None and id_ex.dec.is_load:
            lrd = id_ex.dec.rd
            if lrd is not None and lrd != 0 and if_id.valid and if_id.dec is not None:
                if lrd in if_id.dec.sources():
                    stall = True

        if redirect or halt_ex:
            stall = False

        # ---- next pipeline state ----------------------------------------
        next_if = IfId()
        next_idex = IdEx()
        next_pc = pc
        if redirect:
            next_pc = target if target is not None else pc
            m.flushes += 1
        elif halt_ex:
            next_pc = pc
            m.stop_fetch = True
            m.flushes += 1
        elif stall:
            next_if = if_id
            next_idex = IdEx()
            next_pc = pc
            m.stalls += 1
        else:
            if not m.stop_fetch:
                w = m.fetch(pc)
                next_if = IfId(True, pc, w, decode(w))
                next_pc = (pc + 1) & 0xFFFF
            else:
                next_pc = pc
            if if_id.valid and if_id.dec is not None:
                d = if_id.dec
                next_idex = IdEx(True, if_id.pc, d,
                                 read_reg(d.rs1), read_reg(d.rs2))

        if_id = next_if
        id_ex = next_idex
        ex_mem = exm_next
        mem_wb = mw_next
        pc = next_pc

    m.pc = m.final_pc
    return m.result()


if __name__ == "__main__":
    import argparse
    import sys
    p = argparse.ArgumentParser(description="MiniCPU-16 golden model")
    p.add_argument("image", help="hex file with one 32-bit instruction per line")
    p.add_argument("--arch", action="store_true", help="architectural layer (1 cycle/instruction)")
    p.add_argument("--trace", metavar="FILE", help="write retirement trace")
    p.add_argument("--json", metavar="FILE", help="write final state JSON")
    p.add_argument("--ram-bytes", type=lambda s: int(s, 0), default=RAM_BYTES_DEFAULT)
    p.add_argument("--instr-words", type=lambda s: int(s, 0), default=INSTR_WORDS_DEFAULT)
    p.add_argument("--uart-cycles", type=int, default=UART_CYCLES_DEFAULT)
    p.add_argument("--gpio-in", type=lambda s: int(s, 0), default=0)
    p.add_argument("--max-cycles", type=int, default=200000)
    p.add_argument("--data", metavar="FILE", help="RAM init file, two hex digits per line")
    ns = p.parse_args()
    words: List[int] = []
    with open(ns.image, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            words.append(int(s, 16))
    ram_init = b""
    if ns.data:
        vals = []
        with open(ns.data, "r", encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if not t or t.startswith("#"):
                    continue
                vals.append(int(t, 16) & 0xFF)
        ram_init = bytes(vals)
    cfg = Config(ns.instr_words, ns.ram_bytes, ns.uart_cycles, ns.gpio_in, ns.max_cycles,
                 ram_init=ram_init)
    res = run_arch(words, cfg) if ns.arch else run_pipeline(words, cfg)
    sys.stdout.write(res.to_json() + "\n")
    if ns.trace:
        with open(ns.trace, "w", encoding="utf-8", newline="\n") as f:
            f.write(res.trace_text())
    if ns.json:
        with open(ns.json, "w", encoding="utf-8", newline="\n") as f:
            f.write(res.to_json() + "\n")
