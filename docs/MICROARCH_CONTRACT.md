# MiniCPU-16 Microarchitecture Contract v1.0

This document is the verification contract for the Phase 5 pipelined RTL. The
golden model's timing layer implements exactly this contract, and the Phase 5
retirement trace must match it cycle for cycle on the same program and parameters.

Target: classic 5-stage pipeline IF/ID/EX/MEM/WB, synchronous Block-RAM
instruction and data memories, full forwarding, branches resolved in EX, static
not-taken prediction, load-use stall. No caches, no branch prediction beyond
not-taken, no out-of-order execution, no speculative memory writes.

---

## 1. Stages

| Stage | Work in this stage | State produced at the end of the cycle |
|---|---|---|
| **IF** | drive `PC` to instruction memory; instruction memory synchronous read | IF/ID <- {valid, pc, instr}; PC <- next PC (PC+1 unless redirected/stalled) |
| **ID** | decode; read register file (with WB write-through bypass); sign/zero helpers; hazard detection | ID/EX <- {valid, pc, instr, decoded control, rs1 value, rs2 value, imm16, source register numbers} |
| **EX** | forwarding muxes; ALU; branch compare and target; JALR target; link value | EX/MEM <- {valid, pc, instr, alu result, store data, branch taken, target, rd, control} |
| **MEM** | data memory read or write; MMIO read/write | MEM/WB <- {valid, pc, instr, result, load data, rd, control}; memory write commits at the end of MEM |
| **WB** | register file write; retirement record | architectural state updated; trace record emitted |

The register file has two combinational read ports used in ID and one write port
used in WB. WB writes at the end of the cycle; ID in the **same** cycle sees the
new value through the WB->ID write-through bypass. R0 writes are suppressed.

Instruction memory:

* Address = instruction index (PC), not bytes.
* Synchronous read, one cycle latency: the address is driven during IF cycle c and
  the 32-bit word is captured into IF/ID at the end of c.
* Fetching an index >= program length or >= `INSTR_WORDS` returns the HALT word
  (0x00000000).

Data memory:

* Address = byte address (16 bits), little-endian, synchronous read.
* Address and write data are driven during MEM cycle c. A read word is captured
  into MEM/WB at the end of c and is available in WB (and for forwarding) in c+1.
  A write commits at the end of c.
* MMIO reads and writes happen in MEM with the same timing as data RAM.
* Because all memory effects happen in MEM and the pipeline is in-order, a younger
  access can never pass an older one.

---

## 2. Forwarding rules

Forwarding is computed in EX for both ALU operands (rs1 and rs2), the store-data
operand, the branch/JALR operand, and the JALR base. Store data is read in ID,
forwarded in EX, and carried in EX/MEM to MEM.

| Producer instruction | Producer stage when consumer is in EX | Forward path into EX | Notes |
|---|---|---|---|
| ALU (ADD..SLTU, ADDI..SLTIU) | EX/MEM | EX/MEM -> EX | result is the ALU output |
| ALU | MEM/WB | MEM/WB -> EX | result carried through |
| JAL / JALR | EX/MEM or MEM/WB | link value forwarded | link = PC+1 |
| Load (LB/LBU/LW) | MEM/WB | MEM/WB -> EX | load data only exists in WB |
| Load | EX/MEM | **not forwarded** | consumer at distance 1 is stalled instead |
| Any producer | WB, consumer in ID | WB -> ID write-through | covers distance 3 |
| Store | - | stores write no register | |

Priority when more than one forwarding source matches: **EX/MEM is younger than
MEM/WB**, so EX/MEM wins. A load in EX/MEM does not forward; the hazard logic
guarantees that a consumer cannot be in EX at that time.

R0 is never forwarded and never written: a source of R0 reads 0.

---

## 3. Hazard and stall rules

The only data hazard that requires a stall is **load-use at distance 1**.

Let the instruction in EX be a load (LB, LBU or LW) with destination `rd`
(`rd != 0`). Let the instruction in ID be a valid instruction that reads `rd` in
**any** source position. Then stall for exactly one cycle.

Sources checked in ID:

| Instruction class | Source registers |
|---|---|
| R-type ALU | rs1, rs2 |
| I-type ALU / load / JALR | rs1 (JALR base is a source) |
| SB / SW | rs1 (address) **and rs2 (store data)** |
| Branches | rs1, rs2 |
| JAL | none |
| HALT / NOP / unassigned | none |

The store-data case is explicitly included: a load followed by a store that uses
the loaded value as **store data** stalls. A load followed by a store that uses the
loaded value as the **address base** also stalls. A load followed by a branch or
JALR that consumes the loaded value stalls.

Stall action for one cycle:

* PC holds its value (no fetch).
* IF/ID holds (the instruction in ID stays there).
* ID/EX receives a bubble (valid=0).
* EX/MEM and MEM/WB advance normally, so the load moves to MEM.
* The next cycle the consumer moves to EX and receives the load value from MEM/WB.

No stall is needed at distance 2 or more:

| Distance (consumer fetched after producer) | Producer stage when consumer in EX | Mechanism |
|---|---|---|
| 1 (back-to-back) | producer in MEM | ALU: EX/MEM forward; load: 1-cycle stall then MEM/WB |
| 2 | producer in WB | MEM/WB -> EX |
| 3 | producer retired | WB -> ID write-through |
| 4 or more | retired earlier | register file |

There is **no** structural hazard between IF and MEM: instruction and data memories
are separate (Harvard).

A taken branch and a load-use stall in the same cycle: the flush wins; the younger
instruction is squashed, so no stall is applied.

---

## 4. Branch, jump and HALT control

Conditional branches are predicted **not taken**. They are resolved in EX using
forwarded operands.

| Event in EX at cycle t | Action at the end of t | Younger instructions |
|---|---|---|
| Conditional branch not taken | PC <- PC+1 (normal) | none squashed |
| Conditional branch taken | PC <- PC+1+sext(imm16); IF/ID valid=0; ID/EX valid=0 | IF and ID squashed (2 cycles of penalty) |
| JAL | rd <- PC+1; PC <- target; flush IF and ID | 2 squashed |
| JALR | rd <- PC+1; target <- rs1_old+sext(imm16); flush IF and ID | 2 squashed |
| HALT | stop_fetch <- 1; PC frozen; flush IF and ID | all younger squashed |

The target of a taken branch is fetched in the cycle after EX (t+1). The two
instructions that were in IF and ID at t are discarded and never retire; they must
not commit any memory or register effect. (Their effects would only occur in MEM/WB,
which they never reach.)

HALT is not a branch, but it stops fetch when it reaches EX and drains through
MEM/WB. It retires normally at WB. Instructions younger than HALT are squashed even
though fetch beyond the image also returns HALT.

JALR with `rd == rs1`: the target uses the old `rs1` value; the link value is
written to the register. The forwarding network must not create a combinational
loop from the link write back into the target adder.

---

## 5. Reset

Reset is synchronous, active high. While `rst = 1` at a rising edge:

* PC <= 0; IF/ID, ID/EX, EX/MEM, MEM/WB valid bits <= 0; stop_fetch <= 0.
* No register-file write, no data-RAM write, no MMIO write, no UART byte start.
* GPIO_OUT <= 0; TIMER <= 0; UART busy <= 0.
* Registers R0..R15 and data RAM are **not** cleared. Their power-on values come
  from FPGA initialisation (registers 0, RAM from the image). A warm reset keeps
  the old values.

After reset is deasserted, the first cycle is cycle 1 and IF fetches PC 0. Reset
must not be used to reload `.data`.

---

## 6. Exact cycle-count definition

* **Cycle 1** is the first cycle after reset release. IF fetches instruction 0 in
  cycle 1.
* An instruction fetched (in IF) in cycle `f` is in ID at `f+1`, EX at `f+2`,
  MEM at `f+3`, and WB at `f+4`.
* An instruction **retires** at the end of its WB cycle. Its retirement trace record
  carries that WB cycle number.
* **Total cycles** for a run = the WB cycle of the retiring HALT instruction
  (inclusive), i.e. the cycle in which HALT retires. If the program never halts the
  run is aborted by the watchdog and the result is an error, not a cycle count.
