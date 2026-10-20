# MiniCPU-16 Phase 5A RTL Architecture (as designed)

Status: **Phase 5A design record**. This document is written before the VHDL and is
updated to the as-built state at the end of the phase. It is subordinate to
`docs/ISA.md` and `docs/MICROARCH_CONTRACT.md`; where this document and those
disagree, the ISA wins and the disagreement is a bug recorded in
`docs/RTL_FINDINGS.md`.

Phase 5A implements the frozen ISA as a classic five-stage in-order pipeline with
**stall-only hazard handling** (`FORWARDING = false`). The forwarding muxes and
forwarding unit exist structurally, but in 5A the forwarding unit selects the
ID/EX operand values and the hazard unit stalls. `FORWARDING = true` is
implemented structurally and is **unverified** in 5A.

Phase 5B will enable forwarding and verify cycle exactness. Phase 5A deliberately
does **not** claim cycle-exact agreement with the microarchitecture contract:
stall-only code executes extra stall cycles. Retirement records (sequence, PC,
instruction, register/memory/branch effects) must still agree exactly.

---

## 1. Block diagram

```
                         +-----------------------------------------------+
                         |                minicpu16_core                 |
                         |                                               |
  rst --->+              |   +--------+    +--------+    +--------+      |
          |              |   |  ID    |    |  EX    |    |  MEM   |      |
  PC  --->+--- IF ------>+-->| decode |--->| ALU /  |--->| dmem   |---> WB
          |   imem       |   | RF read|    | branch |    | mmio   |      |
          |   (sync)     |   | hazard |    | JALR   |    | commit |      |
          |              |   +--------+    +--------+    +--------+      |
          |                   ^  ^             ^             ^           |
          |                   |  |             |             |           |
          |              IF/ID  |        ID/EX |       EX/MEM|     MEM/WB|
          |                   +--+-------------+-------------+-----------+---> RETIRE
          |                                                                  (valid,
          |                                                                   pc, instr,
          |                                                                   rd/we/value,
          |                                                                   mem kind/
          |                                                                   addr/size/
          |                                                                   data,
          |                                                                   taken/target)
          |
          +--> uart_tx_byte / uart_tx_valid, gpio_out, uart_busy, pc_out

  Data side:  EX/MEM addr/data --> dmem (two byte banks) / mmio (0xFF00..0xFFFF)
              MEM/WB load data <-- dmem registered output / mmio capture
```

Harvard memories:

* instruction memory: synchronous read, one 32-bit word per instruction index,
  `INSTR_WORDS` entries, out-of-range returns `0x00000000` (HALT);
* data memory: synchronous read, two byte banks (even/odd), `RAM_BYTES` bytes,
  byte enables, word reads align bit 0 down;
* MMIO page 0xFF00..0xFFFF in parallel with the data RAM, resolved in MEM.

---

## 2. Pipeline registers (exact fields)

| Register | Fields | Produced at end of | Consumed in |
|---|---|---|---|
| PC | `pc : u16`, `stop_fetch : bit` | IF | IF |
| IF/ID | `valid`, `pc : u16`, `instr : u32` (the imem output register) | IF | ID |
| ID/EX | `valid`, `pc : u16`, `instr : u32`, `ctrl`, `rs1_val : u16`, `rs2_val : u16` | ID | EX |
| EX/MEM | `valid`, `pc : u16`, `instr : u32`, `ctrl`, `alu_result : u16`, `store_data : u16`, `addr : u16`, `taken`, `target : u16`, `is_halt` | EX | MEM |
| MEM/WB | `valid`, `pc : u16`, `instr : u32`, `ctrl`, `alu_result : u16`, `mem_addr : u16`, `mem_size`, `store_data : u16`, `taken`, `target : u16`, `is_halt`, `mem_is_mmio` | MEM | WB |
| data-memory output | `dmem_rdata : u16` (registered from the byte banks) | MEM | WB |
| MMIO capture | `mmio_rdata_q : u16` (registered combinational MMIO read) | MEM | WB |

`ctrl` contains: `is_halt, is_nop, is_alu, is_load, is_store, is_branch,
is_jal, is_jalr, use_imm, reg_write, mem_read, mem_write, mem_size, rs1_en,
rs2_en, rd, rs1, rs2, op, imm`.

The instruction field of IF/ID **is** the instruction-memory output register. This
is deliberately not a second register: a synchronous BRAM's output register is the
IF/ID instruction register. In cycle `c` the IF stage drives `pc`; at the end of
`c` the imem output register receives `imem(pc)`, and IF/ID valid/PC are loaded
at that same edge. In `c+1` the word is decoded in ID. This gives the contract's
"fetch in `f`, ID in `f+1`" without adding a cycle.

---

## 3. Memory interface timing

### 3.1 Instruction memory

| Cycle | PC | imem address | imem output register | IF/ID valid/pc | Stage |
|---|---|---|---|---|---|
| c | P | P | will become `imem(P)` at the edge ending c | will become `{1,P}` | IF(P) |
| c+1 | P+1 | P+1 | `imem(P)` | `{1,P}` | ID(P), IF(P+1) |
| c+2 | P+2 | P+2 | `imem(P+1)` | `{1,P+1}` | EX(P), ID(P+1) |

* Address is the instruction index, not a byte address.
* The range check is on the registered address (the PC value presented in that
  cycle), not on a combinational path from a next-PC adder into the BRAM address.
  Out of range selects `HALT_WORD` at the output register.
