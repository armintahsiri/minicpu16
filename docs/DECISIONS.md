# MiniCPU-16 Design Decisions

This is a decision log, not a second specification. Where it conflicts with
`docs/ISA.md`, the ISA wins; the conflict is a bug.

Status legend: **LOCKED** = given in the project brief and implemented as written;
**CHOSEN** = a free choice made here; **DEVIATION** = differs from a literal reading
of the brief and is flagged in the final report.

---

## 1. Decision table

| # | Area | Decision | Alternatives considered | Reason |
|---|---|---|---|---|
| D1 | Instruction format | Flat 32-bit word: opcode[31:24], rd[23:20], rs1[19:16], then rs2[15:12]+zero or imm16[15:0]; stores/branches put the second register in [23:20]. No funct field. | RISC-V-style funct3/funct7; 16-bit instructions; variable length. | One opcode byte decodes the whole operation; the decoder is a case statement over 8 bits. Field positions are identical for every format, so the register file addresses are wired once. Immediates are 16 bits, so no LUI/AUIPC and no instruction pairing. |
| D2 | Opcode assignment | 0x00 HALT, 0x01-0x0A R-ALU, 0x0B-0x13 I-ALU, 0x14-0x16 loads, 0x17-0x18 stores, 0x19-0x1E branches, 0x1F JAL, 0x20 JALR, 0x21-0x2F reserved, rest NOP. | Random assignment; RISC-V opcode values. | Contiguous groups make the Phase 5 control decoder and the consistency checker simple; 0x21..0x2F leaves room for MUL/MULH/DIV/DIVU/REM/REMU. |
| D3 | HALT encoding | All-zero 32-bit word is HALT. | A dedicated nonzero opcode; NOP=0. | Zero-filled or uninitialised BRAM halts safely instead of executing garbage. A reset-and-run FPGA image with no program is safe. |
| D4 | NOP encoding | `ADD R0,R0,R0` = 0x01000000. | 0x00000000 (conflicts with HALT); a dedicated NOP opcode. | Uses an existing instruction that provably writes nothing (R0) and touches no memory. Keeps HALT=0. |
| D5 | Flags | No flags register; comparisons are SLT/SLTU and fused branches. | Carry/zero/negative/overflow flags like MiniCPU-8. | Flags create a second architectural state to reset, forward and trace, and make branch dependencies implicit. Value comparisons are simpler to verify and to compile. |
| D6 | Immediates | Every immediate is exactly 16 bits and needs no extension for data operations. SLTI/SLTIU choose signed/unsigned interpretation. Branch/JAL offsets are signed 16-bit; JALR immediate is signed and absolute. | RISC-V-style 12-bit sign-extended immediates. | A 16-bit machine with 16-bit data can encode any constant, address or offset in one instruction. Explicitly saying "no extension" removes the most common source of spec ambiguity. |
| D7 | PC unit | PC counts instructions (16-bit index), wraps mod 2^16. Branch target = PC+1+sext(imm16). | Byte-granular PC. | The 32-bit instruction word is fetched as one unit; a byte PC would waste bits and complicate BRAM addressing. Link values being instruction indices is documented in the ABI. |
| D8 | Branch resolution | Resolve in EX, predict not taken, flush IF and ID on taken (2-cycle penalty). | Resolve in ID (more forwarding/stall cases); predict taken; delay slots. | Matches the brief and keeps the EX ALU shared with the branch comparator. Two squashed instructions are easy to invalidate with valid bits and are visible in the trace. |
| D9 | JALR rd==rs1 | Target uses the old rs1; link value is written to rd. | Undefined; forbid rd==rs1 in the assembler. | A defined result costs one bypass in the target adder and avoids an assembler-only restriction that a compiler could violate. The RTL must not create a combinational loop. |
| D10 | Misaligned loads/stores | LW/SW ignore address bit 0 (`addr & 0xFFFE`); no trap. | Trap; split into two byte accesses; forbid. | There are no traps in v1, and a byte-mask memory is more expensive than a word RAM. The rule is deterministic and documented; the compiler must align. |
| D11 | Memory map | RAM 0x0000..RAM_BYTES-1 (default 0x1000, max 0xFF00); unmapped RAM_BYTES..0xFEFF reads 0/writes ignored; MMIO 0xFF00..0xFFFF. | MMIO at 0x0000; a single 64 KB RAM; memory-mapped ROM. | Keeps the RAM generic and small for BRAM inference, gives a fixed MMIO page, and leaves a documented unmapped hole. |
| D12 | MMIO byte access | Byte accesses ignore address bit 0 and use the low byte of the selected register; SB preserves the high byte (except UART_TX, where any SB transmits). | Byte accesses unsupported; high/low byte selection; separate byte registers. | A 16-bit register file in the MMIO block has no natural byte lanes. "Low byte only" is one rule for every register and is enough for UART/GPIO/status. |
| D13 | UART write when busy | The byte is dropped; software polls UART_STATUS. | Queue one byte; stall the CPU; overwrite. | A one-entry queue is still a queue with its own state; dropping is the simplest deterministic hardware and teaches polling. The status bit makes the race avoidable. |
| D14 | Timer | TIMER reads the pipeline cycle number mod 2^16; reset to 0. | Instruction counter; a separate prescaler. | The timing model already owns the cycle number; using it makes timer-based tests cycle-exact against RTL. |
| D15 | Fetch beyond image | Fetch beyond program length or INSTR_WORDS returns HALT. | Return 0 (which is HALT anyway); trap. | Explicitly documented and equivalent to zero-filled memory. Programs can end by running off the end rather than an explicit HALT. |
| D16 | Unassigned opcodes | Execute as NOP (retire, no state change). 0x21..0x2F reserved for a future MUL/DIV extension. | Trap; illegal-instruction exception. | No traps in v1. NOP is deterministic and lets a future extension reserve encoding space without breaking old images. |
| D17 | Data layout | `.data` starts at byte 0; `.bss` follows; stack starts at RAM_BYTES and grows down. | Data at 0x0100 (null guard); separate .rodata; linker script. | No linker and no relocations in v1. Low data addresses keep the 16-bit address space simple; the missing null guard is documented as a limitation. |
| D18 | Reset | PC forced to 0; registers and RAM preserved; no writes during reset; GPIO_OUT=0; timer=0; UART idle. | Clear all registers; reload .data. | The MiniCPU-8 lesson: reset gating must suppress writes, not add hidden state. FPGA power-on initialises registers to 0; warm reset must not silently change program state. Reloading .data from reset would need a ROM port in hardware. |
| D19 | HALT in pipeline | HALT stops fetch in EX, squashes younger instructions, drains and retires at WB, and is counted. | Stop immediately in IF; treat as a NOP; exclude from instruction count. | A real retired instruction makes the trace uniform and gives a well-defined cycle count. `cycle = HALT WB cycle`. |
| D20 | Load-use stall scope | Stall when a load in EX is followed by any ID instruction that reads its rd, including a store using it as data. | Only stall for ALU consumers; rely on MEM-stage forwarding for stores. | The locked baseline asks for the conservative rule unless forwarding is proven. Store data is consumed in MEM, but the value would have to come from the load's MEM read in the same cycle; stalling is simpler and safe. |
| D21 | Cycle counting | Cycle 1 = first fetch after reset; retire at WB; total = HALT retire cycle. | Count from reset assertion; count only steady-state. | Gives one unambiguous integer for golden model vs RTL. Hand examples are in MICROARCH_CONTRACT.md. |
| D22 | Assembler targets | Branch/JAL operands are absolute **instruction indices** (labels or expressions) in 0..0xFFFF, not raw offsets. The assembler encodes `(target-(PC+1)) mod 2^16` as a signed 16-bit offset; `.` is the current address. | Raw offsets; byte offsets; both. | One meaning for a label in every context. A 16-bit wrapping PC makes every 16-bit target reachable, so the check is on the target range, not on a mathematical difference. Hand-written raw offsets remain possible as `.+N` expressions. Data labels used as branch targets are an error. |
| D23 | Assembler outputs | VHDL package with instruction array and two data byte banks; hex/mem; listing. | A single combined hex; COE/MIF. | Two byte banks map directly to the even/odd byte lanes of a 16-bit BRAM with byte enables, and compile under both simulators. |
| D24 | Trace format | Line-oriented `seq retire pc instr rd value mem...` with `-` for none, header line, stable v1. | JSON lines; binary. | Diffs cleanly, is greppable, and `trace_diff.py` can show context. |
| D25 | Runtime arithmetic | Software `__mulhi3`, `__divhi3`, `__udivhi3`, `__modhi3`, `__umodhi3`; no MUL in base core. | Hardware MUL now. | Locked. The reserved opcodes let a later measurement-driven extension add MUL without changing the rest of the encoding. |
| D26 | Division by zero | Quotient 0xFFFF; remainder = dividend; signed -32768/-1 = 0x8000, remainder 0. | Trap; 0; undefined. | No traps. A fixed value is testable and makes software behavior reproducible; documented in ABI.md. |
| D27 | char signedness | `char` is unsigned; `signed char` is explicit. | Signed char (common on old machines). | Unsigned char avoids sign-extension surprises in `strlen`/`memcpy` and byte I/O; C allows either. |
| D28 | Variadic functions | Not supported in ABI v1. | Count register; pointer to argument block. | See deviation P1. A fixed-arity ABI is enough for Phase 7's C subset and avoids an untested convention. |
| D29 | Struct/long | No struct/union by value, no long/float/double; struct fields by offset, arrays contiguous. | 32-bit long; software float. | Locked scope. Keeps the data model 16-bit and the compiler small. |
| D30 | Heap | No heap in v1; future bump allocator from `__bss_end`. | malloc now. | No libc and no OS; benchmarks use static arrays and the stack. |

