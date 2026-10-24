# Phase 5 RTL findings and spec audits

This file records every ambiguity, inconsistency, or implementation choice found
while building the Phase 5 RTL. It is evidence, not a second specification. If a
finding required a change to the golden model or the microarchitecture contract,
it is marked **CONTRACT/MODEL CHANGE** and listed in the final report.

Status at the start of Phase 5A: the ISA is frozen at tag `isa-v1-frozen`. No
finding below changes ISA semantics, opcodes, or encodings.

---

## F1. GPIO_IN synchroniser latency vs the golden model (5A exclusion)

**Evidence.** `docs/ISA.md` section 7.2 says GPIO_IN is a 16-bit input; the
microarchitecture contract says it is a model parameter, constant for a run unless
the test supplies timed values. The Phase 4 golden model returns
`gpio_in_value()` immediately with no synchroniser. The Phase 5A RTL uses a
two-flop synchroniser (required by the brief), so the value seen by a load is the
input value from two cycles earlier (reset value 0 for the first two cycles).

**Impact.** A program that reads GPIO_IN before the synchroniser has settled, or
observes a timed GPIO_IN change, can differ between the RTL and the golden model.
No reference program or benchmark reads GPIO_IN except `tests/reference_traces/05_mmio.asm`.

**Decision (safest).** Keep the two-flop synchroniser in the RTL. Classify
GPIO_IN-reading programs as `cycle_sensitive` and exclude them from 5A
differential comparison. Do not weaken the synchroniser to match the model.
Documented in the final report. This is **not** a contract or model change; it is
an implementation-latency difference that Phase 5B must either model or continue
to exclude. The directed MMIO test uses GPIO_OUT and UART only, and the MMIO leaf
testbench checks the synchroniser explicitly.

## F2. UART busy window: write cycle inclusion

**Evidence.** `tools/isa_sim.py` implements
`uart_busy() = cycle <= uart_busy_until` and sets
`uart_busy_until = cycle + UART_CYCLES_PER_BYTE` on a write. Thus the write
cycle itself reads busy, and the transmitter is busy for `cycles_per_byte + 1`
observable cycles including the write cycle. The RTL starts a busy countdown of
`UART_CYCLES_PER_BYTE` at the end of the write cycle, so UART_STATUS reads in
`c+1 .. c+cpb` are busy and `c+cpb+1` is idle. A status read in the same cycle
as the write is impossible (one MEM stage, one instruction), so the two models are
observationally equivalent for every legal pipeline. A same-cycle status read
would require a second MEM port, which the machine does not have.

**Decision.** Implement the countdown; document that the write cycle's own
`busy` bit is not observable. No change to the contract or model. The directed
UART-drop test checks the observable behaviour. Phase 5B's cycle-exact comparison
must not schedule a UART_STATUS load in the same MEM cycle as a UART_TX store
(impossible in this ISA/pipeline).

## F3. HALT PC freeze value is not numerically specified

**Evidence.** The ISA and contract say HALT "freezes the PC" and the final
architectural PC is the HALT's PC, but they do not state the value of the fetch
pointer while frozen. The golden timing layer freezes its fetch pointer at the
value it held when HALT was in EX, which is normally `HALT_PC + 2` (the last
fetched younger PC), and reports the architectural PC as `HALT_PC`.

**Decision.** The RTL freezes its PC register exactly like the golden fetch pointer
(same update priority), and the CPU testbench reports the architectural final PC as
the HALT retirement PC. The testbench additionally checks that the PC register is
stable after HALT. No contract/model change; the architectural value is unambiguous.

## F4. "Reset changes no architectural state except PC" vs GPIO_OUT/TIMER/UART

**Evidence.** The general project rule says reset must not change architectural
state except PC. `docs/ISA.md` section 8 and the contract section 5 explicitly
specify GPIO_OUT=0, TIMER=0 and UART idle on reset. These are I/O state, not the
register file or data RAM.

**Decision.** Follow the ISA/contract: reset clears GPIO_OUT, the timer, and the
UART busy state; it preserves registers and data RAM and performs no RF/RAM/MMIO
write. This is an explicit documented exception, not a violation. No change.