* On redirect the output register holds one or two stale words; their IF/ID valid
  bits are zero and they never enter ID/EX.
* **Stall hold:** while a 5A stall is applied, the imem address is switched from
  the fetch pointer `pc` to `if_id_pc` (the PC of the instruction sitting in
  IF/ID).  The output register therefore re-reads and holds that instruction while
  PC is frozen; re-reading the fetch pointer would replace the ID instruction with
  the younger one.  This is the only special case in the imem address path.  It
  was found by the first CPU testbench run (see `docs/RTL_FINDINGS.md` F18).
* While `stop_fetch=1` the PC is frozen and IF/ID valid is forced zero.

### 3.2 Data memory and MMIO

| Cycle | EX/MEM addr/data | dmem/MMIO action | MEM/WB capture |
|---|---|---|---|
| c (MEM) | stable | read banks or MMIO; store commits at the edge ending c | load data registered at the edge ending c; store trace fields registered |
| c+1 (WB) | next instruction may be in MEM | previous load data is in MEM/WB | register write and retirement record |

* A store commits at the **end of MEM** (not at the EX/MEM boundary). Its write
  data is carried in EX/MEM; the write enable is qualified by the MEM valid bit
  and not by reset.
* A load's data is captured at the end of MEM. For RAM, the byte-bank output
  register is the MEM/WB load-data register. For MMIO, the combinational MMIO read
  is captured into `mmio_rdata_q` at the same edge.
* Because the pipeline is in-order and only one instruction is in MEM, a load and
  a store can never be in MEM in the same cycle. Therefore the BRAM
  read-during-write behaviour is never architecturally visible. A store at
  distance 1 is committed at the end of its MEM cycle, one full cycle before the
  younger load's MEM cycle reads the banks.

---

## 4. Hazard, stall, flush and forward equations

Named signals (all active high unless stated):

```
id_valid, ex_valid, mem_valid, wb_valid
id_src1_en, id_src2_en, id_rs1, id_rs2
ex_reg_write, ex_rd, ex_is_load
mem_reg_write, mem_rd
redirect   = ex_branch_taken or ex_is_jal or ex_is_jalr
halt_ex    = ex_valid and ex_is_halt
```

Source match for the ID instruction (R0 is never a source match):

```
src1_match(rd) = id_src1_en and id_rs1 /= 0 and id_rs1 = rd
src2_match(rd) = id_src2_en and id_rs2 /= 0 and id_rs2 = rd
match(rd)      = src1_match(rd) or src2_match(rd)
```

Producer match:

```
prod_ex  = ex_valid  and ex_reg_write  and ex_rd  /= 0 and match(ex_rd)
prod_mem = mem_valid and mem_reg_write and mem_rd /= 0 and match(mem_rd)
```

5A hazard rule (`FORWARDING = false`):

```
stall_5a = id_valid and not redirect and not halt_ex
           and (prod_ex or prod_mem)
stall    = stall_5a
```

In words: **an instruction in ID stalls while a producer of one of its actual
source registers is in EX or MEM and that producer's rd is not R0.** The actual
source registers depend on the instruction class: R-type uses rs1 and rs2;
I-type ALU, loads and JALR use rs1; stores and branches use rs1 and the [23:20]
register (store data / second branch operand); JAL uses none; HALT, NOP and
unassigned opcodes use none. A producer in WB is covered by the register-file
write-through, so it does not stall.

Consequences in 5A: a distance-1 consumer stalls two cycles (producer in EX, then
MEM, then WB write-through); a distance-2 consumer stalls one cycle (producer in
MEM, then WB); a distance-3 consumer does not stall. This is intentionally more
conservative than the contract, whose forwarding covers EX/MEM and MEM/WB. **5A
cycle counts are not compared with the contract or the golden timing layer.**

The 5B rule (structural, unverified in 5A):

```
stall_5b = id_valid and not redirect and not halt_ex
           and ex_valid and ex_is_load and ex_rd /= 0 and match(ex_rd)
stall    = stall_5b when FORWARDING else stall_5a
```

Flush:

```
flush = redirect or halt_ex
```

Stall is suppressed when `flush` is true: the younger instruction is squashed, so
it must not stall the machine (contract section 3, last paragraph).

PC update:

```
if rst then                       pc' = 0
elsif redirect then               pc' = target
elsif halt_ex then                pc' = pc        (freeze fetch pointer)
elsif stall then                  pc' = pc
elsif stop_fetch then             pc' = pc
else                              pc' = pc + 1 (mod 2^16)
```

IF/ID update:

```
if rst or flush then              valid' = 0
elsif stall then                  valid' = valid, pc' = pc
elsif stop_fetch then             valid' = 0
else                              valid' = 1, pc' = pc
```

ID/EX update:

```
if rst or flush or stall then     valid' = 0        (bubble)
else                              valid' = if_id_valid
                                  pc' = if_id_pc
                                  ctrl' = decode(if_id_instr)
                                  rs1_val' = rf_read1 (with WB write-through)
                                  rs2_val' = rf_read2 (with WB write-through)
```

Forwarding (structural; only active when `FORWARDING = true`):

```
fwd_a = EX/MEM  if ex_valid and ex_reg_write and not ex_is_load
                  and ex_rd /= 0 and ex_rd = id_rs1
        MEM/WB  elsif mem_valid and mem_reg_write
                  and mem_rd /= 0 and mem_rd = id_rs1
        ID/EX   otherwise
fwd_b = same with id_rs2
```