---

## 2. ISA review pass (Step 2)

The question: is every C operator and construct needed for `char`/`short`/`int`
expressible in a short instruction sequence?

| C construct | Instruction sequence | Verdict |
|---|---|---|
| `a + b`, `a - b` | ADD / SUB | OK |
| `a * b` | CALL `__mulhi3` (shift/add) | OK, slow |
| `a / b`, `a % b` | CALL `__divhi3` / `__modhi3` (or unsigned) | OK, slow |
| `a & b`, `a | b`, `a ^ b`, `~a` | AND/OR/XOR/XORI 0xFFFF | OK |
| `a << n`, `a >> n` (variable n) | SLL/SRL/SRA with register amount (low 4 bits) | OK |
| `a << 3` (constant) | SLLI/SRLI/SRAI | OK |
| `a == b`, `a != b` | BEQ/BNE; SEQZ/SNEZ for value | OK |
| `a < b`, `a <= b`, signed | SLT/BLT/BGE, BGT/BLE pseudos | OK |
| unsigned comparisons | SLTU/BLTU/BGEU, BGTU/BLEU pseudos | OK |
| `!a`, `a && b`, `a || b` | SEQZ, branches (short-circuit) | OK |
| unary `-a` | SUB rd,R0,rs / NEG | OK |
| 16-bit constant | LI = one ADDI | OK (no LUI) |
| global address | LA = ADDI rd,R0,sym | OK |
| pointer + int, pointer difference | ADD/SUB/ADDI | OK |
| array indexing | SLL by element shift + ADD + LW/SW | OK |
| struct field | ADDI/ADD offset + LW/SW/LB/SB | OK |
| `char` load/store | LB/LBU/SB | OK |
| int -> char | ANDI 0x00FF | OK |
| char -> int | LBU (unsigned) / LB (signed) | OK |
| switch / jump table | table of instruction indices + JALR | OK |
| function pointer | JALR rd,rs,0 | OK |
| call/return | JAL/CALL, JALR/RET | OK |
| stack frame | ADDI SP + SW/LW | OK |
| large frame | multiple ADDI SP adjustments | OK, documented limit |
| varargs | none | **Gap** (P1) |
| 32-bit / float | none | Out of scope (locked) |
| misaligned int access | ignored bit 0 | Not a C feature; compiler must align |

