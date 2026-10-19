# MiniCPU-16 Instruction Set Architecture (ISA) v1.0

Status: **FROZEN** after Step 3 (git tag `isa-v1-frozen`). Any change requires updating
this document, `rtl/minicpu16_pkg.vhd`, the assembler, disassembler, golden model,
tests and consistency checker together.

MiniCPU-16 is a 16-bit, load/store, register-register machine with a fixed 32-bit
instruction word and a word-granular program counter. It is designed to be
implemented as a classic 5-stage pipeline in Block RAM based FPGAs, and to be a
comfortable target for a small C compiler where `int` is 16 bits.

---

## 1. Terminology

| Term | Meaning |
|---|---|
| **word** | 16 bits of data. All registers, data addresses and arithmetic values are one word. |
| **instruction** | One 32-bit instruction word (8 bytes on a byte-addressed medium). |
| **PC** | Program counter, 16 bits. It counts **instructions**, not bytes. |
| **instruction index** | The value held by the PC. Code labels evaluate to instruction indices. |
| **byte address** | A 16-bit address into the data memory space. Data labels evaluate to byte addresses. |
| **sext(x)** | Sign-extend the 16-bit value `x` (already a 16-bit pattern) to a mathematical integer. |
| **zext(x)** | Interpret the 16-bit pattern `x` as an unsigned integer. |
| **mod 2^16** | Arithmetic wrapped into the range 0..65535 (two's complement for signed interpretation). |

There are **no byte-sized registers and no halfword instructions**. Memory is
byte-addressed, but every register holds a 16-bit word.

---

## 2. Register file

* 16 registers, `R0`..`R15`, each 16 bits.
* **R0 is hardwired to zero**: reads always return 0x0000; writes are accepted by the
  decoder but have no effect. R0 is still a normal source register.
* Architectural power-on value of **every** register is 0x0000 (FPGA register
  initialisation values, not reset-gated clearing). Reset does **not** clear the
  registers; a warm reset preserves them. Software must not rely on power-on zero
  except where this document explicitly allows it (the ABI's crt0 does not rely on it;
  it sets SP explicitly).
* There is **no flags register**. Comparisons are value-producing (SLT/SLTU) or
  fused into branches (BEQ/BNE/BLT/BGE/BLTU/BGEU).

ABI names are defined in `docs/ABI.md`. Architectural names used in this document
are `R0`..`R15`.

<!-- BEGIN REGISTER_FILE -->
| Register | Architectural name | Power-on | Reset behaviour |
|---|---|---|---|
| 0 | R0 (zero) | 0x0000 | reads 0, writes ignored, never changes |
| 1-15 | R1..R15 | 0x0000 | preserved by reset |
<!-- END REGISTER_FILE -->

---

## 3. Instruction formats

Every instruction is exactly 32 bits. There are five layouts. The opcode is always
in bits 31:24, which lets the decoder start immediately.

```
R-type (register-register)
 31    24 23   20 19   16 15   12 11              0
+--------+------+------+------+------------------+
| opcode |  rd  | rs1  | rs2  | 0000 0000 0000   |
+--------+------+------+------+------------------+

I-type (immediate, loads, JALR)
 31    24 23   20 19   16 15                    0
+--------+------+------+------------------------+
| opcode |  rd  | rs1  |        imm16           |
+--------+------+------+------------------------+

S-type (stores: rs2 holds the store data)
 31    24 23   20 19   16 15                    0
+--------+------+------+------------------------+
| opcode | rs2  | rs1  |        imm16           |
+--------+------+------+------------------------+

B-type (branches: rs2 is the second compare operand)
 31    24 23   20 19   16 15                    0
+--------+------+------+------------------------+
| opcode | rs2  | rs1  |        imm16           |
+--------+------+------+------------------------+

UJ-type (JAL: rd holds the link, imm16 the target offset)
 31    24 23   20 19   16 15                    0
+--------+------+------+------------------------+
| opcode |  rd  | 0000 |        imm16           |
+--------+------+------+------------------------+
```

The all-zero word `0x00000000` is HALT. The canonical NOP is `ADD R0,R0,R0`
(`0x01000000`).

### 3.1 Field positions (machine readable)

<!-- BEGIN FIELDS -->
| Field | MSB | LSB | Notes |
|---|---|---|---|
| OPCODE | 31 | 24 | primary opcode |
| RD | 23 | 20 | destination register, and store data / second branch operand in S/B formats |
| RS1 | 19 | 16 | first source / base / JALR target base |
| RS2 | 15 | 12 | second source in R format |
| IMM16 | 15 | 0 | 16-bit immediate / offset, spans bits 15:0 |
<!-- END FIELDS -->

### 3.2 Formats (machine readable)

<!-- BEGIN FORMATS -->
| Format | Opcode bits | rd bits | rs1 bits | rs2 bits | imm16 bits | Used by |
|---|---|---|---|---|---|---|
| R | 31:24 | 23:20 | 19:16 | 15:12 | none | ADD SUB AND OR XOR SLL SRL SRA SLT SLTU |
| I | 31:24 | 23:20 | 19:16 | none | 15:0 | ADDI ANDI ORI XORI SLLI SRLI SRAI SLTI SLTIU LB LBU LW JALR |
| S | 31:24 | 23:20 (store data) | 19:16 | none | 15:0 | SB SW |
| B | 31:24 | 23:20 (second operand) | 19:16 | none | 15:0 | BEQ BNE BLT BGE BLTU BGEU |
| UJ | 31:24 | 23:20 (link) | 19:16 (must be 0) | none | 15:0 | JAL |
<!-- END FORMATS -->

For every instruction the unused low bits of an R-type word are **0**. The decoder
ignores those bits (they are not part of the semantics), so a future extension may
reuse them; the assembler always emits zero.

---

## 4. Opcode map

Opcodes 0x21..0x2F are **reserved** for a future MUL/MULH/DIV/DIVU/REM/REMU
extension and execute as NOP in v1. All other unassigned opcodes (0x30..0xFF) also
execute as NOP. Reserved does not mean trap: there are no traps.

<!-- BEGIN OPCODES -->
| Opcode | Mnemonic | Format | Operation (pseudocode) |
|---|---|---|---|
| 0x00 | HALT | HALT | freeze PC, retire, stop fetching |
| 0x01 | ADD | R | rd = (rs1 + rs2) mod 2^16 |
| 0x02 | SUB | R | rd = (rs1 - rs2) mod 2^16 |
| 0x03 | AND | R | rd = rs1 & rs2 |
| 0x04 | OR | R | rd = rs1 | rs2 |
| 0x05 | XOR | R | rd = rs1 ^ rs2 |
| 0x06 | SLL | R | rd = (rs1 << (rs2 & 0xF)) mod 2^16 |
| 0x07 | SRL | R | rd = zext(rs1) >> (rs2 & 0xF) |
| 0x08 | SRA | R | rd = sext(rs1) >> (rs2 & 0xF), truncated to 16 bits |
| 0x09 | SLT | R | rd = 1 if sext(rs1) < sext(rs2) else 0 |
| 0x0A | SLTU | R | rd = 1 if zext(rs1) < zext(rs2) else 0 |
| 0x0B | ADDI | I | rd = (rs1 + imm16) mod 2^16 |
| 0x0C | ANDI | I | rd = rs1 & imm16 |
| 0x0D | ORI | I | rd = rs1 | imm16 |
| 0x0E | XORI | I | rd = rs1 ^ imm16 |
| 0x0F | SLLI | I | rd = (rs1 << (imm16 & 0xF)) mod 2^16 |
| 0x10 | SRLI | I | rd = zext(rs1) >> (imm16 & 0xF) |
| 0x11 | SRAI | I | rd = sext(rs1) >> (imm16 & 0xF), truncated to 16 bits |
| 0x12 | SLTI | I | rd = 1 if sext(rs1) < sext(imm16) else 0 |
| 0x13 | SLTIU | I | rd = 1 if zext(rs1) < zext(imm16) else 0 |
| 0x14 | LB | I | rd = sext(byte at byteaddr) |
| 0x15 | LBU | I | rd = zext(byte at byteaddr) |
| 0x16 | LW | I | rd = word at (byteaddr & 0xFFFE), little-endian |
| 0x17 | SB | S | byte at byteaddr = low 8 bits of rs2 |
| 0x18 | SW | S | word at (byteaddr & 0xFFFE) = low 16 bits of rs2, little-endian |
| 0x19 | BEQ | B | if rs1 == rs2: PC = PC + 1 + sext(imm16) |
| 0x1A | BNE | B | if rs1 != rs2: PC = PC + 1 + sext(imm16) |
| 0x1B | BLT | B | if sext(rs1) < sext(rs2): PC = PC + 1 + sext(imm16) |
| 0x1C | BGE | B | if sext(rs1) >= sext(rs2): PC = PC + 1 + sext(imm16) |
| 0x1D | BLTU | B | if zext(rs1) < zext(rs2): PC = PC + 1 + sext(imm16) |
| 0x1E | BGEU | B | if zext(rs1) >= zext(rs2): PC = PC + 1 + sext(imm16) |
| 0x1F | JAL | UJ | rd = PC + 1; PC = PC + 1 + sext(imm16) |
| 0x20 | JALR | I | rd = PC + 1; PC = (rs1_old + sext(imm16)) mod 2^16 |
| 0x21 | (reserved MUL) | - | NOP in v1 |
| 0x22 | (reserved MULH) | - | NOP in v1 |
| 0x23 | (reserved DIV) | - | NOP in v1 |
| 0x24 | (reserved DIVU) | - | NOP in v1 |
| 0x25 | (reserved REM) | - | NOP in v1 |
| 0x26 | (reserved REMU) | - | NOP in v1 |
| 0x2F | (reserved) | - | NOP in v1 |
<!-- END OPCODES -->

### 4.1 Immediate rules

* **Every immediate is exactly 16 bits.** With 16-bit data there is no extension
  step for arithmetic/logical immediates: the 16-bit field is used directly as a
  16-bit pattern. `ADDI R1,R0,0xFFFF` adds -1; `ANDI R1,R0,0xFFFF` is a bitwise
  AND with all ones. This is stated deliberately so no implementation invents a
  sign-extension rule the ISA does not have.
* `SLTI` and `SLTIU` differ **only** in how they interpret the same 16-bit
  immediate: `SLTI` uses `sext(imm16)`, `SLTIU` uses `zext(imm16)`.
* Shift amounts use the low 4 bits of the register value or of the immediate
  (`amount & 0xF`). The assembler restricts an immediate shift amount to 0..15;
  the hardware masks, so an out-of-range encoding is still deterministic.
* Branch and jump offsets are **signed 16-bit** values interpreted by `sext(imm16)`
  and added to `PC + 1`. They are relative to the **next** instruction. Thus a
  conditional branch can reach PC-32767 .. PC+32768. The assembler checks this range.
* The JALR immediate is signed 16-bit: target = `(zext(rs1_old) + sext(imm16)) mod 2^16`.
  It is an absolute byte-style instruction index, not relative to the PC.

### 4.2 Compare semantics on tricky values

The following table is normative. `a` and `b` are 16-bit patterns. "Taken" is 1 if
the branch would be taken. `SLT`/`SLTU` write 1 for true, 0 for false.

<!-- BEGIN COMPARE_TABLE -->
| a | b | SLT | SLTU | BLT | BLTU | BGE | BGEU |
|---|---|---|---|---|---|---|---|
| 0x8000 | 0x7FFF | 1 | 0 | 1 | 0 | 0 | 1 |
| 0x7FFF | 0x8000 | 0 | 1 | 0 | 1 | 1 | 0 |
| 0xFFFF | 0x0000 | 1 | 0 | 1 | 0 | 0 | 1 |
| 0x0000 | 0xFFFF | 0 | 1 | 0 | 1 | 1 | 0 |
| 0x1234 | 0x1234 | 0 | 0 | 0 | 0 | 1 | 1 |
| 0x8000 | 0x8000 | 0 | 0 | 0 | 0 | 1 | 1 |
| 0x00FF | 0x0100 | 1 | 1 | 1 | 1 | 0 | 0 |
| 0xFF00 | 0x00FF | 1 | 0 | 1 | 0 | 0 | 1 |
<!-- END COMPARE_TABLE -->

---

## 5. PC semantics

* PC is a 16-bit **instruction index**. Normal execution is `PC <- (PC + 1) mod 2^16`.
* Sequential fetch wraps 0xFFFF -> 0x0000.
* A taken conditional branch computes `(PC + 1 + sext(imm16)) mod 2^16`.
* JAL computes `(PC + 1 + sext(imm16)) mod 2^16` and writes `PC + 1` (the instruction
  index of the following instruction) to `rd`.
* JALR computes `(rs1_old + sext(imm16)) mod 2^16` and writes `PC + 1` to `rd`.
  `rs1_old` means the value **before** any write caused by this instruction. If
  `rd == rs1`, the target is still computed from the old value and the link value is
  what ends up in the register. This is the **only** defined behaviour; hardware must
  not depend on a race.
* PCs are not byte addresses. A function pointer, return address or jump-table entry
  is an instruction index. The ABI repeats this.

---

## 6. HALT, fetch beyond the image, and unassigned opcodes

* The all-zero 32-bit word is **HALT**. HALT is a real retired instruction: it writes
  no register, performs no memory access, retires normally, and then freezes the PC.
  Nothing after it executes; no state changes after HALT retires.
* Fetching an instruction index **beyond the end of the program image**, or beyond
  `INSTR_WORDS`, returns the HALT instruction. Therefore zero-filled or uninitialised
  instruction memory halts safely. The fetch itself is not an exception and does not
  retire; the first such fetched HALT does.
* An explicit HALT anywhere in the image behaves identically. Code after a HALT is
  unreachable unless a jump targets it.
* **Unassigned opcodes execute as NOP**: no register write, no memory access, no PC
  change other than the normal increment. They retire normally and appear in the
  retirement trace. This is deterministic and is part of the ISA, not an error.
* Reserved opcodes 0x21..0x2F are unassigned in v1 and therefore NOP. A future
  extension will assign them and bump the ISA version.

---

## 7. Data memory model

Data addresses are **byte addresses**, 16 bits, little-endian. There is no
instruction that reads instruction memory: the machine is Harvard, and read-only
data lives in data memory (initialised from the program image).

### 7.1 Address calculation

```
byteaddr = (rs1 + sext(imm16)) mod 2^16
```

* `LB`/`LBU`/`SB` use `byteaddr` exactly.
* `LW`/`SW` use `byteaddr & 0xFFFE`; **address bit 0 is ignored**, there is no
  misalignment trap and no exception. An `LW` at 0x0001 reads the word at 0x0000.
* `LW` reads the low byte from the even address and the high byte from the odd
  address; `SW` writes them in that order. This is little-endian.

### 7.2 Memory map

<!-- BEGIN MEMORY_MAP -->
| Start | End | Name | Access | Semantics |
|---|---|---|---|---|
| 0x0000 | RAM_BYTES-1 | RAM | R/W | Data RAM, byte addressable. Default RAM_BYTES = 4096 (0x1000), maximum 0xFF00. Initialised from the program image at configuration. Reset does not reload it. |
| RAM_BYTES | 0xFEFF | UNMAPPED | R/W | Reads return 0x0000, writes are ignored. Empty when RAM_BYTES = 0xFF00. |
| 0xFF00 | 0xFF01 | UART_TX | W | Write low 8 bits: start one UART byte if not busy; ignored if busy. Reads return 0x0000. |
| 0xFF02 | 0xFF03 | UART_STATUS | R | Bit 0 = tx_busy; bits 15:1 = 0. Writes ignored. |
| 0xFF04 | 0xFF05 | GPIO_OUT | R/W | 16-bit output register; reset value 0x0000. |
| 0xFF06 | 0xFF07 | GPIO_IN | R | 16-bit input pins; writes ignored. |
| 0xFF08 | 0xFF09 | TIMER | R | Free-running 16-bit cycle counter; writes ignored. Counts timing-model cycles, wraps mod 2^16. |
| 0xFF0A | 0xFFFF | MMIO_RESERVED | R/W | Reads return 0x0000, writes ignored. |
<!-- END MEMORY_MAP -->

**Byte access to MMIO.** MMIO registers are 16 bits on even addresses. For `LB`,
`LBU` and `SB`, address bit 0 is ignored and the register is selected by
`address & 0xFFFE`:

* `LB`/`LBU` return the **low 8 bits** of the selected register, sign- or
  zero-extended to 16 bits. (A byte load never returns the high byte.)
* `SB` writes the **low 8 bits** of the stored value into the low byte of the
  selected register; the high byte is unchanged (except UART_TX, where any SB write
  to 0xFF00 or 0xFF01 transmits the low byte).
* Word accesses (`LW`/`SW`) naturally ignore address bit 0 too.

A write to a read-only register is ignored. A read from a write-only register
returns 0. Reserved MMIO addresses read 0 and ignore writes. During reset no MMIO
write takes effect and the UART is idle.

### 7.3 UART and timer behaviour (timing layer)

* `UART_TX` accepts a byte when `UART_STATUS[0] == 0`. The write takes effect at the
  MEM stage. The byte is appended to the output stream and the transmitter is busy
  for `UART_CYCLES_PER_BYTE` cycles (default 10). If software writes while busy the
  byte is **dropped**; robust code polls `UART_STATUS` first.
* `TIMER` reads the current pipeline cycle number modulo 2^16. The same value drives
  the timing model, so a program that polls the timer can be compared cycle for
  cycle with RTL.
* `GPIO_OUT` is a plain 16-bit register. `GPIO_IN` is a model parameter; it is
  constant for a run unless a test supplies timed values (a list of (cycle, value)
  pairs).

---

## 8. Reset state

Reset is an input that suppresses all architectural writes.

| State | During reset | After reset release |
|---|---|---|
| PC | forced to 0, no fetch retires | fetch starts at instruction index 0 |
| Registers R0..R15 | unchanged (power-on 0 on FPGA) | preserved; reset does not clear |
| Data RAM | unchanged, no writes | unchanged (not reloaded from image) |
| UART | idle, no byte starts | tx_busy = 0 |
| GPIO_OUT | 0x0000 | software controlled |
| TIMER | 0x0000 on reset assertion | counts cycles from 1 |
| Pipeline valid bits | cleared | first instruction fetched in cycle 1 |

The architectural power-on value of all registers is 0 because FPGA flip-flops
initialise to 0. Software must still initialise anything it depends on: a warm reset
does not clear registers, and reset never reloads `.data`. In particular crt0 must
zero `.bss` itself.

---

## 9. Worked encoding examples (hand-computed)

Bit fields: opcode[31:24], rd[23:20], rs1[19:16], rs2[15:12], imm16[15:0].
All values below were computed by hand from the tables above, not by a tool.

Branch and JAL rows below show the **encoded imm16 offset** in the last
column; in assembly source a branch/JAL operand is an absolute instruction
index (or label), and the assembler computes that offset.

| Assembly | Encoding | Derivation |
|---|---|---|
| `add r3,r1,r2` | 0x01312000 | op=01, rd=3, rs1=1, rs2=2, low12=0 |
| `sub r3,r4,r5` | 0x02345000 | op=02, rd=3, rs1=4, rs2=5 |
| `and r6,r7,r8` | 0x03678000 | op=03, rd=6, rs1=7, rs2=8 |
| `or r9,r10,r11` | 0x049AB000 | op=04, rd=9, rs1=A, rs2=B |
| `xor r12,r13,r14` | 0x05CDE000 | op=05, rd=C, rs1=D, rs2=E |
| `sll r1,r2,r3` | 0x06123000 | op=06, rd=1, rs1=2, rs2=3 |
| `srl r4,r5,r6` | 0x07456000 | op=07, rd=4, rs1=5, rs2=6 |
| `sra r7,r8,r9` | 0x08789000 | op=08, rd=7, rs1=8, rs2=9 |
| `slt r10,r11,r12` | 0x09ABC000 | op=09, rd=A, rs1=B, rs2=C |
| `sltu r13,r14,r15` | 0x0ADEF000 | op=0A, rd=D, rs1=E, rs2=F |
| `addi r5,r1,-1` | 0x0B51FFFF | op=0B, rd=5, rs1=1, imm=FFFF |
| `andi r2,r3,0x00FF` | 0x0C2300FF | op=0C, rd=2, rs1=3, imm=00FF |
| `ori r4,r5,0x8000` | 0x0D458000 | op=0D, rd=4, rs1=5, imm=8000 |
| `xori r6,r7,0xFFFF` | 0x0E67FFFF | op=0E, rd=6, rs1=7, imm=FFFF |
| `slli r1,r1,3` | 0x0F110003 | op=0F, rd=1, rs1=1, imm=0003 |
| `srli r2,r3,4` | 0x10230004 | op=10, rd=2, rs1=3, imm=0004 |
| `srai r4,r5,15` | 0x1145000F | op=11, rd=4, rs1=5, imm=000F |
| `slti r6,r7,-1` | 0x1267FFFF | op=12, rd=6, rs1=7, imm=FFFF |
| `sltiu r8,r9,1` | 0x13890001 | op=13, rd=8, rs1=9, imm=0001 |
| `lb r1,0(r2)` | 0x14120000 | op=14, rd=1, rs1=2, imm=0000 |
| `lbu r3,1(r4)` | 0x15340001 | op=15, rd=3, rs1=4, imm=0001 |
| `lw r5,0xFFFE(r6)` | 0x1656FFFE | op=16, rd=5, rs1=6, imm=FFFE (addr = r6-2) |
| `sb r7,-2(r8)` | 0x1778FFFE | op=17, data=rs2=7 in bits 23:20, base=8, imm=FFFE |
| `sw r9,4(r10)` | 0x189A0004 | op=18, data=rs2=9, base=A, imm=0004 |
| `beq r1,r2,+3` | 0x19210003 | op=19, rs2=2, rs1=1, imm=0003 |
| `bne r3,r4,-1` | 0x1A43FFFF | op=1A, rs2=4, rs1=3, imm=FFFF |
| `blt r5,r6,+1` | 0x1B650001 | op=1B, rs2=6, rs1=5, imm=0001 |
| `bge r7,r8,-2` | 0x1C87FFFE | op=1C, rs2=8, rs1=7, imm=FFFE |
| `bltu r9,r10,0` | 0x1DA90000 | op=1D, rs2=A, rs1=9, imm=0000 |
| `bgeu r11,r12,0x7FFF` | 0x1ECB7FFF | op=1E, rs2=C, rs1=B, imm=7FFF |
| `jal r15,0x0010` | 0x1FF0000F | op=1F, rd=F, target 0x0010 at PC 0 -> offset 0x000F |
| `jalr r0,r15,0` | 0x200F0000 | op=20, rd=0, rs1=F, imm=0000 |
| `halt` | 0x00000000 | all zero |
| `nop` | 0x01000000 | ADD r0,r0,r0 |
| `li r1,0x1234` | 0x0B101234 | ADDI r1,r0,0x1234 |
| `mv r3,r4` | 0x01340000 | ADD r3,r4,r0 |
| `not r1,r2` | 0x0E12FFFF | XORI r1,r2,0xFFFF |
| `neg r3,r4` | 0x02304000 | SUB r3,r0,r4 |
| `subi r1,r2,1` | 0x0B12FFFF | ADDI r1,r2,-1 |
| `seqz r1,r2` | 0x13120001 | SLTIU r1,r2,1 |
| `snez r1,r2` | 0x0A102000 | SLTU r1,r0,r2 |
| `call 0x20` | 0x1FF0001F | JAL r15, target 0x20 at PC 0 -> offset 0x001F |
| `ret` | 0x200F0000 | JALR r0,r15,0 |
| `j 0x10` | 0x1F00000F | JAL r0, target 0x10 at PC 0 -> offset 0x000F |
| `bgt r1,r2,T` | BLT r2,r1,T | op=1B, rs2=1, rs1=2, then offset |
| `beqz r5,T` | BEQ r5,r0,T | op=19, rs2=0, rs1=5 |
| `blez r5,T` | BGE r0,r5,T | op=1C, rs2=5, rs1=0 |

---

## 10. Pseudo-instructions

Pseudos are **assembler-only**. They expand exactly as shown; the hardware never
sees them. Branch/jump targets are absolute instruction indices; the assembler
computes the encoded offset. `imm` is a 16-bit value; `SUBI` rejects -32768 because
its negation is not representable as a 16-bit signed addend.

<!-- BEGIN PSEUDOS -->
| Pseudo | Expansion | Notes |
|---|---|---|
| LI rd,imm | ADDI rd,R0,imm | 16-bit constant, range -32768..65535 |
| LA rd,sym | ADDI rd,R0,sym | absolute instruction index or byte address |
| MV rd,rs | ADD rd,rs,R0 | register copy |
| NOT rd,rs | XORI rd,rs,0xFFFF | bitwise complement |
| NEG rd,rs | SUB rd,R0,rs | two's complement negation |
| NOP | ADD R0,R0,R0 | canonical 0x01000000 |
| J target | JAL R0,target | no link |
| CALL target | JAL R15,target | link in LR |
| RET | JALR R0,R15,0 | return via LR |
| BGT rs,rt,target | BLT rt,rs,target | swap operands |
| BLE rs,rt,target | BGE rt,rs,target | swap operands |
| BGTU rs,rt,target | BLTU rt,rs,target | swap operands |
| BLEU rs,rt,target | BGEU rt,rs,target | swap operands |
| BEQZ rs,target | BEQ rs,R0,target | compare with zero |
| BNEZ rs,target | BNE rs,R0,target | compare with zero |
| BLTZ rs,target | BLT rs,R0,target | signed |
| BGEZ rs,target | BGE rs,R0,target | signed |
| BGTZ rs,target | BLT R0,rs,target | signed, operand swap |
| BLEZ rs,target | BGE R0,rs,target | signed, operand swap |
| SUBI rd,rs,imm | ADDI rd,rs,(-imm mod 2^16) | imm must not be -32768 |
| SEQZ rd,rs | SLTIU rd,rs,1 | 1 if rs == 0 |
| SNEZ rd,rs | SLTU rd,R0,rs | 1 if rs != 0 |
| SLTZ rd,rs | SLT rd,rs,R0 | 1 if rs < 0 signed |
| SGTZ rd,rs | SLT rd,R0,rs | 1 if rs > 0 signed |
<!-- END PSEUDOS -->

---

## 11. What this ISA deliberately does not have

* **No flags/carry/zero register.** Branches compare registers directly; SLT/SLTU
  materialise comparisons.
* **No traps, exceptions, interrupts, or privileged mode.** Misaligned accesses do
  not trap; they ignore address bit 0. There is no system call instruction.
* **No MUL/DIV/REM in the base core.** Opcodes 0x21..0x2F are reserved; software
  routines `__mulhi3`, `__divhi3`, `__udivhi3`, `__modhi3`, `__umodhi3` live in
  `programs/runtime/`. A future extension may assign the reserved opcodes after
  measurement.
* **No halfword (16-bit) load/store distinct from word**: `LW`/`SW` are the word
  operations, because a word is 16 bits. There is no 32-bit load.
* **No unaligned access support**: `LW`/`SW` silently align down; there is no
  byte-granular 16-bit access.
* **No byte registers**; `char` lives in the low byte of a word in registers and is
  loaded/stored with LB/LBU/SB.
* **No long, float, double, or 32-bit integers.** `int` is 16 bits.
* **No branch delay slots.** Taken branches flush the two younger instructions.
* **No PC-relative addressing mode** other than branch/JAL offsets; `LA` materialises
  an absolute 16-bit address. There is no LUI/AUIPC because every 16-bit constant
  fits in one instruction.
* **No instruction cache, no MMU, no protection.** Instruction and data memories are
  separate (Harvard).
* **No byte enables on instruction fetch**: instructions are 32 bits and fetched by
  instruction index.