EX/MEM is younger than MEM/WB and wins. A load in EX/MEM does not forward; the
load-use hazard guarantees the consumer is not in EX at that time when
`FORWARDING=true`. R0 is never forwarded.

Register-file write-through:

```
rdata1 = 0                                  if raddr1 = 0
         wb_data                            if wb_we and wb_rd = raddr1
         regs(raddr1)                      otherwise
rdata2 = the same for raddr2
wb_we  = mem_wb_valid and ctrl.reg_write and ctrl.rd /= 0 and not rst
```

The actual register write occurs at the rising edge ending WB; the same-cycle ID
read sees `wb_data` through the read mux (write-through). This covers distance 3
in both 5A and 5B.

---

## 5. Cycle-by-cycle scenarios

Notation: `f` is the IF cycle of the first instruction; `*` marks a stall cycle.
"5A" is the implemented stall-only machine. Where the contract's forwarding
machine differs, both are shown. Retirement is at the end of WB.

### (a) STORE followed by LOAD at distances 1, 2 and 3

The store writes at the end of its MEM cycle. The load reads at the end of its own
MEM cycle. The load's MEM cycle is always later than the store's MEM cycle for
distances >= 1, so the load sees the new value. No bypass and no stall is needed.
The same holds for SB followed by LW (byte lane updated, other lane old), SW
followed by LB (both banks written), and LW/SW with address bit 0 set (both align
down to the same word).

Distance 1:

```
instr        IF  ID  EX  MEM  WB
sw  (s)      f   f+1 f+2 f+3  f+4   write commits end f+3
lw  (s+1)    f+1 f+2 f+3 f+4  f+5   read captures end f+4 -> new value
```

No stall: the store has no destination register.

Distance 2:

```
sw           f   f+1 f+2 f+3  f+4
nop          f+1 f+2 f+3 f+4  f+5
lw           f+2 f+3 f+4 f+5  f+6   read end f+5 -> new value
```

Distance 3:

```
sw           f   f+1 f+2 f+3  f+4
nop          f+1 f+2 f+3 f+4  f+5
nop          f+2 f+3 f+4 f+5  f+6
lw           f+3 f+4 f+5 f+6  f+7   read end f+6 -> new value
```

Contract/golden check: the golden timing layer performs the store in MEM and the
load in a later MEM; its result is identical. The RTL design reproduces it. The
"old value on read-during-write" property of the byte banks is not exercised
because no load and store share a MEM cycle.

### (b) Load-use at distance 1, all consumer forms

The load is in EX while the consumer is in ID. With `FORWARDING=false` the
consumer stalls in ID until the load reaches WB, then reads through. Two stalls
are applied. The same cycle shape applies whether the consumer uses the loaded
value as ALU operand A, ALU operand B, store address (rs1), store data (rs2),
branch operand, or JALR base.

```
instr        IF  ID   EX  MEM  WB
lw  (p)      f   f+1  f+2 f+3  f+4
consumer     f+1 f+2* f+3* f+4(reads WB) f+5 f+6 f+7
                   ^    ^   ^
                   |    |   +-- ID sees load value by write-through; no stall
                   |    +------ producer now in MEM; still stall in 5A
                   +----------- producer in EX; stall
```

Consumer stages after the stalls: ID f+4, EX f+5, MEM f+6, WB f+7.

Contract/golden check: the contract's forwarding machine stalls only one cycle
(ID f+2), then forwards the load from MEM/WB in EX at f+4; consumer WB f+6. This
is **different by design** in 5A and is not compared cycle-for-cycle. The
retirement trace (including the load result) is identical.

### (c) Taken branch / JAL / JALR flush

A redirect resolved in EX at cycle `t` kills the instructions in ID and IF. Those
are the two younger instructions. They never enter EX/MEM or MEM/WB, so they can
never write a register or memory and never retire. The target is fetched in
`t+1` and reaches ID in `t+2`.

```
instr          IF  ID  EX  MEM  WB
branch/jump    t-2 t-1 t   t+1  t+2   retires
shadow 1       t-1 t   -   -    -     killed in ID
shadow 2       t   -   -   -    -     killed in IF
target         t+1 t+2 t+3 t+4  t+5   retires
```

The instruction-memory output register at the end of `t` still captures a word for
the old (sequential) PC, and at the end of `t+1` captures `imem(target)`.
During `t+1` the IF/ID valid bit is zero; the stale word is ignored. The target
word is in ID in `t+2`, exactly the contract's penalty of two cycles.

Shadow contents, all killed:

* **another branch**: never reaches EX, so it cannot redirect or flush;
* **a load**: no MEM access, no trace, no register write;
* **a store**: no MEM write, no MMIO write, no UART byte;
* **HALT**: `halt_ex` is computed from ID/EX (the branch/JAL/JALR), never from
  IF/ID, so a HALT in the shadow does **not** stop fetch. The branch's redirect
  wins.

JAL and JALR use the same flush; the link value is carried in EX/MEM and written
in WB. A taken branch and a load-use stall in the same cycle: the ID instruction is
killed, so no stall is applied (`flush` suppresses `stall`).

### (d) HALT

```
instr        IF  ID  EX  MEM  WB
HALT         f   f+1 f+2 f+3  f+4   retires; stop_fetch set in EX
younger 1    f+1 f+2 -   -    -     killed in ID
younger 2    f+2 -   -   -    -     killed in IF
```