* **Instruction count** = number of retired instructions, including HALT and
  including unassigned-opcode NOPs, excluding squashed instructions.
* **CPI** = total cycles / instruction count.
* **Stall count** = number of cycles in which a load-use stall was applied.
* **Flush count** = number of redirects (taken branches + JAL + JALR + HALT) that
  squashed younger instructions.

The trace records `retire_cycle` in this numbering.

### 6.1 Hand-computed examples

Notation: `f` = IF cycle, and an instruction in WB at cycle c retires at c.

**A. Straight line, 4 instructions (HALT last).**

```
0: addi r1, r0, 1
1: addi r2, r0, 2
2: add  r3, r1, r2
3: halt
```

Fetch 1,2,3,4; retire 5,6,7,8. **Total 8 cycles, 4 instructions, CPI 2.0.**
Formula for N straight-line instructions: **N + 4**.

**B. Load-use pair at distance 1.**

```
0: lw   r1, 0(r2)
1: add  r3, r1, r1     # stalls one cycle in ID
2: add  r4, r4, r4
3: halt
```

| instr | IF | ID | EX | MEM | WB |
|---|---|---|---|---|---|
| 0 | 1 | 2 | 3 | 4 | 5 |
| 1 | 2 | 3 (stall) | 5 | 6 | 7 |
| 2 | 4 | 5 | 6 | 7 | 8 |
| 3 | 5 | 6 | 7 | 8 | 9 |

The IF stage is idle in cycle 3 because the stall freezes IF/ID and the PC.
**Total 9 cycles, 4 instructions, 1 stall, CPI 2.25.** The same program without
the dependency retires its HALT at cycle 8, so the stall costs 1 cycle.

**C. Taken branch over two instructions.**

```
0: beq r1, r2, target    # taken, target = 3
1: addi r3, r3, 1        # squashed
2: addi r4, r4, 1        # squashed
3: target: halt
```

| instr | IF | ID | EX | MEM | WB | retired? |
|---|---|---|---|---|---|---|
| 0 | 1 | 2 | 3 | 4 | 5 | yes |
| 1 | 2 | 3 | - | - | - | no |
| 2 | 3 | - | - | - | - | no |
| 3 | 4 | 5 | 6 | 7 | 8 | yes |

HALT 3 is fetched at 4, ID 5, EX 6, MEM 7, WB 8. **Total 8 cycles, 2 retired
instructions, CPI 4.0** (the branch itself retires at 5). The two squashed
instructions must not change any state. The taken-branch penalty is 2 cycles
relative to a machine that could fetch the target immediately.

**D. Not-taken branch.**

```
0: beq r1, r2, 3    # not taken
1: halt
```

IF 1,2; WB 5,6. **Total 6 cycles, 2 instructions, CPI 3.0.**

**E. JAL.**

```
0: jal r15, 2
1: addi r3, r3, 1   # squashed
2: halt
```

JAL retires at 5, HALT fetched at 4, retires at 8. **Total 8 cycles, 2 instructions.**

**F. JALR (same shape as JAL).** Total 8 cycles; target computed from the old base.

**G. Loop with two taken iterations.**

```
0: addi r1, r0, 3      # limit
1: addi r2, r2, 1      # body, counter
2: bne  r1, r2, 1      # target 1, offset -2
3: halt
```

Dynamic instruction sequence: 0, 1, 2, 1, 2, 1, 2, 3 (8 retirements).

| dynamic instr | IF | ID | EX | MEM | WB |
|---|---|---|---|---|---|
| 0 | 1 | 2 | 3 | 4 | 5 |
| 1a | 2 | 3 | 4 | 5 | 6 |
| 2a | 3 | 4 | 5 | 6 | 7 |
| 1b | 6 | 7 | 8 | 9 | 10 |
| 2b | 7 | 8 | 9 | 10 | 11 |
| 1c | 10 | 11 | 12 | 13 | 14 |
| 2c | 11 | 12 | 13 | 14 | 15 |
| 3 | 12 | 13 | 14 | 15 | 16 |