## F5. Instruction-memory range check placement

**Evidence.** The brief requires the out-of-range check on the registered address
so it does not lengthen the BRAM address path. A naive `if next_pc >= INSTR_WORDS`
before the BRAM would put the next-PC adder/comparator in the address path.

**Decision.** The imem compares the PC value already registered for the current IF
cycle; the comparator controls the data loaded into the output register, not the
BRAM address. The BRAM address is the registered PC (truncated to the array
bounds). This is documented in `RTL_ARCH.md` section 3.1 and checked by the imem
testbench and the fetch-beyond test. No change.

## F6. Program length vs INSTR_WORDS

**Evidence.** The core only receives `INSTR_WORDS` and the image; it does not know
`program_words`. The ISA says fetch beyond the program image returns HALT, which is
equivalent to zero-filled instruction memory (the assembler fills unused words with
zero).

**Decision.** The testbench loads the image into an `INSTR_WORDS` array padded with
zeros; the core's out-of-range check covers indices >= INSTR_WORDS. A fetch beyond
the program but within INSTR_WORDS returns the zero word, which is HALT. This
matches the golden `fetch()` for a zero-padded image. No change.

## F7. Store trace `memdata` for SB

**Evidence.** `docs/TRACE_FORMAT.md` says for SB `memdata` is the low 8 bits of
the stored register, shown as four hex digits. The golden model masks with 0xFF.
An RTL that reports the full 16-bit store data would differ.

**Decision.** The retirement trace for SB reports `store_data & 0x00FF`; SW reports
all 16 bits. The CPU testbench implements this. No change.

## F8. JAL's rs1 field is ignored

**Evidence.** The UJ format says rs1 bits must be 0. The golden decoder ignores
them and reports no sources for JAL.

**Decision.** The RTL decoder reports no sources for JAL regardless of the rs1
field; a nonzero field is still a valid JAL. No change.

## F9. Opcode 0 with nonzero low bits

**Evidence.** The ISA says the all-zero word is HALT. The golden decoder treats any
word with opcode 0 as HALT, ignoring the low bits.

**Decision.** The RTL decoder treats any opcode 0 as HALT. This is deterministic
and matches the model; the assembler only emits all-zero HALT. No change.

## F10. Unassigned opcodes and reserved 0x21..0x2F

**Evidence.** The ISA says unassigned opcodes execute as NOP; the golden decoder
returns NOP for any opcode not in its table. The reserved range is a subset.

**Decision.** The RTL decoder's default is NOP for every unassigned opcode,
including 0x21..0x2F. They retire with no effects. No change.

## F11. MMIO byte access and UART_TX at an odd address

**Evidence.** The ISA says byte accesses to MMIO ignore address bit 0 and select
the register by `addr & 0xFFFE`; an SB to 0xFF00 or 0xFF01 transmits the low byte.
A word access also ignores bit 0.

**Decision.** The MMIO block masks the address for register selection in all
accesses. UART_TX at 0xFF00/0xFF01, GPIO_OUT at 0xFF04/0xFF05, etc. No change.

## F12. Data-memory write commit point

**Evidence.** The contract says a store commits at the end of MEM. The Phase 4
golden model stores in its MEM step, which is the same cycle in its stage model.

**Decision.** RTL commits the store at the rising edge ending MEM. A younger load
at distance 1 reads in the next MEM cycle and sees the new value. This is the key
store-to-load ordering proof in `RTL_ARCH.md` section 5(a). No change.

## F13. Reset gating of the register-file write-through

**Evidence.** The contract says reset suppresses all architectural writes. The
write-through mux is a read path, but if it were left enabled with a stale WB valid
bit during reset it could make ID see non-architectural data.

**Decision.** `wb_we` is qualified by `not rst` and the pipeline valid bits are
cleared during reset, so the write-through cannot present a new value during reset.
The register file write port is also explicitly gated by `rst='0'`. No change.

## F14. 5A cycle counts vs the contract