When HALT is in EX, `stop_fetch` is set, PC holds, IF/ID valid and ID/EX valid are
cleared. HALT advances through EX/MEM and MEM/WB and retires normally. No younger
instruction retires and none writes a register, RAM, MMIO or UART. The PC freezes at
the fetch pointer value; the final architectural PC reported in the trace is the
HALT's own PC, as in the golden model. A HALT in the shadow of a taken branch is
killed (see (c)) and does not take effect.

### (e) Reset

Reset is synchronous, active high.

```
cycle            -3   -2   -1    1      2      3      4      5
rst               1    1    1    0      0      0      0      0
PC                0    0    0    0      1      2      3      4
IF/ID valid       0    0    0    1(instr0) 1(instr1) ...
ID/EX valid       0    0    0    0      1(instr0) ...
stop_fetch        0    0    0    0      0      0      0      0
```

While `rst=1`: PC=0, all valid bits=0, stop_fetch=0, no RF write, no RAM write, no
MMIO write, no UART start; GPIO_OUT=0, TIMER=0, UART busy=0. Registers and RAM are
**not** cleared. After reset is released before a rising edge, cycle 1 fetches PC 0;
instruction 0 is in ID in cycle 2 and retires in cycle 5. Pipeline registers after
reset: valid bits 0, PC 0, stop_fetch 0, other fields don't-care (initialised to 0
for simulation determinism).

The register file and RAM are initialised at power-on (FPGA initialisation / TB
image), not by reset. A warm reset preserves them.

### (f) MMIO

Reads and writes happen in MEM, with the same timing as data RAM. The address is
presented during MEM cycle `c`; a read is captured at the end of `c`; a write
commits at the end of `c`.

| Cycle | Action |
|---|---|
| c (MEM) | MMIO address decode; UART_TX write starts if not busy; GPIO_OUT write; MMIO read mux |
| c+1 (WB) | load result in MEM/WB (LB/LBU extended, LW full); store retirement record |

TIMER sample point: a TIMER read in MEM cycle `c` returns `c` (cycle 1 is the
first fetch after reset), modulo 2^16, matching the golden timing layer's
`m.cycle`. The RTL keeps a 16-bit cycle counter that is 0 after reset and a
combinational `timer_value = cycle_count + 1`.

UART_TX: a write to 0xFF00/0xFF01 (word or byte) transmits the low 8 bits if the
transmitter is not busy at that MEM cycle; otherwise the byte is dropped. The
transmitter is busy for the next `UART_CYCLES_PER_BYTE` cycles. UART_STATUS bit 0
is the busy flag; bits 15:1 are zero. Reads of UART_TX return 0.

GPIO_OUT: word write replaces all 16 bits; byte write replaces the low byte and
preserves the high byte; byte read returns the low byte; word read returns all 16.
GPIO_IN: read-only, two-flop synchroniser; word or low byte. Writes ignored.
TIMER: read-only; word or low byte; writes ignored.
MMIO_RESERVED (0xFF0A..0xFFFF): reads 0, writes ignored.
Byte accesses ignore address bit 0 and select `addr & 0xFFFE`; LB/LBU return the
low 8 bits only (never the high byte), sign/zero extended; SB writes the low 8 bits
into the low byte. A word access also ignores bit 0.

Cycle-sensitive MMIO programs (TIMER reads, UART_STATUS polling, GPIO_IN reads) are
excluded from 5A differential comparison; see the final report and
`docs/RTL_FINDINGS.md`.

### (g) Fetch beyond INSTR_WORDS and PC wrap

* The instruction memory compares the registered address with `INSTR_WORDS`. If
  `addr >= INSTR_WORDS`, the output register is loaded with `0x00000000` (HALT).
  The comparator is not in series with the BRAM address: the address goes directly
  to the BRAM, and the range decision selects the output register's data.
* A zero word is HALT. So zero-filled or uninitialised instruction memory halts.
* The PC is 16 bits and `pc+1` wraps 0xFFFF -> 0x0000. Branch/JAL targets are
  computed modulo 2^16. A 65536-word image can execute across the wrap; with the
  default 2048-word image, any index >= 2048 (including 0xFFFF) fetches HALT.
* A program that runs off its end fetches HALT from the zero-filled image.

### (h) Dependencies on R0

* The register file returns 0 for address 0 and suppresses writes to address 0
  (including loads and JAL/JALR links).
* The hazard unit checks `id_rs /= 0` and `producer rd /= 0`; a producer that
  writes R0 never stalls a consumer, and a consumer that reads R0 never stalls.
* The forwarding unit never forwards R0.
* A load to R0 still performs the memory read and appears in the trace with
  `rd=-`; the loaded value is discarded. JAL/JALR to R0 still jump and retire
  with `rd=-` and no link write.

### (i) JALR with rd == rs1, and back-to-back JAL/JALR

JALR computes `target = old_rs1 + sext(imm16)` in EX from the value captured in
ID/EX, and `link = pc+1`. The link travels in EX/MEM and is written in WB, so it
cannot feed back into the target adder. This is the ISA's only defined behaviour.
A back-to-back JAL/JALR is a redirect followed by a shadow jump: the first
redirects in EX, the second is in ID and is killed. Its link is never written. A
consumer of the first link stalls until the link reaches WB (5A).

---

## 6. Module file plan

