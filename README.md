# MiniCPU-16

**A 16-bit, 5-stage pipelined CPU in VHDL, specified before it is built, and verified
against a Python golden model by retirement-trace comparison.**

![CI](https://github.com/armintahsiri/minicpu16/actions/workflows/test.yml/badge.svg)

Status: **Phase 5A complete** - a 5-stage pipelined VHDL CPU with stall-only
hazard handling, verified architecturally against the golden model by
differential retirement traces and fuzzing.  The ISA is frozen
(<code>isa-v1-frozen</code>), the retirement trace is identical to the golden
model (retire cycles ignored), and 12/12 injected RTL bugs are detected.

**Phase 5A does not claim cycle-exactness.**  Forwarding is structurally present
but `FORWARDING=true` is unverified; 5A stalls instead and is intentionally
slower than `docs/MICROARCH_CONTRACT.md`.  There is no hardware test and CI has
not been executed on GitHub.

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
| <code>rtl/minicpu16_pkg.vhd</code> | constants, opcodes, control record, image types, coverage indices |
| <code>rtl/*.vhd</code> | register file, ALU, shifter, branch unit, decoder, hazard/forwarding units, imem, dmem, MMIO, core |
| <code>rtl/tb/</code> | CPU trace testbench plus one self-checking testbench per leaf module |
| <code>docs/RTL_ARCH.md</code> | Phase 5A block diagram, pipeline fields, memory timing, stall/flush equations, cycle tables |
| <code>docs/RTL_FINDINGS.md</code> | spec audits and RTL bugs found/fixed |
| <code>tools/rtl_run.py</code> | assemble -> golden -> RTL -> trace diff for one program |
| <code>tools/rtl_suite.py</code> | reference traces, benchmarks and directed differential suite |
| <code>tools/rtl_fuzz.py</code> | seeded differential fuzzer with coverage table |
| <code>tools/mutation_check.py</code> | 12-mutation verification check |
| <code>tools/synth_smoke.py</code> | Yosys/GHDL block-RAM inference smoke test |
| <code>tools/assembler.py</code> | assembler with VHDL/hex/mem/listing outputs |
| <code>tools/disassembler.py</code> | canonical disassembler and round-trip decoder |
| <code>tools/isa_sim.py</code> | architectural and cycle-accurate timing golden model |
| <code>tools/trace_diff.py</code> | first-divergence trace comparison |
| <code>tools/check_consistency.py</code> | cross-checks docs, VHDL, assembler, model; negative self-test |
| <code>tools/random_programs.py</code> | deterministic program generator with hazard modes |
| <code>tools/bench.py</code> | benchmark runner / docs generator |
| <code>programs/</code> | benchmarks and <code>runtime/</code> (crt0, soft arithmetic, strings) |
| <code>tests/reference_traces/</code> | five asm + trace pairs for the first Phase 5 vectors |

## Implemented microarchitecture (Phase 5A)

    IF -> ID -> EX -> MEM -> WB
     |     |     |
     |     |     +-- branch / JAL / JALR resolved in EX
     |     |         taken => flush IF and ID (2-cycle penalty)
     |     +-------- decode, register read with WB->ID write-through, stall check
     +-------------- PC, synchronous instruction BRAM (output register = IF/ID instr)

    Harvard memories: instruction BRAM (32-bit words, INSTR_WORDS)
                      data BRAM (two byte banks, RAM_BYTES)
    MMIO page at 0xFF00: UART_TX/STATUS, GPIO_OUT/IN, TIMER, reserved

Phase 5A uses **stall-only hazards**: an ID instruction stalls while a producer of
one of its actual sources is in EX or MEM (rd != R0); WB is covered by the
write-through.  This is two stalls at distance 1, one at distance 2, none at
distance 3.  The forwarding muxes and `forwarding_unit` are structurally present
and drive `FORWARDING=true` in 5B, but that mode is **unverified**.  HALT drains
and retires, younger instructions are flushed, and reset changes no architectural
state except the specified PC/GPIO_OUT/TIMER/UART state.  Full equations, cycle
tables and the memory interface timing are in <code>docs/RTL_ARCH.md</code>.

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

    make consistency     # docs vs VHDL vs assembler vs golden model + self-test
    make vhdl-check      # NVC and GHDL, VHDL-2008, constants + generated package
    make asmtest         # assembler encodings/errors and disassembler round-trip
    make simtest         # ISA, timing contract and trace-diff tests
    make programstest    # all benchmark programs and their expected results
    make gentest         # 200 random programs + reference traces
    make bench           # print table and regenerate docs/BENCHMARKS.md
    make rtl-leaf        # 11 self-checking leaf testbenches, NVC and GHDL
    make rtl-suite       # reference traces + benchmarks + directed, both sims
    make rtl-fuzz        # 300 random + 12 curated programs; 50 on GHDL
    make mutation        # 12 injected RTL bugs must be caught
    make synth-smoke     # Yosys/GHDL block-RAM inference and Fmax estimate

Run one RTL program differentially:

    python3 tools/rtl_run.py tests/directed/a_store_load_d1.asm \
        --name a_store_load_d1 --sim nvc \
        --expected tests/directed/a_store_load_d1.expected

    python3 tools/rtl_run.py programs/bubble_sort.s --runtime --sim ghdl

The default comparison ignores the retire-cycle column (5A).  Use
<code>--check-cycles</code> only for Phase 5B.

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

CPI above 1 is the pipeline fill/drain, taken-branch penalty and load-use stalls.
These are the **contract** numbers from the golden timing layer.  The 5A RTL is
intentionally slower (stall-only) and its cycle counts are **not** compared; 5B
owns cycle exactness.  For example, 5A bubble_sort takes 2647 cycles instead of
1950, with 817 stall cycles instead of 120.

## Tests actually run

<code>make clean && make test</code> currently reports:

* consistency: 490 checks, PASS; negative mutation self-test PASS
* assembler: 20 test methods / 830 hand-computed checks
* golden model: 37 test methods / 22,204 checks
* trace diff: 8 test methods / 17 checks
* programs: 6 test methods / 290 checks
* generator: 5 test methods / 80 checks (200 programs, all opcodes, all hazard patterns)
* VHDL: NVC and GHDL both analyse the constants package and a generated program package
* leaf RTL: 11 testbenches / **8,023,388 checks across NVC and GHDL** (4,011,694 per simulator), 0 failures
* differential suite: **61 case-runs, 0 failed** (NVC 43 = 4 reference traces +
  10 benchmarks + 29 directed; GHDL 18); reference traces 01-04 match
  architecturally, 05_mmio is excluded as cycle_sensitive
* full fuzz (<code>make rtl-fuzz</code>): **312 programs on NVC, 50 on GHDL,
  0 mismatches**, every coverage counter non-zero
* mutation: **12/12 injected bugs detected**

Cycle counts in 5A are intentionally higher than the contract; only architectural
retirement records and final state are compared.

### Synthesis smoke (<code>make synth-smoke</code>, estimate only)

Yosys 0.70 + ghdl-yosys-plugin (GHDL 6.0.0) and <code>synth_ice40</code>, default
generics except a non-constant 2048-word image (the default image is one HALT word
and constant-folds to 0 BRAM):

* **24 `SB_RAM40_4K`** (16 instruction + 8 data), 1263 LUT4, 611 flip-flops
* HX8K (ct256, seed 1, target 50 MHz): **59.22 MHz**, PASS
* UP5K (sg48, seed 1, target 50 MHz): **25.58 MHz**, FAIL at 50
* critical paths: HX8K instruction-BRAM output -> decode/control; UP5K `b_ex` ->
  `ex_mem_result`

These are placement/routing estimates, not hardware measurements; the design was
not tuned for Fmax in Phase 5A.

## Honest limitations

* **No cycle-exactness claim.**  Phase 5A is stall-only; its cycles are not the
  contract's cycles.  <code>FORWARDING=true</code> is structurally present but
  **unverified**.  That is Phase 5B's job.
* **No hardware test.**  Nothing has run on an FPGA.  The synthesis smoke test is
  an inference/count estimate, not a placed-and-routed hardware result; Fmax is an
  estimate with a fixed seed.
* CI is written and updated for Phase 5A, but it has **not been executed on
  GitHub**.  The workflow installs NVC and the distro GHDL; package versions and
  action behaviour are unverified.  Mutation and full fuzz stay local.
* MMIO programs that observe cycle timing (TIMER, UART_STATUS polling, GPIO_IN)
  are excluded from the 5A differential suite; 05_mmio and hello are the named
  exclusions.  GPIO_IN has a two-flop synchroniser, so it is not the golden
  model's immediate value.
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

* **Phase 5A (done)** - 5-stage pipelined RTL, synchronous instruction/data
  memories, MMIO, HALT drain, reset gating, stall-only hazard handling; leaf
  testbenches, directed tests, reference traces, benchmarks, 312-program fuzz and
  12/12 mutation detection.
* **Phase 5B** - enable forwarding, verify the one-cycle load-use rule, and make
  the RTL cycle-exact against <code>docs/MICROARCH_CONTRACT.md</code>; then the
  reference-trace retire cycles and benchmark cycle counts become assertions.
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