No ISA change was needed before freezing. The review found one ABI-level gap
(varargs) and one compiler obligation (alignment), both documented rather than
fixed by adding instructions. The review also confirmed that all six branch
relations and all ten ALU operations have both register and immediate forms where
it matters.

---

## 3. Proposed deviations from the locked brief

### P1. Variadic functions are not supported (minor, flagged)

The brief asks for "varargs (minimal)". This implementation defines **no** varargs
convention in ABI v1: every function has fixed arity. Rationale: any varargs
convention needs an argument-block pointer or a count register, plus callee-side
walking rules; without a compiler that emits it, it would be untested
specification text. The safest option is to state the limitation and keep the ABI
implementable and testable. If Phase 7 needs `printf`, a v1.1 ABI can add "pointer
to argument block in a3" without changing the ISA.

### P2. No Fmax number in Phase 4 (not a design deviation)

The brief lists Fmax/CPI as the later performance metric. Phase 4 has no CPU RTL,
so only CPI and instruction counts are real; Fmax is explicitly not measured. It
will be measured in Phase 5/6 after synthesis.

### P3. Build location (environment deviation, not a design decision)

The new repository was requested in a dedicated project directory, but the
session sandbox only allowed writes under a different directory. The
project was built and tested in the sandbox workspace and then copied to the
requested path. Both trees contain the same files and git history; the reference
MiniCPU-8 tree was never modified. See the final report.