| File | Contents |
|---|---|
| `rtl/minicpu16_pkg.vhd` | constants, opcodes, memory-map constants, unconstrained image types, control record, coverage indices |
| `rtl/register_file.vhd` | 16x16, R0 zero, two async reads, WB->ID write-through, reset-gated write |
| `rtl/shifter.vhd` | SLL/SRL/SRA, amount low 4 bits |
| `rtl/alu.vhd` | add/sub/logic/compare; instantiates the shifter |
| `rtl/branch_unit.vhd` | BEQ/BNE/BLT/BGE/BLTU/BGEU |
| `rtl/decoder.vhd` | instruction decode to `ctrl_t` |
| `rtl/imem.vhd` | synchronous instruction ROM, INSTR_WORDS, HALT out of range |
| `rtl/dmem.vhd` | two byte banks, byte enables, LB/LBU/LW alignment/extension, unmapped |
| `rtl/mmio.vhd` | UART_TX/STATUS, GPIO_OUT/IN, TIMER, reserved, byte rules |
| `rtl/hazard_unit.vhd` | 5A stall rule and structural 5B load-use rule |
| `rtl/forwarding_unit.vhd` | structural EX/MEM and MEM/WB select equations |
| `rtl/minicpu16_core.vhd` | five-stage top, memories, MMIO, retirement port, coverage counters |
| `rtl/tb/cpu_tb.vhd` | program/data image loader, trace writer, final state, watchdog |
| `rtl/tb/tb_*.vhd` | one self-checking testbench per leaf module |

---

## 7. Critical-path predictions (to be checked by synthesis in Step 7)

These are predictions, not measurements:

1. **Forwarded operand -> branch comparator -> next-PC mux -> instruction BRAM
   address.** In 5B the EX/MEM or MEM/WB forwarding mux feeds the branch unit, the
   target adder and the PC mux, then the BRAM address. In 5A the same path exists
   from the ID/EX operand register; forwarding only adds a mux in front.
2. **ALU -> data BRAM address.** Address adder/ALU result -> EX/MEM register ->
   dmem address -> byte-bank address. The byte-bank address is registered inside
   dmem, so the path is register-to-register through the address adder.
3. **WB write-through -> ID/EX operand register -> ALU.** In 5A this path is
   exercised on every distance-3 dependency and after each load-use stall.
4. **Instruction-memory output -> decode -> hazard -> PC/valid.** The decode of the
   imem output feeds the hazard unit and the IF/ID/ID-EX enables. This is a
   control path, not a datapath, but it may be timing-relevant.
5. **MMIO address decode -> UART/TIMER/GPIO read mux -> MEM/WB.** The MMIO read mux
   is combinational in MEM and is captured at the MEM/WB edge.

Measured in the Step 7 smoke test (not tuned; estimate, seed 1, target 50 MHz):

| Device | Fmax estimate | Critical path reported by nextpnr |
|---|---:|---|
| iCE40 HX8K (ct256) | 59.22 MHz, PASS at 50 | instruction BRAM `RDATA` -> imem output/decode logic |
| iCE40 UP5K (sg48) | 25.58 MHz, FAIL at 50 | `b_ex` (EX operand register) -> `ex_mem_result` (ALU/address path) |

The predicted forwarded-operand -> branch -> PC path was **not** the reported
critical path for this seed in 5A (forwarding is disabled).  The prediction is
still the one to re-check in 5B.  The HX8K path is the instruction-memory output
feeding decode/control; the UP5K path is an EX operand to the EX/MEM result.  The
design was not tuned for Fmax in this phase.

Synthesis inferred 24 `SB_RAM40_4K` (16 instruction + 8 data) in the wrapper with a
non-constant image, plus 1263 LUT4 and 611 flip-flops.  The true default core
(all-zero HALT image) constant-folds to 0 BRAM; see `docs/RTL_FINDINGS.md` F23/F24.

---

## 8. Verification plan (as built)

* Leaf self-checking testbenches for every module, run under NVC and GHDL.
* CPU testbench with elaboration-time hex image loading, exact v1 trace output,
  final-state output, cycle budget and wall-clock watchdog.
* Differential runs: 5 reference traces, all benchmarks except cycle-sensitive
  ones, and hand-written directed tests for scenarios (a)-(i).
* Seeded differential fuzzing: >=300 programs on NVC, >=50 on GHDL, full
  retirement-trace comparison with cycles ignored.
* Mutation check for the 12 listed bugs.
* Synthesis inference smoke test with GHDL + ghdl-yosys-plugin + Yosys, time-boxed.

## 9. As-built notes (end of Phase 5A)

* All leaf modules and the core are implemented in the files listed in section 6.
* The synchronous imem output register is the IF/ID instruction field; a stall
  holds it by re-reading `if_id_pc` (F18).  A redirect lets it hold one stale word
  with valid=0, as designed.
* The CPU testbench checks that no instruction retires and the fetch PC does not
  move for two cycles after HALT.  This closes a verification gap found by the
  mutation check (F20).
* Image loading uses an impure textio function at elaboration and a generic
  `instr_image_t`/`byte_image_t`; it works identically in NVC and GHDL, so the
  package-per-program fallback was not needed (F21).
* `make test` runs the Phase 4 suite, all leaf testbenches on NVC and GHDL, the
  quick differential suite and a quick fuzz.  `make rtl-fuzz`, `make mutation`
  and `make synth-smoke` are separate, longer targets.
* DMEM inference required splitting the two byte banks into two clocked processes
  with unconditional registered reads; see `docs/RTL_FINDINGS.md` F23.  The core
  wrapper now synthesises 24 `SB_RAM40_4K` (16 instruction, 8 data).