**Evidence.** The contract's hazard rule is load-use-only with full forwarding.
Phase 5A intentionally has no forwarding and stalls for every EX/MEM producer.
A distance-1 consumer therefore takes two stalls instead of one, and distance-2
takes one instead of none.

**Decision.** 5A compares retirement records with the retire-cycle column ignored.
Cycle counts, CPI, and the contract's hand-computed examples are **not** claimed in
5A. Phase 5B enables forwarding and must reproduce the contract exactly. This is
the central explicit non-goal of 5A, not a bug.

## F15. Coverage counters are verification-only logic

**Evidence.** The fuzz step requires stalls by cause and HALT-in-shadow counters,
which cannot be recovered reliably from the architectural trace. The core therefore
has an optional `COUNTING` generic and a counter output.

**Decision.** `COUNTING=false` ties all counters to zero and is the synthesis
default. The counters observe pipeline events and never drive architectural state.
The RETIRE port is likewise removable when unconnected. Neither adds logic to the
architectural datapath. No change.

## F16. Cycle counter and TIMER after long runs

**Evidence.** The RTL cycle counter is 16 bits and wraps modulo 2^16; the timer
read is `cycle_count + 1` to make cycle 1 read 1. The golden model's cycle is an
unbounded Python integer and its timer returns `cycle & 0xFFFF`. The two agree
modulo 2^16 for all runs shorter than 65535 cycles; for a run that wraps, the
counter's modulo arithmetic still matches `m.cycle & 0xFFFF` provided the counter
is reset at the same point.

**Decision.** Use a 16-bit counter and document the wrap. Fuzz programs are short;
the directed wrap test is separate. No change.

## F17. UART TX output register and dropped bytes

**Evidence.** When a UART write is dropped because the transmitter is busy, the
architectural store still retires and its trace record shows the store. The UART
output stream must not contain the dropped byte.

**Decision.** The MMIO block emits a one-cycle `uart_tx_valid` pulse and the byte
only when the write is accepted. The trace's store record is independent of
acceptance. The UART-drop directed test checks one accepted and one dropped byte.
No change.

## F18. IF/ID instruction hold during a stall (RTL bug found and fixed)

**Evidence.** The first CPU testbench run (a 4-instruction smoke program)
retired only 3 instructions.  The imem output register was serving directly as the
IF/ID instruction field.  During a stall the PC is frozen at the *fetch pointer*,
which is two instructions ahead of the ID instruction; re-reading `pc` therefore
replaced the ID instruction with the younger fetched word.  This is a property
of using a synchronous BRAM output register as the pipeline register, not a spec
ambiguity.

**Fix.** During `stall=1`, the imem address is `if_id_pc` instead of `pc`.  The
output register re-reads and holds the ID instruction; PC still holds the fetch
pointer.  The smoke trace then contained all four instructions, and all directed,
reference and benchmark traces passed.  Documented in `RTL_ARCH.md` section 3.1.
No contract or golden-model change is needed: this is an RTL implementation fix.

## F19. DMEM little-endian concatenation (RTL bug found and fixed)

**Evidence.** The first dmem testbench run failed every word read with the two
bytes swapped (`CDAB` instead of `ABCD` for a stored value `0xABCD`).  The
expression `even_bank(i) & odd_bank(i)` puts the even byte in the high half; the
ISA is little-endian, so the even byte is the low half.

**Fix.** Read `odd_bank(i) & even_bank(i)`.  All dmem checks and all subsequent
differential traces passed.  No change to the ISA, contract, or golden model.

## F20. Post-HALT drain check (verification gap found by mutation 9)

**Evidence.** The mutation "HALT does not flush younger instructions" was not
detected by `d_halt`: the younger instruction retires one cycle *after* HALT, and
the testbench stopped at the HALT retirement.  The architectural trace of a
normal run still ended at HALT, so the bug was invisible.

**Fix.** The CPU testbench now continues for `DRAIN_CYCLES=2` after HALT and
asserts that `retire_valid=0` and the frozen PC does not move.  Mutation 9 is now
detected by `d_halt`.  This is a testbench strengthening, not a spec change.

