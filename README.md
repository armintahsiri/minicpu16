# MiniCPU-16

**A 16-bit, 5-stage pipelined CPU in VHDL, specified before it is built, and verified
against a Python golden model by retirement-trace comparison.**

![CI](https://github.com/armintahsiri/minicpu16/actions/workflows/test.yml/badge.svg)

Status: **Phase 5B complete** - a 5-stage pipelined VHDL CPU with a full
forwarding network, verified **cycle-exact** against the golden timing layer,
not just architecturally.  The ISA is frozen (<code>isa-v1-frozen</code>), the
retirement trace *including the retire-cycle column and the per-cycle stage
occupancy* matches the golden model, and 30/30 injected RTL bugs are detected.
The default configuration is `FORWARDING=true`; the stall-only
`FORWARDING=false` build remains as an architectural regression.

Cycle exactness is asserted by 1000 forwarding fuzz programs on NVC plus 150 on
GHDL, the full differential suite (136 case-runs), and every benchmark
satisfying `cycles = N + 4 + S + 2R` exactly.  There is still **no hardware
test** and CI has **not been executed on GitHub**.

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

## Implemented microarchitecture (Phase 5B)

    IF -> ID -> EX -> MEM -> WB
     |     |     |
     |     |     +-- branch / JAL / JALR resolved in EX
     |     |         taken => flush IF and ID (2-cycle penalty)
     |     +-------- decode, register read with WB->ID write-through, stall check
     +-------------- PC, synchronous instruction BRAM (output register = IF/ID instr)

    Harvard memories: instruction BRAM (32-bit words, INSTR_WORDS)
                      data BRAM (two byte banks, RAM_BYTES)
    MMIO page at 0xFF00: UART_TX/STATUS, GPIO_OUT/IN, TIMER, reserved

Phase 5B uses **full forwarding** by default (`FORWARDING=true`): the ID/EX
operands are taken from EX/MEM (youngest) or MEM/WB, EX/MEM wins, a load in
EX/MEM is never forwarded but instead stalls its distance-1 consumer, R0 is
never forwarded, and the mux is gated by the stage valid bits so bubbles and
flushed instructions never source an operand.  Only load-use at distance 1
stalls (one cycle).  With `FORWARDING=false` the earlier stall-only rule is
kept: an ID instruction stalls while a producer of an actual source is in EX or
MEM, WB being covered by write-through.

The cycles are exactly the contract's: `N + 4 + S + 2R`, where `N` retires
(including HALT), `S` is load-use stalls, and `R` is taken conditional
branches + JAL + JALR.  HALT drains and retires, younger instructions are
flushed, and reset changes no architectural state except the specified
PC/GPIO_OUT/TIMER/UART state.  Full equations, cycle tables and the memory
interface timing are in <code>docs/RTL_ARCH.md</code>.

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
    make rtl-leaf        # self-checking leaf testbenches, NVC and GHDL
    make rtl-suite       # reference traces + benchmarks + directed, both sims
    make rtl-fuzz        # 1000 forwarding + curated programs, NVC and GHDL
    make fwd-regression  # stall-only architectural regression (FORWARDING=false)
    make mutation        # 30 injected RTL bugs must be caught
    make synth-smoke     # Yosys/GHDL block-RAM inference and Fmax estimate

Run one RTL program differentially:

    python3 tools/rtl_run.py tests/directed/a_store_load_d1.asm \
        --name a_store_load_d1 --sim nvc \
        --expected tests/directed/a_store_load_d1.expected

    python3 tools/rtl_run.py programs/bubble_sort.s --runtime --sim ghdl

Comparisons are **cycle-exact** by default in Phase 5B: the retire-cycle
column and the per-cycle pipeview are compared as well as the architectural
retirement records and final state.  Use <code>--arch-only</code> for the
architectural (5A-style) comparison, which the `FORWARDING=false`
regression uses.

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
These are the **contract** numbers from the golden timing layer, and in Phase 5B
the RTL matches them **exactly** with `FORWARDING=true` (verified, not
asserted).  The stall-only `FORWARDING=false` configuration is intentionally
slower and is compared architecturally only; for example its bubble_sort takes
2647 cycles instead of 1950, with 817 stall cycles instead of 120.

The composition is documented in <code>docs/OPTIMIZATION_DATA.md</code>; in
short, taken control transfers dominate the overhead (99.9% of matmul's) and
load-use stalls matter only in the load-heavy integer programs.

## Tests actually run

<code>make clean && make test</code> currently reports (Phase 5B):

* consistency: 490 checks, PASS; negative mutation self-test PASS
* assembler: 20 test methods / 830 hand-computed checks
* golden model / timing layer: 37 test methods / 22,204 checks
* trace diff: 8 test methods / 17 checks
* programs: 6 test methods / 290 checks
* generator: 5 test methods / 80 checks (200 programs, all opcodes, all hazard patterns)
* VHDL: NVC and GHDL both analyse the constants package and a generated program package
* leaf RTL: **26 testbench runs / 8,023,444 checks across NVC and GHDL**, 0 failures
* differential suite (full `make rtl-test`): **140 case-runs, 0 failed**,
  cycle-exact; the quick subset in `make test` is 46 case-runs
* quick fuzz in `make test`: 52 case-runs (40 NVC + 8 GHDL subset + curated),
  0 failed, cycle-exact
* forwarding fuzz (<code>make rtl-fuzz</code>): **1000 programs on NVC, 150 on
  GHDL, 0 mismatches**, cycle-exact, every reachable coverage counter non-zero
* stall-only regression (<code>make fwd-regression</code>): 0 failed, architecture only
* mutation: **30/30 detected on NVC, 30/30 on GHDL, 27/30 (90%) by the
  fuzz-only corpus** (threshold >= 90%)

Because forwarding is now the default, the cycle-exact comparison includes the
retire-cycle column and the per-cycle stage occupancy.

### Synthesis smoke (<code>make synth-smoke</code>, estimate only)

Yosys 0.70 + ghdl-yosys-plugin (GHDL 6.0.0) and <code>synth_ice40</code>, both
`FORWARDING` configurations, a non-constant 2048-word image (the default image
is one HALT word and constant-folds to 0 BRAM):

* **24 `SB_RAM40_4K`** (16 instruction + 8 data) in **both** configurations
* `FORWARDING=false`: 1257 LUT4, 610 flip-flops
* `FORWARDING=true`: 1369 LUT4, 618 flip-flops
* HX8K (ct256, 50 MHz, seeds 1/2/3): nofwd **55.59 / 63.92 / 62.22 MHz** (PASS);
  fwd **43.33 / 43.38 / 44.54 MHz** (FAIL at 50)
* UP5K (sg48, 50 MHz, seeds 1/2/3): nofwd **25.34 / 24.94 / 25.54 MHz** (FAIL);
  fwd **18.07 / 17.78 / 18.74 MHz** (FAIL)

These are placement/routing estimates from a flat, untuned flow, not hardware
measurements.  On this flow forwarding is *slower*, not faster; that is expected
for an unpipelined flat design and no timing fix was attempted (Fmax tuning is a
Phase 5B non-goal).  The 5A baseline was HX8K 59.22 MHz, UP5K 25.58 MHz, 1263
LUT4.

## Honest limitations

* **Cycle exactness is verified at the RTL/model level, not on hardware.**
  `FORWARDING=true` matches the timing layer including retire cycles and stage
  occupancy, but nothing has run on an FPGA and the Fmax numbers are untuned
  estimates.
* **No hardware test.**  Nothing has run on an FPGA.  The synthesis smoke test is
  an inference/count estimate, not a placed-and-routed hardware result; Fmax is an
  estimate with a fixed seed.
* CI is written and updated for Phase 5A, but it has **not been executed on
  GitHub**.  The workflow installs NVC and the distro GHDL; package versions and
  action behaviour are unverified.  Mutation and full fuzz stay local.
* The peripheral timing (TIMER, UART busy window, GPIO_IN synchroniser) was
  made normative in contract section 7 during Phase 5B and the RTL was corrected
  (finding F25); those programs are now compared cycle-exactly too.
* No MUL/DIV in the base ISA; software routines exist but multiply, divide and
  modulo have not yet been exercised through a C compiler.  In the benchmark set
  only factorial_rec (38.8% of cycles) and muldiv (69.1%) use them
  (<code>docs/OPTIMIZATION_DATA.md</code>).
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
* **Phase 5B (done)** - full forwarding and the one-cycle load-use rule; RTL
  cycle-exact against <code>docs/MICROARCH_CONTRACT.md</code> including the
  retire-cycle column and stage occupancy; 1000+150 cycle-exact fuzz, 30/30
  mutation detection, peripheral timing made normative and corrected, Fmax/CPI
  measured honestly.
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