* The true default core (one-word HALT image) constant-folds to 0 BRAM; the
  synthesis smoke test reports both that case and a non-constant-image wrapper
  (F24).

Measured Phase 5A results (NVC unless stated):

* leaf testbenches: 11 modules x 2 simulators = 8,023,388 checks, 0 failures;
* reference traces 01-04 and all 10 non-cycle-sensitive benchmarks: architectural
  retirement trace identical, retire cycles intentionally different;
* 29 directed programs (scenarios a-i, plus the 65536-word PC-wrap case): all
  match the golden model and their hand-computed final states;
* full fuzz: 300 random + 12 curated programs on NVC, 50 on GHDL, 0 mismatches,
  every coverage counter non-zero;
* 12/12 mutations detected;
* synthesis: 24 `SB_RAM40_4K`, 1263 LUT4, 611 FFs; HX8K 59.22 MHz (PASS at 50),
  UP5K 25.58 MHz (FAIL at 50), seed 1, no tuning.

The exact per-case numbers are in the final report; they are not repeated here.


---

# Phase 5B RTL Architecture (as built)

Status: **Phase 5B design record, verified cycle-exactly in simulation**.  This
section is subordinate to `docs/ISA.md` and `docs/MICROARCH_CONTRACT.md`; where
they disagree, the ISA wins and the disagreement is a bug in
`docs/RTL_FINDINGS.md`.  The Phase 5B machine is the same five-stage pipeline
with forwarding enabled by default (`FORWARDING = true`); `FORWARDING = false`
remains as a regression configuration and is compared architecturally only.

## 6. Forwarding network

The forwarding unit's producer inputs are the instructions in **MEM** (the
EX/MEM register) and **WB** (the MEM/WB register).  Its consumer inputs are the
source-register numbers of the instruction in **EX** (the ID/EX register).  The
muxes select the EX operands:

```
a_ex = ID/EX.rs1_val | EX/MEM.result | WB value
b_ex = ID/EX.rs2_val | EX/MEM.result | WB value
```

`WB value` is the same value the register file would write: the load data after
byte selection and sign/zero extension, the MMIO read data, or the ALU/link
result.

### 6.1 Producer x consumer x distance

| Producer (class) | Produced value in EX/MEM | Produced value in MEM/WB | Into consumer operand at distance 1 | distance 2 | distance 3 |
|---|---|---|---|---|---|
| ALU (ADD..SLTU, ADDI..SLTIU) | `result_ex = alu_result` | `mem_wb_result` | EX/MEM -> EX | MEM/WB -> EX | WB -> ID write-through |
| Load (LB/LBU/LW) | **not forwarded** (only the address is there) | `wb_value` = aligned/extended data | **not forwarded; 1-cycle stall** | MEM/WB -> EX | WB -> ID write-through |
| MMIO read (load from 0xFFxx) | **not forwarded** | `mmio_rdata_q` (the captured read) | **not forwarded; 1-cycle stall** | MEM/WB -> EX | WB -> ID write-through |
| JAL / JALR link | `result_ex = pc+1` | `mem_wb_result` | EX/MEM -> EX | MEM/WB -> EX | WB -> ID write-through |
| Store | writes no register | - | - | - | - |
| HALT / NOP / unassigned | no `reg_write` | - | - | - | - |

Consumer operands and the same forwarding mux:

| Consumer class | Operand | Uses mux |
|---|---|---|
| R-type ALU | rs1, rs2 | `a_ex`, `b_ex` |
| I-type ALU | rs1 | `a_ex` (b is the immediate) |
| Load | rs1 (base) | `a_ex` |
| Store | rs1 (address), rs2 (store data, bits [23:20]) | `a_ex`, `b_ex` |
| Branch | rs1, rs2 | `a_ex`, `b_ex` into the comparator |
| JALR | rs1 (base) | `a_ex` into the target adder |
| JAL | none | - |

### 6.2 Priority, R0, valid bits

* **Youngest producer wins.**  EX/MEM is younger than MEM/WB, so the EX/MEM
  match is tested first.  The double-producer case (same `rd` in both stages)
  therefore forwards the EX/MEM value.  A directed test
  (`j_double_producer`) sets `r1=5`, then `r1=6`, then computes `r2=r1+r1` and
  checks `r2=12`, not 10.
* **R0 is never forwarded and never written.**  A source of R0 reads the
  hardwired zero; the forwarding unit tests `reg /= "0000"` before matching and
  the register file suppresses R0 writes.  `j_r0_producer` and
  `j_load_r0_forward` check this.
* **Valid bits.**  A producer is eligible only when its stage valid bit is 1
  (`reg_write` alone is not enough).  A bubble or a flushed instruction has
  valid=0 and cannot forward.  This is what makes a taken branch/JAL/JALR/HALT
  in EX safe: the flushed younger instruction never supplies an operand.
* **No load forward from EX/MEM.**  The load data does not exist until the end
  of MEM.  The hazard unit guarantees that a consumer of a load at distance 1 is
  not in EX while the load is in EX/MEM; it is stalled in ID for one cycle and
  then receives the MEM/WB value.

### 6.3 Load-use stall rule (forwarding configuration)

Let the instruction in EX be a valid load with `rd /= 0`, and let the valid
instruction in ID read `rd` in any architectural source position:

| ID consumer | Sources checked |
|---|---|
| R-type ALU | rs1, rs2 |
| I-type ALU / load / JALR | rs1 |
| SB / SW | rs1 (address) **and** rs2 (store data) |
| Branch | rs1, rs2 |
| JAL | none |
| HALT / NOP / unassigned | none |

