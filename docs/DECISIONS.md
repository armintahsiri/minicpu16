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
