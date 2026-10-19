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

Synthesis will report whether the memories inferred block RAM and what the critical
path is. Do not tune Fmax in Phase 5A.

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

The exact results are in the final report and are not repeated here.
