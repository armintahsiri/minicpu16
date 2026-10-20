# MiniCPU-16 optimization-opportunity data (Phase 5B)

This file is **measurement, not tuning**.  Nothing in Phase 5B changes the ISA,
the microarchitecture contract, or the timing layer to make these numbers
better; the goal is to say exactly where the cycles go, with the same tools
that produce the verification results.

All numbers are produced by the cycle-exact timing layer
(`tools/isa_sim.py`) on the linked benchmark programs and cross-checked
against the RTL with `tools/rtl_suite.py` and `tools/rtl_run.py`
(FORWARDING=true).  The RTL and the layer agree on every cycle count in
`docs/BENCHMARKS.md`.

## 1. The cycle equation is exact

For every benchmark, with FORWARDING=true:

    total cycles = N + 4 + S + 2*R

where

* `N` = retired instructions (including the final HALT),
* `S` = load-use stalls (the load is in EX and an actual consumer is in ID),
* `R` = retired taken conditional branches + JAL + JALR,
* `4` = pipeline fill (and the HALT drain, which is already inside `N`).

| program | N | cycles | CPI | S | taken br | JAL | JALR | R | 2R | N+4+S+2R |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fibonacci | 112 | 156 | 1.393 | 0 | 2 | 17 | 1 | 20 | 40 | 156 |
| factorial_rec | 287 | 448 | 1.561 | 7 | 23 | 36 | 16 | 75 | 150 | 448 |
| bubble_sort | 1278 | 1950 | 1.526 | 120 | 66 | 207 | 1 | 274 | 548 | 1950 |
| crc16 | 647 | 893 | 1.380 | 10 | 105 | 10 | 1 | 116 | 232 | 893 |
| memory_ops | 572 | 857 | 1.498 | 87 | 35 | 57 | 5 | 97 | 194 | 857 |
| muldiv | 1186 | 1626 | 1.371 | 0 | 146 | 57 | 15 | 218 | 436 | 1626 |
| matmul | 12950 | 18114 | 1.399 | 0 | 586 | 1993 | 1 | 2580 | 5160 | 18114 |
| **total** | **17432** | **24673** | | **285** | | | | **3456** | | |
| hello | 257 | 400 | 1.556 | 57 | 20 | 20 | 1 | 41 | 82 | 400 |
| switch | 58 | 96 | 1.655 | 4 | 1 | 5 | 9 | 15 | 30 | 96 |
| nested_calls | 55 | 85 | 1.545 | 0 | 1 | 6 | 6 | 13 | 26 | 85 |
| funcptr | 30 | 48 | 1.600 | 0 | 1 | 1 | 5 | 7 | 14 | 48 |

The identity holds exactly for all eleven programs.  It is the contract's
"two-cycle control penalty" made concrete.

## 2. Where the cycles go

Overhead above `N` is entirely pipeline fill (4), load-use stalls (`S`) and
control transfers (`2R`).  There is no other source of lost cycles in the
retiring part of the machine.

| program | overhead | fill | stall % of ovh | control % of ovh |
|---|---:|---:|---:|---:|
| fibonacci | 44 | 4 | 0.0 | 90.9 |
| factorial_rec | 161 | 4 | 4.3 | 93.2 |
| bubble_sort | 672 | 4 | 17.9 | 81.5 |
| crc16 | 246 | 4 | 4.1 | 94.3 |
| memory_ops | 285 | 4 | 30.5 | 68.1 |
| muldiv | 440 | 4 | 0.0 | 99.1 |
| matmul | 5164 | 4 | 0.0 | 99.9 |
| hello | 143 | 4 | 39.9 | 57.3 |
| switch | 38 | 4 | 10.5 | 78.9 |
| nested_calls | 30 | 4 | 0.0 | 86.7 |
| funcptr | 18 | 4 | 0.0 | 77.8 |

**Control transfers dominate.**  matmul spends 99.9% of its overhead on
branches and jumps; muldiv 99.1%.  Load-use stalls are visible only in the
load-heavy integer programs (memory_ops 30.5%, hello 39.9%, bubble_sort
17.9%).

## 3. Software arithmetic

The runtime provides multiply and divide in software
(`programs/runtime/arith.s`).  Attributing retired instructions by PC range
(`__mulhi3` .. just before `memcpy`):

| program | retired in arith helpers | share of instructions | share of cycles |
|---|---:|---:|---:|
| factorial_rec | 174 | 60.6% | 38.8% |
| muldiv | 1123 | 94.7% | 69.1% |

Every other benchmark is 0%.  Detail for muldiv:

| helper | retired |
|---|---:|
| __mulhi3 | 300 |
| __udivhi3 | 742 |
| __divhi3 | 67 |
| __modhi3 | 7 |
| __umodhi3 | 7 |

## 4. Branch/jump behavior

Static not-taken prediction means every retired taken branch, JAL and JALR
pays two flush cycles.  Backward-taken conditional branches (the classic
loop-closing branch) are:

| program | backward-taken conditional |
|---|---:|
| crc16 | 63 |
| muldiv | 75 |
| memory_ops | 31 |
| hello | 18 |
| all others | 0 |

## 5. What-if estimates (model-level, not implemented)

These are arithmetic `2R` / instruction-count reductions, **not measured on
hardware and not implemented**.  They are the honest upper bound of what the
named change could give on this program set; realise them only with a new
phase, new verification and new synthesis numbers.

1. **Backward-taken/forward-not-taken predictor.**  A backward-taken branch
   would resolve without a flush.  Saving: `2 * 187 = 374` cycles over the
   whole benchmark set (~1.5% of the 24673 total cycles), concentrated in
   crc16, muldiv, memory_ops and hello.  It cannot help matmul, whose 586
   taken branches are all forward conditional branches and whose 1993 JALs
   are unconditional.
2. **Resolve JAL in ID instead of EX.**  A JAL's target is PC+imm and does not
   need operands, so it could redirect one stage earlier and flush only IF:
   saving `1` cycle per JAL/JALR.  There are 2470 JAL/JALR retirements in the
   set (2409 JAL + 61 JALR), the vast majority in matmul (1994); saving ~2470
   cycles (~10.0% of the total) if applied uniformly.
3. **Hardware MUL.**  Replacing `__mulhi3` (a shift-add loop) with a single
   instruction would remove essentially all of the 60.6% instruction share on
   factorial_rec and the `__mulhi3` part of muldiv (300 of 1123 helper
   retirements).  Division has no such shortcut in the current ISA and would
   remain software.  This is an ISA change and is out of scope for 5B.

## 6. Synthesis/cost context

From `tools/synth_smoke.py` (flat `synth_ice40`, unconstrained
`nextpnr-ice40`; estimates, not sign-off — see the final Phase 5B report):

| configuration | SB_LUT4 | SB_DFF* | SB_RAM40_4K |
|---|---:|---:|---:|
| FORWARDING=false | 1257 | 610 | 24 |
| FORWARDING=true | 1369 | 618 | 24 |

Forwarding costs about 112 LUT4 more than the stall-only pipeline on this
flow; block-RAM inference is identical (24 blocks).  The flat nextpnr estimate
is *lower* for forwarding in this measurement, which is a placement-seed
artifact of the unconstrained flow, not a claim that forwarding is faster in
silicon.

---

*Produced in Phase 5B.  All measured numbers can be regenerated with
`make bench`, `python3 tools/synth_smoke.py` and
`python3 tools/rtl_suite.py --sim both`.  The what-if estimates are arithmetic
and explicitly unverified.*