No other deviations. In particular: HALT=0, R0 hardwired, no flags, 5-stage
pipeline, not-taken prediction, load-use stall including store data, reset gating,
and the MMIO map are implemented exactly as locked.

---

## 4. Phase 5A decisions (continuing the numbering)

| # | Area | Decision | Alternatives considered | Reason |
|---|---|---|---|---|
| D31 | RTL shape | One RTL with `FORWARDING : boolean`; Phase 5A runs with `false`. The forwarding muxes and `forwarding_unit` are structurally present but select ID/EX values in 5A. | Two separate RTLs; no forwarding unit until 5B. | The brief requires a single codebase. The 5B change is then a generic/verification step, not a rewrite. `FORWARDING=true` is **unverified** in 5A. |
| D32 | 5A hazard rule | Stall in ID while a producer of an actual source is in EX or MEM and rd /= R0; WB is covered by write-through. Distance 1 costs two stalls, distance 2 one, distance 3 none. | Load-use-only stall with no forwarding (would be incorrect); full forwarding now. | No forwarding means the value is only architecturally visible in WB. The conservative rule is correct and simple. Cycle counts intentionally differ from the contract and are not compared. |
| D33 | 5A cycle comparison | Compare retirement records with `retire` ignored, plus final architectural state. Do not compare cycles, stalls or CPI. | Compare cycles and accept mismatches; invent a new cycle contract. | 5A is intentionally slower. 5B owns cycle exactness. Flush counts are still compared because they are independent of the stall rule. |
| D34 | Image loading | Testbench-only impure textio function at elaboration, returning unconstrained `instr_image_t`/`byte_image_t`; the core pads/constrains them. | VHDL package per program; hex through ports. | Works in NVC and GHDL (F21), keeps the core synthesizable and free of file I/O, and avoids a per-program compile. |
| D35 | Retirement interface | The core exposes a combinational RETIRE port; the testbench assigns sequence numbers and the retire cycle. | Core owns a trace buffer/FIFO; debug port. | No trace storage in synthesizable RTL, no simulation-only constructs, and the testbench can drain/check after HALT. Synthesis removes the port when unconnected. |
| D36 | Coverage counters | Optional `COUNTING` generic and `cov` array; false by default, true in the testbench. Counters observe only, never drive architectural state. | Recover all coverage from the trace. | Stalls by cause and HALT-in-shadow cannot be recovered reliably from an architectural trace. The counters are verification-only and removable. |
| D37 | Cycle-sensitive tests | Exclude `05_mmio` (GPIO_IN/TIMER/UART_STATUS) and `hello` (UART polling) from 5A differential comparison. Fuzz uses write-only MMIO and `UART_CYCLES_PER_BYTE=0`. | Model the synchroniser/cycle timing in the golden model now. | 5A has no cycle exactness, so these programs are not meaningful vectors. The RTL still runs one directed UART-drop program with a hand expectation. |
| D38 | UART busy model | Busy countdown for `UART_CYCLES_PER_BYTE` cycles after the write; the write cycle itself is not observable by a status load (one MEM stage). | Match the golden `cycle <= busy_until` literally, including the write cycle. | Observationally equivalent in this pipeline; documented in F2. |
| D39 | GPIO_IN | Two-flop synchroniser in the RTL; programs reading GPIO_IN are cycle_sensitive and excluded in 5A. | Remove the synchroniser to match the golden immediate value. | The brief requires a synchroniser; removing it would be a hardware-quality regression. F1. |
| D40 | Verification tools | `rtl_run.py` (single case), `rtl_suite.py` (suite), `rtl_fuzz.py`, `mutation_check.py`, `rtl_leaf_tests.py`, `synth_smoke.py`; all standard-library Python. | One monolithic script; third-party frameworks. | Each tool has one job and can be run independently; `make test` chains the quick versions. |
| D41 | Mutation 11 | The mutation delays store visibility by one cycle via a previous-store shadow in dmem; a store-to-load at distance 1 returns the old value. | Change the store commit point in the core (would require a second dmem write port). | The shadow reproduces the architectural bug with a small, compile-clean textual change; a_store_load_d1 detects it. |
| D42 | Synthesis scope | Synthesize the core with default generics and report SB_RAM40_4K/LUT/FF; Fmax from nextpnr is an estimate only, seed 1, target 50 MHz. Do not tune. | Optimize for Fmax now. | Phase 5A is functional verification; timing is Phase 6. |

