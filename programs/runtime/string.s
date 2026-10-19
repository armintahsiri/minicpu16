# MiniCPU-16 byte/string runtime.

.text
.globl memcpy
# memcpy(dst=a0, src=a1, n=a2) -> dst in a0.
memcpy:
    mv   t0, a0
    beqz a2, .Lmc_done
.Lmc_loop:
    lbu  t1, 0(a1)
    sb   t1, 0(a0)
    addi a0, a0, 1
    addi a1, a1, 1
    addi a2, a2, -1
    bnez a2, .Lmc_loop
.Lmc_done:
    mv   a0, t0
    ret

.globl strlen
# strlen(s=a0) -> length in a0.
strlen:
    mv   t0, a0
    li   a0, 0
.Lsl_loop:
    lbu  t1, 0(t0)
    beqz t1, .Lsl_done
    addi a0, a0, 1
    addi t0, t0, 1
    j    .Lsl_loop
.Lsl_done:
    ret

.globl strcmp
# strcmp(a=a0, b=a1) -> 0, <0 or >0 in a0.
strcmp:
.Lsc_loop:
    lbu  t0, 0(a0)
    lbu  t1, 0(a1)
    bne  t0, t1, .Lsc_diff
    beqz t0, .Lsc_equal
    addi a0, a0, 1
    addi a1, a1, 1
    j    .Lsc_loop
.Lsc_diff:
    sub  a0, t0, t1
    ret
.Lsc_equal:
    li   a0, 0
    ret
