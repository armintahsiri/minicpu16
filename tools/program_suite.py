#!/usr/bin/env python3
"""Program suite definitions and expected results.

Used by tools/test_programs.py and tools/bench.py.  Each program is assembled
with crt0 and the runtime, run on the timing layer, and its architectural
results are checked against independently computed expectations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import assembler
from isa_sim import Config, Result, run_pipeline

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = [
    "programs/runtime/crt0.s",
    "programs/runtime/arith.s",
    "programs/runtime/string.s",
]

Check = Tuple[str, object, object]


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def word_at(res: Result, addr: int) -> int:
    return res.ram[addr & 0xFFFF] | (res.ram[(addr + 1) & 0xFFFF] << 8)


def _checks_fib(img, res) -> List[Check]:
    return [("fib(16)", word_at(res, img.symbols["result"].value), 987)]


def _checks_fact(img, res) -> List[Check]:
    return [("8!", word_at(res, img.symbols["result"].value), 40320)]


def _checks_bubble(img, res) -> List[Check]:
    base = img.symbols["arr"].value
    original = [_s16(img.data[base + 2 * i] | (img.data[base + 2 * i + 1] << 8)) for i in range(16)]
    expected = sorted(original)
    out: List[Check] = []
    for i in range(16):
        out.append(("arr[%d]" % i, _s16(word_at(res, base + 2 * i)), expected[i]))
    return out


def _checks_crc(img, res) -> List[Check]:
    return [("CRC-16/XMODEM", word_at(res, img.symbols["crc"].value), 0x31C3)]


def _checks_memory(img, res) -> List[Check]:
    src = img.symbols["src"].value
    dst = img.symbols["dst"].value
    n = 0
    while img.data[src + n] != 0:
        n += 1
    out: List[Check] = [
        ("strlen", word_at(res, img.symbols["len"].value), n),
        ("strcmp equal", _s16(word_at(res, img.symbols["cmp1"].value)), 0),
        ("strcmp diff", _s16(word_at(res, img.symbols["cmp2"].value)), -1),
    ]
    for i in range(32):
        out.append(("memcpy[%d]" % i, res.ram[dst + i], img.data[src + i]))
    return out


def _checks_muldiv(img, res) -> List[Check]:
    base = img.symbols["res"].value
    expected = [
        (1234 * 5678) & 0xFFFF,
        (-1234 * 5678) & 0xFFFF,
        (0xFFFF * 0xFFFF) & 0xFFFF,
        50000 // 7,
        50000 % 7,
        0xFFFF,
        50000,
        (-5000 // 7) & 0xFFFF,   # Python // floors; fix below for truncation
        (-5000) % 7,
        (-32768 // -1) & 0xFFFF,
        0,
        (-5000) % 7,
        50000 % 7,
    ]
    # C truncates toward zero; Python // floors.  Adjust the signed cases.
    expected[7] = 0xFD36
    expected[8] = 0xFFFE
    expected[11] = 0xFFFE
    names = ["mul", "mul_neg", "mul_ff", "udiv_q", "udiv_r", "udiv0_q", "udiv0_r",
             "div_q", "div_r", "div_min", "div_min_r", "mod", "umod"]
    return [("res.%s" % names[i], _s16(word_at(res, base + 2 * i)), _s16(v))
            for i, v in enumerate(expected)]


def _checks_hello(img, res) -> List[Check]:
    return [("UART bytes", bytes(res.uart), b"Hello, MiniCPU-16!\n")]


def _checks_switch(img, res) -> List[Check]:
    base = img.symbols["out"].value
    return [("switch[%d]" % i, _s16(word_at(res, base + 2 * i)), v)
            for i, v in enumerate((10, 20, 30, 40))]


def _checks_nested(img, res) -> List[Check]:
    return [("outer(5)", _s16(word_at(res, img.symbols["out"].value)), 34)]


def _checks_funcptr(img, res) -> List[Check]:
    return [("addfn", _s16(word_at(res, img.symbols["r1"].value)), 42),
            ("subfn", _s16(word_at(res, img.symbols["r2"].value)), 42)]


def _checks_matmul(img, res) -> List[Check]:
    a = img.symbols["A"].value
    b = img.symbols["B"].value
    c = img.symbols["C"].value
    def w(addr: int) -> int:
        return img.data[addr] | (img.data[addr + 1] << 8)
    AA = [[w(a + 2 * (i * 8 + j)) for j in range(8)] for i in range(8)]
    BB = [[w(b + 2 * (i * 8 + j)) for j in range(8)] for i in range(8)]
    out: List[Check] = []
    for i in range(8):
        for j in range(8):
            want = sum(AA[i][k] * BB[k][j] for k in range(8)) & 0xFFFF
            out.append(("C[%d][%d]" % (i, j), _s16(word_at(res, c + 2 * (i * 8 + j))), _s16(want)))
    return out


@dataclass(frozen=True)
class Program:
    name: str
    source: str
    description: str
    checks: Callable[[object, Result], List[Check]]


PROGRAMS: List[Program] = [
    Program("fibonacci", "programs/fibonacci.s", "iterative Fibonacci fib(16)", _checks_fib),
    Program("factorial_rec", "programs/factorial_rec.s", "recursive 8! with stack and LR", _checks_fact),
    Program("bubble_sort", "programs/bubble_sort.s", "bubble sort of 16 signed values", _checks_bubble),
    Program("crc16", "programs/crc16.s", "bitwise CRC-16/XMODEM", _checks_crc),
    Program("memory_ops", "programs/memory_ops.s", "memcpy / strlen / strcmp", _checks_memory),
    Program("muldiv", "programs/muldiv.s", "software multiply/divide/modulo edge cases", _checks_muldiv),
    Program("matmul", "programs/matmul.s", "8x8 integer matrix multiply", _checks_matmul),
    Program("hello", "programs/hello.s", "UART polling hello", _checks_hello),
    Program("switch", "programs/switch.s", "jump-table switch via JALR", _checks_switch),
    Program("nested_calls", "programs/nested_calls.s", "nested calls and callee-saved registers", _checks_nested),
    Program("funcptr", "programs/funcptr.s", "function pointers via JALR", _checks_funcptr),
]


def build(program: Program):
    sources = []
    for f in RUNTIME + [program.source]:
        path = ROOT / f
        sources.append((f, path.read_text(encoding="utf-8")))
    return assembler.assemble_sources(sources)


def run(program: Program):
    img = build(program)
    res = run_pipeline(img.instr_words, Config(ram_init=img.data))
    return img, res


def verify(program: Program, img, res) -> List[Check]:
    got = program.checks(img, res)
    bad = [(name, g, w) for name, g, w in got if g != w]
    if bad:
        msg = "; ".join("%s: got %r want %r" % (n, g, w) for n, g, w in bad[:8])
        raise AssertionError("%s: %s" % (program.name, msg))
    return got