| D43 | 5B target | FORWARDING=true becomes the default and the cycle-exact configuration; FORWARDING=false is kept as a stall-only regression configuration. | Delete the stall-only path; make forwarding a new entity. | The brief requires both and the 5A RTL to remain valid; D31 already fixed the single-RTL shape. |
| D44 | Forwarding rule | Full forwarding from EX/MEM (youngest) and MEM/WB, EX/MEM wins; a load in EX/MEM is never forwarded and instead stalls the load-use distance-1 consumer; R0 is never forwarded; the mux is gated by the stage valid bits. | Forward loads with a replay; forward from EX/MEM without validity. | This is the contract's model; the valid gating is what keeps bubbles and flushed instructions from corrupting operands. |
| D45 | Cycle-exact comparison | The retire-cycle column AND the per-cycle stage-occupancy pipeview are compared to the timing layer, in addition to the retirement trace and final state. | Compare architecture only (5A). | The brief requires "cycle-exact including the retire-cycle column and per-cycle stage occupancy"; a same-trace/different-cycle design would otherwise pass. |
| D46 | Peripheral timing | Step 2 documented TIMER, UART busy and GPIO_IN as normative contract section 7 and fixed the 5A busy off-by-one (F25). | Model the peripherals only in the testbench; leave the contract silent. | The brief permits exactly this area to be amended with hand derivations; otherwise cycle exactness of hello/05_mmio would be undefined. |
| D47 | Cycle formula | The reported and asserted model is `cycles = N + 4 + S + 2R` with the listed definitions; every benchmark must satisfy it exactly. | Just report cycles. | It gives an independently hand-checkable number and separates fill, stalls and control penalties (F29, docs/OPTIMIZATION_DATA.md). |
| D48 | Mutation coverage | 30 mutations (12 from 5A plus 18 forwarding/peripheral/cycle ones); the check passes only at 100% detected on NVC and at least 90% detected by the fuzz-only corpus; timing edges are corpus programs, not directed-test imports. | Report the raw matrix without thresholds; add only RTL mutations. | The brief sets the 100%/90% thresholds; the timing-edge corpus programs make the peripheral and cycle mutations fuzz-detectable. |
| D49 | Fmax reporting | Fmax comes from an unconstrained flat nextpnr-ice40 run, HX8K and UP5K at 50 MHz, seeds 1-3, both forwarding configs, and is reported as a lower-bound estimate. No timing fix is attempted. | Tune for Fmax; report a single device number. | Fmax tuning is a non-goal; the flat flow is honest about being untuned. The forwarding build is slower in this flow (F30). |
| D50 | Cycle-counting coverage counters | 19 forwarding counters (total 36) are added under COUNTING; the permanently-zero ones assert the load-use-vs-flush and bubble-forwarding impossibilities (F28). | Add counters only for observed events. | A counter that must stay zero is a real assertion: if the RTL ever forwards from a bubble the check fails loudly. |
| D51 | Optimization document | `docs/OPTIMIZATION_DATA.md` records measured cycle composition, arith-helper share and branch direction, and states what-if savings as arithmetic only. | Leave the data in the final report only. | The brief asks for the data and for honesty about estimates; a durable file is citable and regenerable. |
| D52 | Post-synthesis simulation | Optional Step 10 uses generic technology mapping + Yosys \`sim\` (initialized memories kept as logic), asserting GPIO_OUT and HALT; the iCE40 cell netlist cannot be simulated with the open model because \`SB_RAM40_4K\` INIT is not loaded (F31). | Skip Step 10 entirely; fix or vendor a RAM simulation model. | A real gate-level run of the forwarding logic is achievable now and is non-vacuous (mutation 16 fails it); Step 10 was optional but could be done honestly. |

| D53 | Step 1c coverage enforcement | The random-only coverage thresholds are enforced in *every* full fuzz run, including the forwarding run; `--no-cov-assert` is no longer used by the Makefile.  A dedicated `store_load` generator mode, plus store->load templates in the `fwd` mode, produce the distances 1..4 with the SB/SW x LB/LBU/LW size pairs and both address parities. | Keep `--no-cov-assert` for the forwarding run and report the shortfall. | The 5B brief requires store_load_d1/d2/d3 >= 50 from random programs only.  The forwarding run had 3334/2/0, i.e. the requirement was not met and the threshold had been switched off.  The generator is fixed instead of the threshold. |
| D54 | HALT placement in generated programs | The `HALT` of the `fwd` mode is emitted once, at the very end, by `fwd()` rather than in the middle of `fwd_specials()`. | Keep the HALT where it was and accept that later templates are dead code. | The HALT ended the program early, so every template after it — including the store->load distances — never executed.  A HALT must be the last instruction of a program. |
| D55 | Wrong expectation (F32) | `tests/directed/j_load_store_addr_d1.expected` is corrected to `ram 0006 5500`, the value derived by hand from ISA.md section 7.1 (a `SW`/`LW` address has bit 0 ignored, so address 0x0007 is the word at 0x0006).  The RTL and the model were not changed. | Change the RTL to store at the odd address 0x0007. | ISA.md section 7.1 is explicit that bit 0 of a word address is ignored.  The RTL was already correct; the hand-written expectation was wrong.  Correcting the expectation to the specification is the only legitimate direction (F32). |
| D56 | Tool locations | GHDL 6.0.0 lives in `$(HOME)/tools/ghdl` and the ghdl-yosys-plugin in `$(HOME)/tools/ghdl-yosys-plugin`; the Makefile and the Python tools prefer these, then PATH, and only then the ephemeral `/tmp` copy.  A missing GHDL fails loudly with the list of searched paths. | Keep depending on `/tmp/ghdl-llvm-6.0.0-macos15-aarch64`. | `/tmp` is wiped on reboot, so a build that depends on it breaks silently later.  A permanent path under `~/tools` plus a loud failure is reproducible. |
| D57 | Critical-path prediction vs measurement | The Step-8 measurement is compared against the five predictions in `RTL_ARCH.md` section 9, one row per prediction, with an explicit *confirmed / partially confirmed / refuted* verdict.  The critical paths are read from the nextpnr JSON report and mapped back to the RTL through the Yosys netlist `hdlname` attributes. | Report only the raw nextpnr console text. | The brief requires a recorded comparison, and the nextpnr console text uses sink-derived net names that do not identify the RTL signal, so the netlist mapping is needed to make the comparison meaningful.  Prediction 1 is **refuted** and is stated as such (F33). |
| D58 | Mutation 8 scope | Mutation 8 breaks only the dmem *read* path (the word index used for `LW`), not the write path. | Break both read and write. | Breaking both shifts the store and the load consistently and hides the bug; with the read path only, the mutation is caught by both `a_misaligned` and `j_load_store_addr_d1`, which is the regression proof for F32. |
| D59 | Post-synthesis comparison depth | The optional Step-10 gate-level run compares the **full retirement trace including the retire-cycle column** and the **per-cycle pipeview**, for three programs, against the golden timing layer — not only the final GPIO_OUT. | Compare the final state only; compare the trace but not the cycles. | The generic-mapped netlist still carries the core's `cycle_count`, and the RTL testbench's cycle numbering (first cycle after reset release = cycle 1) matches `cycle_count + 1` exactly, so a cycle-exact gate-level comparison *is* possible and is therefore done.  The earlier note that only the retire *order* could be compared is superseded. |
| D60 | Gate-level sampling point | The VCD is sampled at the **falling** clock edge, the same point at which the RTL testbench samples the retirement interface and the pipeview. | Sample at the rising edge. | The testbench samples mid-cycle; sampling at the rising edge would compare a different point in the cycle and produce a one-cycle offset against both the model and the RTL testbench. |
| D61 | ghdl-yosys-plugin library path | The plugin's `@rpath` is extended with `$(HOME)/tools/ghdl/lib`, and `postsynth_sim.py` also sets `DYLD_FALLBACK_LIBRARY_PATH` (`LD_LIBRARY_PATH` elsewhere) to the library directory of the GHDL it uses. | Require the user to set the variable by hand. | The plugin links against `libghdl-6_0_0.dylib`; the original rpath pointed at a `/tmp` directory that no longer exists, so the plugin failed to load with "Library not loaded".  A rebuild must not depend on a manual environment step. |

No ISA, ABI or architectural change was required in Phase 5B. The only
contract/timing-layer changes are the Step-2 peripheral items (contract section
7, F25) permitted by the brief; everything else is RTL, verification and
documentation.