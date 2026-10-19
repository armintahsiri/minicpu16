# MiniCPU-16 Application Binary Interface (ABI) v1.0

This document is normative for the compiler (Phase 7), the runtime in
`programs/runtime/`, and hand-written assembly. It assumes the ISA in
`docs/ISA.md` is frozen.

---

## 1. Data model

| C type | Size | Alignment | Notes |
|---|---|---|---|
| `char` | 1 byte | 1 | **unsigned** by default on this ABI |
| `signed char` | 1 byte | 1 | loaded with `LB`, stored with `SB` |
| `short` | 2 bytes | 2 | same as `int` |
| `int` | 2 bytes | 2 | 16-bit two's complement |
| `unsigned int` | 2 bytes | 2 | |
| pointers | 2 bytes | 2 | **byte addresses** |
| function pointers | 2 bytes | 2 | **instruction indices** |
| `long`, `long long`, `float`, `double` | not supported | - | no 32-bit or FP operations |
| structs/unions | supported by pointer and field offsets | field alignment | not passed or returned by value in v1 |
| bitfields | not supported in v1 | - | |

`sizeof(int) == 2`, `sizeof(short) == 2`, `sizeof(char) == 1`,
`sizeof(void *) == 2`. There is no padding beyond natural alignment; a struct's
size is rounded up to a multiple of 2. Arrays are contiguous with no gaps.

**Code versus data addresses.** A data label, pointer to an object, array index and
stack slot are **byte addresses**. A code label, function pointer, return address
and jump-table entry are **instruction indices**. The two spaces are separate
(Harvard); converting one to the other is meaningless.

---

## 2. Register roles

<!-- BEGIN ABI_REGISTERS -->
| Register | Alias | Role | Preserved across a call |
|---|---|---|---|
| R0 | zero | constant 0; writes ignored | n/a (always 0) |
| R1 | a0 | first argument, return value | no (caller-saved) |
| R2 | a1 | second argument | no (caller-saved) |
| R3 | a2 | third argument | no (caller-saved) |
| R4 | a3 | fourth argument | no (caller-saved) |
| R5 | t0 | temporary | no (caller-saved) |
| R6 | t1 | temporary | no (caller-saved) |
| R7 | t2 | temporary | no (caller-saved) |
| R8 | t3 | temporary | no (caller-saved) |
| R9 | s0 | callee-saved | **yes** |
| R10 | s1 | callee-saved | **yes** |
| R11 | s2 | callee-saved | **yes** |
| R12 | s3 | callee-saved | **yes** |
| R13 | s4 | callee-saved | **yes** |
| R14 | sp | stack pointer (full-descending) | **yes** |
| R15 | lr / ra | link register (return address) | no (caller-saved) |
<!-- END ABI_REGISTERS -->

The assembler accepts `zero`, `a0`..`a3`, `t0`..`t3`, `s0`..`s4`, `sp`,
`lr`, `ra`, and `r0`..`r15` (case-insensitive).

---

## 3. Calling convention

### 3.1 Arguments and return value

* The first four arguments are passed in `a0`, `a1`, `a2`, `a3` (R1-R4), in
  order.
* Argument 5 and later are passed on the stack, **at the caller's SP**:
  argument 5 at `0(sp)`, argument 6 at `2(sp)`, argument 7 at `4(sp)`, and so on.
  The caller reserves this area before the call and removes it afterwards. `JAL`
  does not change SP.
* The return value is in `a0` (R1). A `char` return is in the low 8 bits,
  zero-extended (unsigned `char`) or sign-extended (`signed char`); a `short`/
  `int` occupies all 16 bits.
* A function may clobber `a0`..`a3`, `t0`..`t3` and `lr`. It must preserve
  `s0`..`s4` and `sp`.

### 3.2 Stack

* The stack is **full-descending**: `sp` points at the last occupied word
  (the top of stack). It is always **2-byte aligned**.
* Push a word: `addi sp, sp, -2` then `sw value, 0(sp)`.
  Pop: `lw value, 0(sp)` then `addi sp, sp, 2`.
* The initial `sp` is `RAM_BYTES` (one past the last RAM byte), set by crt0.
  With the default RAM this is 0x1000; the first push writes 0x0FFE.
* Stack grows down from the top of RAM; static data grows up from 0. There is no
  stack-overflow check and no guard page.
* There is **no frame pointer** in v1. A function addresses its frame relative to
  `sp`; it must therefore adjust all offsets after any dynamic stack allocation.
  Frames are allocated once in the prologue, so this is simple.

### 3.3 Prologue and epilogue

A function that calls other functions **must** save `lr` (the `JAL` link is a
single register and nested calls overwrite it). A function that uses `s0`..`s4`
must save the ones it uses and restore them before returning.

Canonical prologue:

```
func:
    addi sp, sp, -FRAME       # FRAME is even; includes saved ra and saved regs
    sw   lr, FRAME-2(sp)
    sw   s0, FRAME-4(sp)      # only if used
    ...                       # body
    lw   s0, FRAME-4(sp)
    lw   lr, FRAME-2(sp)
    addi sp, sp, FRAME
    ret
```

`FRAME` must fit a signed 16-bit `ADDI` immediate (max 32767). Larger frames need
multiple `addi sp, sp, -32767` steps; with a 64 KB address space and a 4 KB default
RAM this is not expected in v1.

### 3.4 Calls through pointers

A function pointer holds an **instruction index**. Call it with:

```
    jalr lr, t0, 0       # t0 = function pointer
```

