# Retirement trace format v1

The golden model and (from Phase 5) the RTL produce the same line-oriented trace.
It is deliberately plain text: stable, greppable, easy to diff, and readable
without tools. `tools/trace_diff.py` compares two traces and prints the first
divergence with context.

## Header

The first two lines are fixed:

```
# minicpu16-retirement-trace v1
# seq retire pc instr rd value memop memaddr memsize memdata taken target
```

## Records

One record per **retired** instruction, in retirement order. Squashed instructions
(branch/JAL/JALR/HALT shadow) never appear. The HALT instruction appears as the last
record of a normal run.

Fields are separated by one or more spaces. Hex fields have no `0x` prefix. `-`
means "not applicable".

| # | Field | Width | Meaning |
|---|---|---|---|
| 1 | `seq` | decimal | 1-based retirement sequence number |
| 2 | `retire` | decimal | cycle in which the instruction retires (WB), per the cycle definition in MICROARCH_CONTRACT.md |
| 3 | `pc` | 4 hex | instruction index of the retired instruction |
| 4 | `instr` | 8 hex | the 32-bit instruction word |
| 5 | `rd` | `rN` or `-` | architectural destination register, or - if none. A write to R0 is reported as - because it has no effect. |
| 6 | `value` | 4 hex or `-` | value written to `rd` (load data, ALU/link result) |
| 7 | `memop` | `L`, `S` or `-` | load or store |
| 8 | `memaddr` | 4 hex or `-` | effective byte address; LW/SW have bit 0 cleared |
| 9 | `memsize` | 1, 2 or `-` | bytes transferred |
| 10 | `memdata` | 4 hex or `-` | value transferred: load result after LB/LBU extension, or the stored value |
| 11 | `taken` | 0 or 1 | conditional branch taken, or JAL/JALR executed |
| 12 | `target` | 4 hex or `-` | branch/jump target instruction index |

Notes:

* For `LB`, `memdata` is the sign-extended 16-bit value written to `rd` (e.g. a
  byte 0x80 gives `FF80`); for `LBU` it is zero-extended. The raw byte is not
  stored separately.
* For `SB`, `memdata` is the low 8 bits of the stored register (shown as 4 hex
  digits); for `SW` it is all 16 bits.
* MMIO accesses use the same fields. A UART_TX store has memop `S`, the MMIO
  address, size 1 or 2, and the transmitted byte in the low bits of `memdata`.
* A taken conditional branch reports `taken=1` and its target; a not-taken one
  reports `taken=0` and `target=-`.
* `JAL`/`JALR` always report `taken=1` and their target, and their link value in
  `value` if `rd != 0`.
* `HALT` reports `taken=0`, `target=-`, no register and no memory.
* Unassigned opcodes execute as NOP and appear with all effect fields `-`.

## Example

```
# minicpu16-retirement-trace v1
# seq retire pc instr rd value memop memaddr memsize memdata taken target
1 5 0000 0B100001 r1 0001 - - - - 0 -
2 6 0001 00000000 - - - - - - 0 -
```

The second record is the HALT; the run's total cycle count is 6.

## Comparison rules

* Compare all fields by default.
* `trace_diff.py --ignore-cycles` compares everything except `retire`. This is the
  right mode for architectural-layer vs timing-layer comparisons, where only the
  cycle numbers differ.
* The first differing record is reported with up to `--context` records around it.
  Exit status: 0 identical, 1 different, 2 malformed trace.
* Format changes require a new version line. The Phase 5 RTL must emit exactly
  this v1 format.
