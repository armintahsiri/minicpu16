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

---

## Contract/model changes in Phase 5A

**None at the time of writing.** The 5A RTL is a stall-only implementation of the
frozen ISA; it does not claim cycle exactness, so it does not require the contract
or timing layer to change. If a later finding requires a change, it will be added
here and highlighted in the final report.