## F21. Textio image loading works in NVC and GHDL

**Evidence.** The brief allowed a package-per-program fallback if elaboration-time
textio loading failed in either simulator.  A generic `instr_image_t`/
`byte_image_t` loaded by an impure function at elaboration was tested in NVC
1.23 and GHDL 6.0.0; both produced identical traces.  The fallback was not needed.

**Decision.** Keep the textio+generic approach.  Document the NVC detail that the
work library lives under the work directory (`<work>/work`), and the GHDL detail
that `GHDL_PREFIX` must point at the GHDL library prefix (not the install root) for
ghdl-yosys-plugin.  Environment only; no design change.

## F22. Synthesis plugin environment

**Evidence.** `ghdl-yosys-plugin` built at tag `ghdl-v6.0.0` needs
`GHDL_PREFIX=<ghdl>/lib/ghdl`; with the install root it searched for
`<prefix>/ieee/v08` and failed.  The built module is
`/tmp/ghdl-yosys-plugin/ghdl.so` and Yosys 0.70 loads it with `-m`.

**Decision.** Record the exact command in `tools/synth_smoke.py` and the final
report.  The target remains time-boxed and non-blocking.

## F23. DMEM block-RAM inference (RTL structure changed, behaviour unchanged)

**Evidence.** The first Yosys/GHDL synthesis attempt expanded the dmem byte banks
to 4096+ flip-flops and the whole core synthesis stalled for minutes.  A minimal
reproduction showed that GHDL emits `$memrd_v2`/`$memwr_v2` for a single bank with
an unconditional registered read, but expands two banks placed in one clocked
process, or banks with conditional reads, into flip-flops and muxes.  `memory_dff`
could not merge the read register into the memory because the read was enabled and
because the odd-bank write data was a mux inside the process.

**Fix (behaviour-preserving).**

* one clocked process per byte bank, each with exactly one array;
* unconditional registered bank reads (the pipeline valid bits decide whether the
  value is used), so the read register merges into the BRAM output register;
* the odd-bank write data is selected combinationally (`odd_wdata`) instead of a
  mux inside the process;
* the read type/address is captured in parallel with the bank read for the WB mux;
* dmem has no reset branch in the memory processes (BRAM has no reset); the core
  gates `mem_load_en`/`mem_store_en` with `rst`, and the dmem output is forced to
  zero during reset combinationally.

**Result.** The core wrapper synthesises 24 `SB_RAM40_4K` (16 instruction +
8 data) and completes in seconds; all 8,023,388 leaf checks (both simulators), the
directed suite, reference traces, benchmarks and the 312-program fuzz still pass.
No ISA, contract or golden-model change.

## F24. The true default core constant-folds its instruction ROM

**Evidence.** `minicpu16_core` with the true default generics has a one-word
`INSTR_INIT` (HALT).  Yosys correctly constant-folds it: the default-core
synthesis reports 0 `SB_RAM40_4K`, 43 LUT4 and 125 flip-flops.  This is a valid
but empty design, not an inference failure.

**Decision.** `tools/synth_smoke.py` reports both: the true default core (0 BRAM)
and a synthesis-only wrapper with a non-constant 2048-word image (24 BRAM).  The
wrapper is not part of the architectural RTL.  Fmax numbers from nextpnr are
estimates (seed 1, target 50 MHz), not tuned results.

## F25. UART busy was one cycle too long (RTL bug found and fixed)

**Evidence.** The Phase 5A MMIO block derived `uart_busy` from a registered
flag, so a status load observed busy for `N+1` cycles and a write issued on the
"last busy" cycle could be accepted when the contract says it must be dropped.
The hand tables in `docs/MICROARCH_CONTRACT.md` section 7.2 pin busy to exactly
`c+1 .. c+N` after an accepting write in MEM cycle `c`.

**Fix.** `rtl/mmio.vhd` now computes `uart_busy_s` combinationally from
`uart_remaining`; the registered `uart_busy_r` was removed.  This changes no
architectural register and no ISA behaviour.