Then stall for **exactly one cycle**: PC holds, IF/ID holds, ID/EX receives a
bubble, EX/MEM and MEM/WB advance.  At distance 2 the load is in WB and is
forwarded through MEM/WB, so no stall.  At distance 3 the value is read through
the WB->ID write-through, so no stall.  Store **data** is explicitly included:
`j_load_store_addr_d1`, `b_load_use_store_data`, `j_load_branch_d1`,
`j_load_jalr_d1` and `j_pointer_chain` each show one stall.

### 6.4 WB -> ID write-through

The register file has two combinational read ports and one write port.  When
the instruction in WB writes the same register that ID reads in the same cycle,
the read mux returns `wb_value` instead of the old array value.  R0 always reads
zero.  This covers distance 3.  The write itself still happens at the rising
edge ending WB.

### 6.5 Stall and flush together

**Flush wins.**  `redirect = ex_branch_taken or ex_is_jal or ex_is_jalr` and
`halt_ex = ex_valid and ex_is_halt`.  The hazard unit gates the stall with
`redirect = 0 and halt_ex = 0`.  The core's ID/EX update is
`bubble if redirect or halt_ex or stall`; PC takes the redirect target (or is
frozen on HALT) before the stall branch.

Proof for the critical case (taken branch/JAL/JALR in EX while ID holds a
dependent or stalled instruction): let the redirecting instruction be in EX in
cycle `t`.  The instruction in ID at `t` is younger and is in the shadow; the
contract says it must not commit and must not hold the machine.  Since
`redirect=1`, the hazard unit forces `stall=0`; PC <- target at the end of `t`;
IF/ID valid <- 0 and ID/EX valid <- 0.  The next fetch is the target at `t+1`.
HALT in EX has the same effect except PC is frozen rather than redirected.
A HALT in the shadow of a taken branch/JAL is therefore squashed; directed
tests `c_branch_shadow_halt`, `c_jal_shadow` and the generator's HALT-in-shadow
template assert this.

### 6.6 Instruction memory under stall and flush

The imem output register is the IF/ID instruction register.  On a **stall** the
imem address is switched from the fetch pointer `pc` to `if_id_pc`, so the
output register re-reads and holds the ID instruction while PC is frozen.  On a
**flush** the imem address is the new PC (or the frozen PC after HALT); the
first one or two output words are stale, but IF/ID valid is forced to 0 during
the flush and they never enter ID/EX.  This is the F18 fix from Phase 5A and is
unchanged in 5B.

### 6.7 Memory address and data timing

The effective address is computed in EX (`a_ex + imm`) and carried in EX/MEM.
It is presented to dmem/MMIO during MEM.  Store data is forwarded in EX, carried
in EX/MEM, and written at the end of MEM.  A load's data is captured at the end
of MEM (RAM bank output register or MMIO capture register) and is available in
WB and for forwarding in the next cycle.  No load and store can be in MEM in the
same cycle, so read-during-write is never architecturally visible.

## 7. Cycle-by-cycle tables for the contract examples

Notation: cycle 1 is the first fetch after reset.  Retire is the WB cycle.
`-` = squashed / not in the pipeline.

### 7.1 Contract A: straight line, 4 instructions

| instr | IF | ID | EX | MEM | WB |
|---|---|---|---|---|---|
| 0 addi | 1 | 2 | 3 | 4 | 5 |
| 1 addi | 2 | 3 | 4 | 5 | 6 |
| 2 add | 3 | 4 | 5 | 6 | 7 |
| 3 halt | 4 | 5 | 6 | 7 | 8 |

8 cycles, 4 instructions.

### 7.2 Contract B: load-use at distance 1

| instr | IF | ID | EX | MEM | WB |
|---|---|---|---|---|---|
| 0 lw | 1 | 2 | 3 | 4 | 5 |
| 1 add (stall) | 2 | 3*, 4 | 5 | 6 | 7 |
| 2 add | 4 | 5 | 6 | 7 | 8 |
| 3 halt | 5 | 6 | 7 | 8 | 9 |

9 cycles, 1 stall.  The forward is MEM/WB -> EX in cycle 5.

### 7.3 Contract C: taken branch

| instr | IF | ID | EX | MEM | WB |
|---|---|---|---|---|---|
| 0 beq (taken) | 1 | 2 | 3 | 4 | 5 |
| 1 shadow | 2 | 3 | - | - | - |
| 2 shadow | 3 | - | - | - | - |
| 3 halt | 4 | 5 | 6 | 7 | 8 |

8 cycles, 2 retired.

### 7.4 Contract D: not-taken branch

| instr | IF | ID | EX | MEM | WB |
|---|---|---|---|---|---|
| 0 beq | 1 | 2 | 3 | 4 | 5 |
| 1 halt | 2 | 3 | 4 | 5 | 6 |

6 cycles.

### 7.5 Contract E/F: JAL / JALR

| instr | IF | ID | EX | MEM | WB |
|---|---|---|---|---|---|
| 0 jal/jalr | 1 | 2 | 3 | 4 | 5 |
| 1 shadow | 2 | 3 | - | - | - |
| 2 target halt | 4 | 5 | 6 | 7 | 8 |

8 cycles; the link value (PC+1) is written by forwarding through EX/MEM or
MEM/WB when the next instruction consumes it (`j_jal_link_next`: 3 retired,
9 cycles).

