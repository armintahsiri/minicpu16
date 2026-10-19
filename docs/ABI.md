
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