**Regression.** `f_uart_busy_edge` samples `UART_STATUS` on the last busy
cycle (must read 1) and the first idle cycle (must read 0), with a hand-derived
`.expected`; mutation 28 ("busy window off by one") is now detected by the
directed test and by the fuzz-only corpus.

## F26. Forwarding-unit producer wiring was stale (5A latent bug)

**Evidence.** The Phase 5A `forwarding_unit` was structurally present but its
producer inputs were wired to the wrong pipeline registers, so the `FORWARDING`
generic was not actually implementing a correct forwarding network.

**Fix.** The unit now observes EX/MEM and MEM/WB producers against the ID/EX
consumer, with EX/MEM winning over MEM/WB, a load in EX/MEM never forwarded,
R0 never forwarded, and valid bits preventing forwarding from bubbles.  The
`FORWARDING=false` configuration is unchanged and still matches the 5A traces
architecturally.

**Regression.** The whole forwarding-targeted fuzz (1000 programs) and the
directed `j_*` forwarding tests are cycle-exact against the layer; mutations
13-19, 23-26 are detected.

## F27. Link-register EX/MEM forwarding is architecturally unreachable

**Evidence.** A taken JAL/JALR flushes the instruction that follows it in IF/ID;
that instruction re-fetches when the jump is in WB, by which time the link value
is already in the register file.  The only exercised link path is therefore
WB->ID write-through.

**Consequence.** The EX/MEM link forwarding path exists in the RTL but cannot
change an architectural result.  It is not removed (it would complicate the
mux for no gain) and it is explicitly not claimed as verified by trace
comparison.  A directed test (`j_jal_link_next`) covers the reachable
WB->ID case.

## F28. `fwd_from_bubble` and `stall_near_flush` counters stay at zero

**Evidence.** With forwarding enabled, a load-use stall and a redirect cannot
occur in the same cycle: the stall requires a load in EX, and the redirect is
produced by the instruction in EX.  Likewise the forwarding unit is gated by
the stage valid bits, so it never forwards from a bubble.

**Consequence.** The coverage counters `fwd_from_bubble` and
`stall_near_flush` are expected to read zero under FORWARDING=true.  They are
kept because they are the exact assertions that this impossibility holds; a
non-zero value would itself be a bug.  The stall-versus-flush priority is
covered by mutation 22 in the FORWARDING=false configuration.

## F29. Cycle definition and the HALT drain

**Evidence.** Every benchmark satisfies `cycles = N + 4 + S + 2*R` exactly
(docs/OPTIMIZATION_DATA.md).  The `+4` is the IF..WB fill and the HALT drain;
HALT itself adds no extra penalty beyond being an instruction that retires.

**Consequence.** The retire-cycle column of the trace is an exact,
independently-checkable quantity, not a derived convenience.

## F30. Flat nextpnr estimates are lower for FORWARDING=true

**Evidence.** In the unconstrained flat `nextpnr-ice40` flow, HX8K reports
~43-45 MHz for FORWARDING=true and ~56-64 MHz for FORWARDING=false, with the
forwarding build using ~112 more LUT4.  This is a placement/routing artifact of
an unpipelined, untuned design; it is not a hardware result.

**Consequence.** The reported Fmax numbers are lower-bound estimates from a
shared, unconstrained flow and must not be read as a comparison of the two
microarchitectures.  No timing fix was attempted in 5B (non-goal).

## F31. Post-synthesis simulation needs generic technology mapping

**Attempt.** Step 10 makes an optional gate-level run of the synthesized
design with `tools/postsynth_sim.py`: Yosys + ghdl-yosys-plugin maps the core,
then the built-in `sim` command executes the netlist.

**Finding.** Running the **iCE40 cell netlist** (after `synth_ice40`) under the
open `cells_sim.v` model does not work: the instruction ROM is inferred as
`SB_RAM40_4K`, whose simulation model only preloads `INIT_0` and is not given the
per-instance `INIT_*` parameters that the synthesizer emitted, so the core reads
a zero instruction word, which is HALT; the fetch pointer sticks at index 1.
The synthesized netlist itself is correct — the `INIT_0` parameters carry the
program (verified by reading them back and by a direct `SB_RAM40_4K` model
probe, which returned the expected word at address 1).  This is a limitation of
the open simulation model / Yosys `sim` parameter handling, not of the RTL.