A return is `ret` (`jalr zero, lr, 0`). If the callee is a leaf that does not
touch `lr`, it may simply `ret`.

### 3.5 Variadic functions

**Not supported in v1.** Every function has a fixed arity. The ABI reserves no
argument-count register and defines no `va_list`. This is a deliberate, documented
limitation (see `docs/DECISIONS.md`); a future ABI revision may define a pointer
to an argument block.

### 3.6 Interrupts and reentrancy

There are none. The ABI is single-threaded and non-reentrant. Runtime routines may
use scratch registers freely but must obey the same save rules if they call other
functions.

---

## 4. Program image, crt0 and `.bss`

The assembler produces one image with no linker and no relocations.

* `.text` starts at **instruction index 0**. crt0 is placed first.
* `.data` starts at **byte address 0** in data memory and is initialised by the
  FPGA bitstream / memory image. Data labels are byte addresses.
* `.bss` follows `.data`; it occupies RAM but has no image contents. The symbols
  `__bss_start` and `__bss_end` are defined by the assembler (equal if there is
  no `.bss`).
* The heap is undefined in v1 (no `malloc`); a later runtime may grow a bump
  allocator from `__bss_end` upwards.
* **Reset does not reload `.data`**; only FPGA configuration does. A warm reset
  keeps RAM. crt0 must zero `.bss` on every start and does not copy `.data`.
* Strings and `.rodata` live in data memory (there is no instruction that reads
  instruction memory).

crt0 (in `programs/runtime/crt0.s`) does exactly this:

```
_start:
    li   sp, RAM_BYTES        # stack top, one past the last RAM byte
    la   t0, __bss_start
    la   t1, __bss_end
.Lbss_loop:
    bgeu t0, t1, .Lbss_done
    sb   zero, 0(t0)
    addi t0, t0, 1
    j    .Lbss_loop
.Lbss_done:
    li   a0, 0                # argc = 0
    li   a1, 0                # argv = NULL
    call main
    halt
```

`main` may return; its return value is ignored. There is no `exit()` system call.

---

## 5. Worked example: locals, nested calls, saved registers

C source:

```c
int leaf(int x)       { return (x << 1) + 1; }
int add3(int a,int b,int c) { return a + b + c; }

int caller(int x) {
    int local = leaf(x);
    return add3(local, x, 7) + local;
}
```

Assembly (canonical frame: [0] local, [2] saved s0, [4] saved lr):

```
leaf:
    slli a0, a0, 1
    addi a0, a0, 1
    ret

add3:
    add  a0, a0, a1
    add  a0, a0, a2
    ret

caller:
    addi sp, sp, -6          # 3 words: local, s0, lr
    sw   lr, 4(sp)
    sw   s0, 2(sp)
    mv   s0, a0              # s0 = x, preserved across calls
    call leaf                # a0 = leaf(x)
    sw   a0, 0(sp)           # local
    mv   a1, s0              # arg2 = x
    li   a2, 7               # arg3 = 7
    call add3                # a0 = add3(local, x, 7); a0 already local
    lw   t0, 0(sp)
    add  a0, a0, t0          # + local
    lw   s0, 2(sp)
    lw   lr, 4(sp)
    addi sp, sp, 6
    ret
```

Stack pictures:

```
on entry to caller          after prologue             at call add3
(higher addresses)          sp+6: caller's frame       sp+6: caller's frame
sp+0: return address?       sp+4: saved lr             sp+4: saved lr
      (lr register)         sp+2: saved s0             sp+2: saved s0
                            sp+0: local                sp+0: local
```

The 5th-argument case:

```
    addi sp, sp, -2
    sw   t0, 0(sp)       # argument 5
    call f
    addi sp, sp, 2
```

Inside `f` before its prologue, argument 5 is at `0(sp)`; after a 4-byte
prologue it is at `4(sp)`.

---

## 6. Runtime routine names

Software arithmetic (no MUL/DIV in the base ISA; see `docs/ISA.md`):

| Symbol | Operation | Notes |
|---|---|---|
| `__mulhi3` | signed and unsigned 16x16 -> low 16 product | "HI" = HImode = 16-bit; `*` needs the low half only |
| `__divhi3` | signed 16/16 quotient | division by zero returns 0xFFFF; -32768 / -1 wraps to 0x8000 |
| `__udivhi3` | unsigned 16/16 quotient | division by zero returns 0xFFFF |
| `__modhi3` | signed remainder | division by zero returns the dividend; -32768 % -1 = 0 |
| `__umodhi3` | unsigned remainder | division by zero returns the dividend |

Also provided: `memcpy`, `memset`, `strlen`, `strcmp` (byte operations).
These follow the same ABI: arguments in `a0`..`a3`, result in `a0`.

---

## 7. Rules a compiler must follow

1. Never rely on power-on register values; only crt0's explicit setup is guaranteed.
2. Keep `sp` 2-byte aligned at every call and return.
3. Save `lr` in any non-leaf function.
4. Preserve `s0`..`s4` and `sp` across calls; everything else is clobberable.
5. Use `LB`/`LBU` for `signed char`/`char` loads and `SB` for stores; mask with
   `andi 0x00FF` when converting an `int` to `char`.
6. Align `int`/`short`/`pointer` objects to 2 bytes. Unaligned `LW`/`SW` do not
   trap but silently ignore address bit 0, which would corrupt C semantics.
7. Use `SLTU`/`BLTU`/`BGEU` for unsigned comparisons.
8. Materialise constants with `LI`; do not use two instructions for a 16-bit
   constant.
9. Jump tables contain instruction indices, not byte addresses.