### 7.6 Contract G: loop with two taken branches

Same table as the contract: dynamic instructions 0,1,2,1,2,1,2,3 retire at
5,6,7,10,11,14,15,16; **16 cycles**, 8 retired.

## 8. Cycle tables for the Step 3 scenarios

All tables are for the forwarding configuration and are the expectations in
`tests/directed/j_*.asm` / `.expected`.

### 8.1 ALU -> ALU at distances 1, 2, 3

| distance | producer | consumer | mechanism | example cycles |
|---|---|---|---|---|
| 1 | EX/MEM | EX | EX/MEM -> EX | `j_alu_alu_d1`: 3 instr, 7 cycles |
| 2 | MEM/WB | EX | MEM/WB -> EX | `j_alu_alu_d2`: 4 instr, 8 cycles |
| 3 | WB | ID | WB -> ID write-through | `j_alu_alu_d3`: 5 instr, 9 cycles |

No stall in any case.

### 8.2 Load -> ALU at distances 1, 2, 3

| distance | producer | consumer | mechanism | example |
|---|---|---|---|---|
| 1 | EX/MEM | EX | 1 stall, then MEM/WB -> EX | `j_load_alu_d1`: 6 instr, 11 cycles, 1 stall |
| 2 | MEM/WB | EX | MEM/WB -> EX | `j_load_alu_d2`: 7 instr, 11 cycles |
| 3 | WB | ID | WB -> ID write-through | `j_load_alu_d3`: 8 instr, 12 cycles |

### 8.3 Load -> store data / store address / branch / JALR

| consumer | example | stalls | cycles |
|---|---|---|---|
| store data | `b_load_use_store_data` | 1 | 12 |
| store address | `j_load_store_addr_d1` | 1 | 13 |
| branch operand | `j_load_branch_d1` | 1 | 14 (taken) |
| JALR base | `j_load_jalr_d1` | 1 | 14 |
| pointer chase (load->load base) | `j_pointer_chain` | 1 | 13 |

### 8.4 Double producer (EX/MEM vs MEM/WB priority)

`j_double_producer`: `li r1,5; addi r1,r1,1; add r2,r1,r1` -> when the add is in
EX, EX/MEM holds 6 and MEM/WB holds 5.  EX/MEM wins, `r2=12`.  Cycle 1..8, no
stall.

### 8.5 JAL link consumed by the next instruction

`j_jal_link_next`: `jal r15,.+1` redirects to PC+1 (the next instruction), which
is squashed and re-fetched.  When it is in EX, the JAL is in MEM (EX/MEM) and
the link 1 is forwarded.  `r1 = 1+1 = 2`; 3 retired, 9 cycles.

### 8.6 Store -> load at distances 1..4, byte/word combinations

Stores commit at the end of MEM; a younger load's MEM is always later.  No
bypass or stall is needed.

| distance | SB then LB/LBU | SW then LB | SB then LW | SW then LW |
|---|---|---|---|---|
| 1 | `j_sb_lw_d1` | `a_sw_lb` | `a_sb_lw` | `a_store_load_d1` |
| 2 | generator | `a_misaligned` variant | - | `a_store_load_d2` |
| 3 | generator | - | - | `a_store_load_d3` |
| 4 | generator | - | - | `j_store_load_d4` |

`j_sw_lb_d1` checks both byte lanes after an SW: `lb 0` = 0xFFCD, `lb 1` =
0xFFAB, `lbu 0` = 0x00CD.

### 8.7 HALT with a load in flight

`j_halt_load_in_flight`: `lw r3,0(r1); halt`.  HALT reaches EX while the load is
in MEM; the load completes and retires, HALT retires at cycle 9, and fetch is
frozen.  5 retired, 9 cycles.

### 8.8 Pointer-chasing load chain

`j_pointer_chain`: load `r3 = 0x0102, then `lw r4,0(r3)`.  The second load is at
distance 1 and stalls one cycle; `r4 = 0x55`, 8 retired, 13 cycles.

## 9. Predicted critical paths (checked in Step 8)

1. **Forwarding mux -> ALU -> branch/target adder -> PC**: the EX/MEM result
   mux feeds the ALU and the JALR/branch adder, then the PC.  This should be the
   longest combinational path in the forwarding configuration.
2. **dmem bank output register -> WB mux -> register-file write data** (and the
   parallel MEM/WB forwarding mux): a shorter RAM-to-register path.
3. **Hazard/forwarding compare (4-bit equality) -> operand mux select**: short,
   but shared by both operands.
4. **imem address mux (stall) -> BRAM address**: the stall mux adds one LUT
   level before the instruction BRAM.
5. **MMIO read mux (UART_STATUS/GPIO_IN/TIMER) -> MEM/WB capture**: a wide mux
   but registered at the end of MEM.

The Step 8 nextpnr reports are compared against these predictions.

## 10. Post-synthesis simulation (optional)

`tools/postsynth_sim.py` (Make target `postsynth-sim`) maps the forwarding
core with Yosys + ghdl-yosys-plugin and runs the built-in `sim` command,
asserting the gate-level `gpio_out` and HALT against the golden model for
`tests/directed/l_postsynth.asm`.  It uses generic technology mapping rather
than the iCE40 cell netlist because the open `SB_RAM40_4K` model does not load
the synthesized INIT parameters (finding F31).  The check is non-vacuous: the
operand-B forwarding mutation makes it fail.  It is optional and not part of
`make test`.