**Choice.** `postsynth_sim.py` therefore maps the design with generic
technology mapping (`proc; opt; memory; opt; techmap; opt`), which keeps the
memories as initialized flip-flop logic, and runs the gate-level logic through
Yosys `sim`.

**What the gate-level run compares (Phase 5B, T5).**  Three programs are run
(`j_double_producer`, `j_load_store_addr_d1`, `j_load_branch_d1`); together
they cover ALU forwarding, the load-use stall, a store whose address comes
from a load (the F32 case) and a taken branch that flushes the pipeline.  For
each program the tool reconstructs, from the VCD:

* the **full retirement trace**, record by record: pc, instruction word, rd,
  written value, memory kind/address/size/data, branch taken and target — and
  the retire *cycle* column as well;
* the **per-cycle pipeview**: the occupancy and PC of all four pipeline
  stages, plus the stall and flush flags.

Both are compared against the golden timing layer's own output
(`isa_sim.run_pipeline`) with the same rule the RTL runner uses for the
pipeview (a stale PC in an empty stage is not significant).  The final
GPIO_OUT and the HALT are compared as well, and the run fails if anything
retires after the HALT.

The retire cycle *is* recoverable at gate level: the generic-mapped netlist
still carries the core's architectural cycle counter (`cycle_count`), which is
cleared by reset and incremented once per non-reset clock.  The RTL
testbench numbers the first cycle after reset release as cycle 1, which is
exactly the cycle in which the counter still reads 0, so the gate-level cycle
number of a sample is `cycle_count + 1`.  The gate-level run is therefore
**cycle-exact**; the earlier note that only the retire *order* could be
compared is superseded.

Not compared: `pc_out` (the internal fetch pointer — its frozen value after
HALT is unspecified, F3; it is reported only) and the final RAM contents
(the stores are compared through the retirement trace instead, which is what
the RTL testbench itself asserts).

**Non-vacuity.** With mutation 16 applied to a scratch copy of the RTL
(disable forwarding to operand B), all three programs fail: the retirement
trace diverges in the *data* fields (e.g. a load returns 0x0000 instead of
0x0007) while the retire cycles still agree, so the check isolates the value
error from a timing error.  On the committed RTL all three pass.

**Toolchain note.** The `ghdl-yosys-plugin` binary links against
`libghdl-6_0_0.dylib` through an `@rpath` that pointed at a `/tmp` directory
which no longer exists.  The rpath was extended with the permanent
`$HOME/tools/ghdl/lib` directory, and `postsynth_sim.py` additionally sets
`DYLD_FALLBACK_LIBRARY_PATH` (`LD_LIBRARY_PATH` elsewhere) to the library
directory of the GHDL it uses, so the plugin loads without any manual step.

**Status.** Optional Step 10, run manually; not part of `make test`.

---

## F32. A hand-computed expectation was wrong; the RTL was right

**Symptom.** The first full-suite runs (`build_suite_full_nvc.log` and
`build_suite_full_ghdl.log`, both simulators) reported one failure:

```
FAIL dir_j_load_store_addr_d1     nvc   cycles=13     (gold 13    ) stalls=1     trace=8/8
  expected: ram[0x0007]: got 0000 want 5500
```

The directed test `tests/directed/j_load_store_addr_d1.asm` is:

```
0: li   r1, 0x0100
1: li   r2, 0x0007
2: sw   r2, 0(r1)      ; store 0x0007
3: li   r4, 0x0055
4: lw   r3, 0(r1)      ; r3 = 0x0007
5: sw   r4, 0(r3)      ; store 0x0055
6: lw   r5, 0(r3)
7: halt
```

**Hand derivation (from `docs/ISA.md` section 7.1 only, before looking at the
RTL or the model).**  ISA.md says:

> `LW`/`SW` use `byteaddr & 0xFFFE`; **address bit 0 is ignored**, there is no
> misalignment trap and no exception.

Walking the program:

| instr | operation | `byteaddr` | effective word address | effect |
|---|---|---|---|---|
| 2 | `sw r2, 0(r1)` | `0x0100 + 0 = 0x0100` | `0x0100 & 0xFFFE = 0x0100` | RAM[0x0100] = 0x0007 |
| 4 | `lw r3, 0(r1)` | `0x0100 + 0 = 0x0100` | `0x0100 & 0xFFFE = 0x0100` | r3 = 0x0007 |
| 5 | `sw r4, 0(r3)` | `0x0007 + 0 = 0x0007` | `0x0007 & 0xFFFE = **0x0006**` | RAM[0x0006] = 0x0055 |
| 6 | `lw r5, 0(r3)` | `0x0007 + 0 = 0x0007` | `0x0007 & 0xFFFE = **0x0006**` | r5 = 0x0055 |

So the correct final state is `ram 0100 0700` and **`ram 0006 5500`**.  The
hand-written expectation that said `ram 0007 5500` contradicted ISA.md: it
forgot that bit 0 of a `SW`/`LW` address is ignored, so the word lands at the
*even* address 0x0006, not at 0x0007.

