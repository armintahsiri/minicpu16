# MiniCPU-16

**A 16-bit, 5-stage pipelined CPU in VHDL, specified before it is built, and verified
against a Python golden model by retirement-trace comparison.**

![CI](https://github.com/armintahsiri/minicpu16/actions/workflows/test.yml/badge.svg)

Status: **Phase 4 complete** - ISA frozen, toolchain and golden model working.
There is **no CPU RTL yet** except the constants-only package
<code>rtl/minicpu16_pkg.vhd</code>.  The pipelined CPU is Phase 5.

## Goals

* A small, readable 16-bit CPU with a fixed 32-bit instruction word and a
  word-granular PC.
* An ISA designed for a 5-stage pipeline, synchronous Block RAM and a small C
  compiler where <code>int</code> is 16 bits.
* <code>docs/ISA.md</code> as the single source of truth, written before RTL.
* A Python golden model with a stable retirement trace; the Phase 5 RTL must match
  that trace cycle for cycle.
* Open-source FPGA synthesis and, eventually, C compiled by a retargeted compiler.

Inherited method from MiniCPU-8: hand-computed expectations, deterministic seeds,
a consistency checker that fails loudly, and every claim backed by a test that was
actually run.

## Repository layout

| Path | Purpose |
|---|---|
| <code>docs/ISA.md</code> | frozen ISA v1.0: formats, opcodes, pseudos, memory map, encodings, compare table |
| <code>docs/ABI.md</code> | data model, registers, calls, stack, crt0, runtime routine names |
| <code>docs/MICROARCH_CONTRACT.md</code> | the Phase 5 pipeline contract: stages, forwarding, stalls, flushes, exact cycle definition |
| <code>docs/DECISIONS.md</code> | every non-trivial decision, alternatives, ISA review and proposed deviations |
| <code>docs/TRACE_FORMAT.md</code> | the retirement-trace file format v1 |
| <code>docs/BENCHMARKS.md</code> | benchmark table generated from real runs |
| <code>docs/COMPILER_SPIKE.md</code> | lcc/SDCC/chibicc licensing and backend-risk findings |
| <code>docs/ENVIRONMENT.md</code> | tool versions used |
| <code>rtl/minicpu16_pkg.vhd</code> | constants/types only; analyses with NVC and GHDL |
| <code>tools/assembler.py</code> | assembler with VHDL/hex/mem/listing outputs |
| <code>tools/disassembler.py</code> | canonical disassembler and round-trip decoder |
| <code>tools/isa_sim.py</code> | architectural and cycle-accurate timing golden model |
| <code>tools/trace_diff.py</code> | first-divergence trace comparison |
| <code>tools/check_consistency.py</code> | cross-checks docs, VHDL, assembler, model; negative self-test |
| <code>tools/random_programs.py</code> | deterministic program generator with hazard modes |
| <code>tools/bench.py</code> | benchmark runner / docs generator |
| <code>programs/</code> | benchmarks and <code>runtime/</code> (crt0, soft arithmetic, strings) |
| <code>tests/reference_traces/</code> | five asm + trace pairs for the first Phase 5 vectors |

## Planned microarchitecture (Phase 5)

    IF -> ID -> EX -> MEM -> WB
     |     |     |
     |     |     +-- branch / JAL / JALR resolved here
     |     |         taken => flush IF and ID (2-cycle penalty)
     |     +-------- decode, register read, load-use hazard check
     +-------------- PC, synchronous instruction BRAM

    Harvard memories: instruction BRAM (32-bit words, INSTR_WORDS)
                      data BRAM (16-bit words with byte enables, RAM_BYTES)
    MMIO page at 0xFF00: UART, GPIO, TIMER

Baseline contract: static not-taken prediction, full forwarding
(EX/MEM->EX, MEM/WB->EX, WB->ID), one-cycle load-use stall including a store that
uses the loaded value as data, HALT drains and retires, reset changes no
architectural state except PC.  Exact rules and hand-computed cycle examples are in
<code>docs/MICROARCH_CONTRACT.md</code>.

## ISA summary

* 16-bit data, 16 registers (R0 hardwired zero), 32-bit fixed instructions,
  16-bit word-granular PC, byte addresses for data.
* Formats: opcode[31:24], rd[23:20], rs1[19:16], then rs2[15:12] for R-type or
  imm16[15:0] for I-type.  Stores/branches put the second register in [23:20].
* ALU: ADD SUB AND OR XOR SLL SRL SRA SLT SLTU, plus ADDI ANDI ORI XORI SLLI SRLI
  SRAI SLTI SLTIU.
* Memory: LB LBU LW SB SW; little-endian; misaligned LW/SW ignore address bit 0.
* Control: BEQ BNE BLT BGE BLTU BGEU JAL JALR; no flags; branch target is
  PC + 1 + sext(imm16).
* HALT is the all-zero instruction, so zero-filled instruction memory halts.
  NOP is ADD R0,R0,R0.  Unassigned opcodes are NOP.
* 0x21..0x2F are reserved for a future MUL/DIV extension.
* Memory map: RAM 0x0000..RAM_BYTES-1 (default 4096); unmapped reads 0 / writes
  ignored; MMIO 0xFF00..0xFFFF (UART_TX, UART_STATUS, GPIO_OUT, GPIO_IN, TIMER,
  reserved).
* Every 16-bit constant and offset fits in one instruction; no LUI/AUIPC.

Pseudo-instructions include LI, LA, MV, NOT, NEG, NOP, J, CALL, RET, BGT/BLE/
BGTU/BLEU, BEQZ/BNEZ/BLTZ/BGEZ/BGTZ/BLEZ, SUBI, SEQZ/SNEZ/SLTZ/SGTZ.  Full
expansion table: <code>docs/ISA.md</code>.

## ABI summary

| Register | Alias | Role |
|---|---|---|
| R0 | zero | constant 0; writes ignored |
| R1-R4 | a0-a3 | arguments and return value (R1) |
| R5-R8 | t0-t3 | caller-saved temporaries |
| R9-R13 | s0-s4 | callee-saved |
| R14 | sp | full-descending stack pointer, 2-byte aligned |
| R15 | lr/ra | link register (return address) |

Arguments 5+ are on the stack at the caller's SP.  <code>int</code> and pointers
are 16 bits, <code>char</code> is unsigned 8 bits, no <code>long</code>/float/
varargs/struct-by-value in v1.  crt0 sets SP to <code>RAM_BYTES</code>, zeroes
<code>.bss</code>, calls <code>main(0, NULL)</code>, and halts.  Function pointers
are **instruction indices**; data pointers are **byte addresses**.

## Quick start

Run everything from scratch:

    make clean && make test

Individual checks:

    make consistency     # docs vs VHDL vs assembler vs golden model + mutation test
    make vhdl-check      # NVC and GHDL, VHDL-2008, constants + generated package
    make asmtest         # assembler encodings/errors and disassembler round-trip
    make simtest         # ISA, timing contract and trace-diff tests
    make programstest    # all benchmark programs and their expected results
    make gentest         # 200 random programs + reference traces
    make bench           # print table and regenerate docs/BENCHMARKS.md

Assemble, run and trace a program:

    python3 tools/assembler.py \
        programs/runtime/crt0.s programs/runtime/arith.s programs/runtime/string.s \
        programs/hello.s --vhdl build/hello_pkg.vhd --hex build/hello \
        --listing build/hello.lst

    python3 tools/isa_sim.py build/hello.hex --data build/hello.data.hex \
        --trace build/hello.trace --json build/hello.json

    python3 tools/disassembler.py --hex build/hello.hex
    python3 tools/trace_diff.py before.trace after.trace

The golden model's timing layer prints a JSON final state and a line-oriented
retirement trace.  Use <code>--arch</code> for the architectural layer (one cycle
per instruction; only the timing layer is the cycle contract).

## Benchmark baselines (real runs)

From <code>docs/BENCHMARKS.md</code>; default 4 KB RAM, 2048 instruction words,
10 cycles/UART byte, timing layer, crt0 and runtime included:

| Program | Instr | Cycles | CPI | ALU % | Load % | Store % | Branch % | Jump % | Taken br % | Stalls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fibonacci | 112 | 156 | 1.393 | 66.1 | 0.0 | 0.9 | 16.1 | 16.1 | 11.1 | 0 |
| factorial_rec | 287 | 448 | 1.561 | 51.2 | 5.2 | 5.6 | 19.5 | 18.1 | 41.1 | 7 |
| bubble_sort | 1278 | 1950 | 1.526 | 35.0 | 19.2 | 8.1 | 21.3 | 16.3 | 24.3 | 120 |
| crc16 | 647 | 893 | 1.380 | 72.5 | 1.5 | 0.2 | 24.0 | 1.7 | 67.7 | 10 |
| memory_ops | 572 | 857 | 1.498 | 39.3 | 21.7 | 6.3 | 21.7 | 10.8 | 28.2 | 87 |
| muldiv | 1186 | 1626 | 1.371 | 68.0 | 1.0 | 2.1 | 22.7 | 6.1 | 54.3 | 0 |
| matmul | 12950 | 18114 | 1.399 | 55.2 | 8.0 | 1.5 | 19.9 | 15.4 | 22.7 | 0 |
| hello | 257 | 400 | 1.556 | 39.3 | 22.2 | 7.4 | 22.6 | 8.2 | 34.5 | 57 |
| switch | 58 | 96 | 1.655 | 55.2 | 8.6 | 8.6 | 1.7 | 24.1 | 100.0 | 4 |
| nested_calls | 55 | 85 | 1.545 | 43.6 | 14.5 | 16.4 | 1.8 | 21.8 | 100.0 | 0 |
| funcptr | 30 | 48 | 1.600 | 60.0 | 3.3 | 10.0 | 3.3 | 20.0 | 100.0 | 0 |

CPI above 1 is the pipeline fill/drain, taken-branch penalty and load-use stalls;
Phase 5 must reproduce these numbers exactly before any optimization.

## Tests actually run

<code>make clean && make test</code> currently reports:

* consistency: 490 checks, PASS; negative mutation self-test PASS
* assembler: 20 test methods / 830 hand-computed checks
* golden model: 37 test methods / 22,204 checks
* trace diff: 8 test methods / 17 checks
* programs: 6 test methods / 290 checks
* generator: 5 test methods / 80 checks (200 programs, all opcodes, all hazard patterns)
* VHDL: NVC and GHDL both analyse the constants package and a generated program package
* 5 reference traces reproduce byte-for-byte

## Honest limitations

* **No CPU RTL yet.**  The timing layer is a contract, not silicon.  Nothing in
  this phase has been synthesized, placed, routed, or measured for Fmax.
* The timing model has not been checked against real hardware; that is the main
  Phase 5 activity.
* CI is written but **not run** here (no GitHub remote yet).  The workflow installs
  NVC and the distro GHDL; package versions and action behaviour are unverified.
* No MUL/DIV in the base ISA; software routines exist but multiply, divide and
  modulo have not yet been exercised through a C compiler.
* No traps, interrupts, exceptions, memory protection, caches, or atomics.
  Misaligned LW/SW silently align down.
* Reset does not reload <code>.data</code> and does not clear registers; crt0
  zeroes <code>.bss</code>.
* No varargs, <code>long</code>, <code>float</code>, <code>double</code>,
  struct-by-value, or bitfields in ABI v1.
* lcc is not MIT-compatible; see <code>docs/COMPILER_SPIKE.md</code>.
* The architectural layer's UART/TIMER behaviour is cycle-approximate; only the
  timing layer is the contract.

## Roadmap

* **Phase 5** - 5-stage pipelined RTL, BRAM memories, MMIO, HALT drain, reset
  gating; retirement-trace differential fuzzing against the golden model; the five
  reference traces are the first vectors.
* **Phase 6** - FPGA synthesis and timing (Yosys/nextpnr), UART/GPIO/TIMER on real
  hardware, CPI/Fmax on these benchmarks, MUL evaluation, BTFN evaluation.
* **Phase 7** - a C-subset compiler (Python, MIT, standard library only) targeting
  this ISA/ABI; software arithmetic and libc subset; compiler differential tests.

## License

MIT, Copyright 2026 Armin Tahsiri.  See <code>LICENSE</code>.

## Reference

Successor to [MiniCPU-8](https://github.com/armintahsiri/minicpu8-vhdl).  The
MiniCPU-8 tree was used only as a read-only reference; this repository is
self-contained.