**Total 16 cycles, 8 instructions, 2 taken branches, CPI 2.0.** Without the two
branch penalties the same 8 instructions would take 12 cycles.

These numbers are asserted by `tools/test_isa_sim.py`; the Phase 5 RTL must
reproduce them exactly.


---

## 7. Peripheral timing (normative; added in Phase 5B)

This section resolves the Phase 5A findings F1-F3.  **The timing layer is
authoritative** for any program that reads `TIMER`, `UART_STATUS` or `GPIO_IN`.
The architectural layer (`run_arch`) reads the same registers but is not a
cycle-accurate statement about them; only `run_pipeline` and the RTL implement
this section.

Cycle numbering is the one in section 6: cycle 1 is the first cycle after reset
release; an instruction fetched in cycle `f` is in MEM at `f+3` and WB at `f+4`.
All peripheral reads below happen in **MEM** and the captured value is available
in WB and for forwarding in the next cycle.

### 7.1 TIMER (`0xFF08`)

**Definition.** A read of TIMER in MEM at cycle `c` returns `c mod 2^16`.  The
counter is 1 in cycle 1, 2 in cycle 2, ..., 65535 in cycle 65535, 0 in cycle
65536, and so on.  A 16-bit read and a byte read both return the corresponding
part of that 16-bit value (`LB` sign-extends, `LBU` zero-extends, per the ISA).

**Wrap.** The counter is modulo 2^16.  A read in a run longer than 65535 cycles
therefore sees the wrapped value; the run's cycle number in the trace is not
wrapped.

**Mid-run reset.** Reset clears the counter to 0.  After release the next cycle is
cycle 1 again and the counter reads 1.  Reset does not preserve the old count.
No read can occur during reset because the pipeline is cleared.

**Hand derivation.** A straight-line program has MEM cycle = `index + 4` for a
program starting at instruction 0 (instruction 0: IF1, MEM4).  For

```
0: li t0, TIMER
1: lw t1, 0(t0)
2: nop
3: lw t2, 0(t0)
4: halt
```

| instr | IF | ID | EX | MEM | WB | TIMER value read |
|---|---|---|---|---|---|---|
| 0 `li` | 1 | 2 | 3 | 4 | 5 | - |
| 1 `lw` | 2 | 3 | 4 | 5 | 6 | **5** |
| 2 `nop` | 3 | 4 | 5 | 6 | 7 | - |
| 3 `lw` | 4 | 5 | 6 | 7 | 8 | **7** |
| 4 `halt` | 5 | 6 | 7 | 8 | 9 | - |

**Total 9 cycles**, `t1 = 5`, `t2 = 7`.  This is asserted by
`tools/test_isa_sim.py::test_timer_mem_cycle_hand_table`.

### 7.2 UART (`0xFF00` TX, `0xFF02` STATUS)

Let `N = UART_CYCLES_PER_BYTE`.  `N >= 1` is the legal hardware range.  `N = 0`
is a documented degenerate setting used only by simulation/fuzz corpus code: the
transmitter is never busy and no write is ever dropped.  It is **not** a hardware
configuration and a synthesis build must not rely on it.

A store to `UART_TX` whose MEM cycle is `c` is an **accepting write** iff the
transmitter is idle in cycle `c` (equivalently, iff no earlier accepting write has
its busy window covering `c`).  An accepting write:

* appends the byte to the output stream;
* makes the transmitter busy for exactly `N` observable cycles: **first busy
  cycle `c+1`, last busy cycle `c+N`, first idle cycle `c+N+1`**;
* the write cycle `c` itself is never observable by a status read, because a
  status read has its own, different MEM cycle.

A store to `UART_TX` whose MEM cycle is `d` while busy (`c+1 <= d <= c+N`) is
**dropped**: the byte never appears in the output stream, the store still
retires and its retirement record is still emitted.  A store at `d = c+N+1` is
accepted.

**Legal N / hand tables.**  Write at MEM cycle `c = 7`; status reads at MEM
cycles `8 = c+1`, `9 = c+2`, `11 = c+4`:

| N | c+1 = 8 | c+2 = 9 | c+4 = 11 | busy window (observable) |
|---|---|---|---|---|
| 1 (min legal) | 1 | 0 | 0 | 8 |
| 2 | 1 | 1 | 0 | 8..9 |
| 10 | 1 | 1 | 1 | 8..17 |
| 104 | 1 | 1 | 1 | 8..111 |

Acceptance boundary, first write at `c = 6`, second write at MEM cycle `d`:

| N | d = c+1 = 7 | d = c+2 = 8 | d = c+3 = 9 |
|---|---|---|---|
| 1 | dropped (7 <= 6+1) | **accepted** (8 = 6+1+1) | accepted |
| 2 | dropped | dropped (8 <= 8) | **accepted** (9 = 6+2+1) |
| 10 | dropped | dropped | dropped |

These two tables are asserted by
`test_uart_busy_window_hand_table`, `test_uart_accept_boundary_hand_table` and
`test_uart_back_to_back_drop`.

**Mid-run reset.** Reset clears the busy countdown; UART_STATUS reads 0 in the
first cycle after release, and a write in that cycle is accepted.

### 7.3 GPIO_IN (`0xFF06`)

The external input is asynchronous and passes through a **two-flop
synchroniser**, so `L = 2`.  Define `ext(t)` as the external 16-bit value that is
stable during cycle `t`.  Then a read of GPIO_IN in MEM at cycle `c` returns:

```
GPIO_IN_read(c) = ext(c - L) = ext(c - 2)          for c > 2
GPIO_IN_read(1) = GPIO_IN_read(2) = 0              (synchroniser reset value)
```

Equivalently `ext(t) = 0` for `t <= 0`.  A byte read returns the low byte of the
observed 16-bit value, with the ISA's `LB`/`LBU` extension rule.

**Reset.** Both synchroniser flops are cleared by reset, so the first two cycles
after release read 0 regardless of the external value.

**Deterministic input schedule (shared by the model and the RTL testbench).**

```
# comment lines and blank lines are ignored
<cycle> <value>
```

`cycle` is a decimal or `0x`-prefixed integer `>= 1`; `value` is a decimal or
`0x`-prefixed integer in `0..0xFFFF`.  Entries are sorted by cycle.  The external
value during cycle `t` is the value of the **last entry with entry_cycle <= t**;
before the first entry it is 0; the last entry holds for all later cycles.
Duplicate cycles are an error.  The loader is
`tools/isa_sim.py::load_gpio_schedule`; the RTL testbench reads the same file
through the `GPIO_SCHEDULE` generic.

**Hand derivation.** Schedule `1 -> 0x1111`, `3 -> 0x2222`, `6 -> 0x3333`:

```
0: li t0, GPIO_IN     MEM 4
1: lw t1, 0(t0)       MEM 5   reads ext(3) = 0x2222
2: nop                MEM 6
3: lw t2, 0(t0)       MEM 7   reads ext(5) = 0x2222
4: nop                MEM 8
5: nop                MEM 9
6: lw t3, 0(t0)       MEM 10  reads ext(8) = 0x3333
7: halt               MEM 11, WB 12
```

**Total 12 cycles**, `t1 = 0x2222`, `t2 = 0x2222`, `t3 = 0x3333`.  If the first
entry were at cycle 5 instead, the MEM-5 read would observe `ext(3) = 0` and the
MEM-7 read would observe `ext(5) = 0xABCD`.  These are asserted by
`test_gpio_in_schedule_latency` and
`test_gpio_in_schedule_before_first_entry`.

### 7.4 Consequences for verification

* `hello`, `05_mmio` and every program that reads a peripheral are now compared
  **cycle-exactly**; the Phase 5A `cycle_sensitive` exclusions are removed.
* The RTL implementation of the UART busy flag must be the combinational
  `remaining > 0`, not a registered flag delayed by one cycle.  The Phase 5A RTL
  registered flag made busy observable for `N+1` cycles and disagreed with the
  status read at `c+N+1`; this is an RTL bug (finding F25) fixed in Phase 5B, not
  a contract change.
* The architectural layer keeps its documented approximation: it reports the
  current-cycle peripheral value.  It is never used for cycle-exact comparison.