**Classification: wrong expectation, RTL correct.**  The RTL wrote 0x0055 at
0x0006 and read it back as r5 = 0x0055 — exactly what ISA.md prescribes.  The
model agreed with the RTL (the suite compares against the model too, and the
model's own trace matched).  Only the hand-written `.expected` file was wrong.

**Fix.**  `tests/directed/j_load_store_addr_d1.expected` now states
`ram 0006 5500`, the value the hand derivation above produces.  **No RTL, no
model and no ISA change was made** — the expected value was corrected *to* the
specification, not the other way round.  The test is the regression: it now
passes on NVC and GHDL and would fail again if the RTL ever stopped aligning
`SW`/`LW` addresses down.

**Note on the log.**  The two failing logs are the pre-5B-commit runs; the
corrected expectation is in commit `03a4cd0` and every suite run since then
(`build_suite_5b_full.log`, `build_rtl_test_full.log`) reports 0 failures.

---

## F33. The predicted critical path was wrong: forwarding is not the bottleneck

**Claim under test.**  `docs/RTL_ARCH.md` section 9 predicted, for the
forwarding configuration, that the longest combinational path would be

> forwarding mux -> ALU -> branch/target adder -> PC

and section 7 predicted the same family of path for the 5A configuration.

**Measurement.**  `tools/synth_smoke.py` (Yosys 0.70 `synth_ice40` +
nextpnr-ice40, unconstrained placement, target 50 MHz, seeds 1-3, no tuning)
was re-run for both configurations, both devices, all three seeds.  The
critical paths were read from the nextpnr JSON report and each hop was mapped
back to the RTL through the Yosys netlist `hdlname` attributes.

**Finding.**  The prediction is **refuted**.  In all six `FORWARDING=true`
runs the critical path does **not** contain the forwarding mux, the ALU
operand mux, the branch comparator, the target adder or the PC adder.  The
`u_forward` instance appears exactly once, in the UP5K path, and only as the
*control* input `u.u_forward.ex_reg_write` — the hazard/valid compare — not as
a forwarded operand.

The real critical path in the forwarding configuration is:

> dmem bank output register -> LUT -> dmem output mux / EX/MEM address adder
> -> `id_ex_valid` / `stop_fetch` -> the PC register's clock enable.

Two structural causes, both visible in the netlist:

1. `dmem.vhd` reads the bank unconditionally
   (`if a < RAM_BYTES then even_q <= even_bank(idx);`) so the tool can merge the
   read register into the block RAM's output register.  The consequence is that
   the bank output bits and the `q_addr0` / `q_load_type` registers feed the
   *next* MEM instruction's address cone, so the chain crosses the dmem/EX
   boundary instead of stopping at a register.
2. The PC update is a priority mux (`redirect` > `HALT` > `stall` >
   `stop_fetch` > `+1`, `minicpu16_core.vhd:506-517`) whose *clock enable* is
   computed from `stop_fetch`, which is derived from `id_ex_valid` and the
   reset.  The hazard/valid control cone therefore terminates on a register
   enable pin, which is a timing endpoint even though it carries no data.

**Consequence.**  The forwarding datapath is not the reason the forwarding
build is slower in this flow.  The forwarding build uses ~112 more LUT4
(1369 vs 1257) and, in an untuned flat placement, that extra logic is what
moves the critical path onto the longer dmem-to-PC-enable cone.  This sharpens
F30: the `FORWARDING=true` Fmax deficit (43.75 vs 60.47 MHz geometric mean on
HX8K) is a *placement* effect of extra combinational logic, not a measurement
of the forwarding path itself.

**Fix / action.**  Documentation only.  `docs/RTL_ARCH.md` section 9.1 now
carries the full prediction-versus-measurement table with an explicit
"prediction 1: **refuted**" verdict, and section 7 carries a pointer to it.
**No RTL change was made** and no timing tuning was attempted (Fmax tuning is a
non-goal for this phase).

**Not verified.**  This is a flat, unconstrained, single-FPGA-family estimate.
It says nothing about a real ASIC or FPGA flow, and the absolute numbers are
not sign-off numbers.

---

## F34. CI was terminated during the quick RTL suite; cause not determined

**Observation.** The reported GitHub Actions `full` job passed all 26 leaf RTL
testbench runs, then `make rtl-suite-quick` was terminated with exit code 143.
The Makefile maps this target to `tools/rtl_suite.py --sim both --quick`.
The previous log showed only the Make target header, with no case-level
progress or simulator/resource versions. The workflow timeout is 45 minutes;
the Python runner already had a 180-second subprocess timeout, but its timeout
output was collapsed into a generic per-case error and buffered suite output
could hide the last completed case.

**Cause.** Not determined. The available log does not distinguish slow
simulation, an external termination, or an apt-GHDL compatibility issue.
Local GHDL is 6.0.0, NVC is 1.23.0, and Docker reproduction was unavailable
because the local Docker daemon was not running. The local host is macOS, so
the Ubuntu apt-GHDL invocation was not reproduced here.

**Diagnostic change.** Python Makefile recipes are unbuffered. The suite,
fuzz, and leaf runners log `START` and `PASS`/`FAIL` with elapsed seconds for
each run. `RTL_RUN_TIMEOUT` sets the RTL subprocess timeout (default 180
seconds); expiry now reports the simulator command and last 40 output lines.
The workflow prints GHDL, NVC, Python, Make, CPU-count, and memory versions/
resources before `make test`, even if an earlier step failed.

**Not verified.** The updated workflow has not run on GitHub Actions. No
simulator version change, test-scope reduction, or job split is justified by
the available evidence. The next Actions log should identify the last `START`
case and its timeout/failure details, or show that termination occurred
outside a case.

---

## Contract/model changes in Phase 5B

Phase 5B amended the contract and timing layer in exactly the Step-2 peripheral
area permitted by the brief (documented in `docs/MICROARCH_CONTRACT.md`
section 7 and `docs/DECISIONS.md`):

1. **UART busy window.**  Normative hand tables; busy is exactly `c+1..c+N`,
   the write cycle `c` is not observable, first idle is `c+N+1`, and a write
   whose MEM cycle is busy is dropped.  This corrected the 5A RTL (F25).
2. **GPIO_IN synchroniser.**  Two-flop synchroniser (L=2): a read in MEM cycle
   `c` returns `ext(c-2)`.  The schedule file format is now normative.
3. **TIMER.**  A read in MEM cycle `c` returns `c mod 2^16`.

No ISA change, and no change outside the peripheral timing listed above.

---

## Contract/model changes in Phase 5A

**None at the time of writing.** The 5A RTL is a stall-only implementation of the
frozen ISA; it does not claim cycle exactness, so it does not require the contract
or timing layer to change. If a later finding requires a change, it will be added
here and highlighted in the final report.
