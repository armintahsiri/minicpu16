# MiniCPU-16 software arithmetic runtime.
# ABI: arguments in a0..a3, result in a0; a1 carries the remainder for the
# division helpers.  Callee-saved registers are preserved.

.text
.globl __mulhi3
# __mulhi3(a0, a1) -> low 16 bits of a0*a1 (signed or unsigned).
__mulhi3:
    li   t0, 0
    mv   t1, a0
    mv   t2, a1
.Lmul_loop:
    beqz t2, .Lmul_done
    andi t3, t2, 1
    beqz t3, .Lmul_skip
    add  t0, t0, t1
.Lmul_skip:
    slli t1, t1, 1
    srli t2, t2, 1
    j    .Lmul_loop
.Lmul_done:
    mv   a0, t0
    ret

.globl __udivhi3
# __udivhi3(a0, a1) -> a0 quotient, a1 remainder.  Division by zero:
# quotient 0xFFFF, remainder the dividend.
__udivhi3:
    beqz a1, .Ludiv_zero
    mv   t2, a0         # dividend
    li   t0, 0          # quotient
    li   t3, 0          # remainder
    li   t1, 16
.Ludiv_loop:
    slli t3, t3, 1
    srli a2, t2, 15
    or   t3, t3, a2
    slli t2, t2, 1
    slli t0, t0, 1
    bltu t3, a1, .Ludiv_skip
    sub  t3, t3, a1
    ori  t0, t0, 1
.Ludiv_skip:
    addi t1, t1, -1
    bnez t1, .Ludiv_loop
    mv   a0, t0
    mv   a1, t3
    ret
.Ludiv_zero:
    mv   a1, a0
    li   a0, 0xFFFF
    ret

.globl __divhi3
# __divhi3(a0, a1) -> signed quotient in a0, remainder in a1.
# Division by zero: quotient 0xFFFF, remainder the dividend.
__divhi3:
    beqz a1, .Ldiv_zero
    addi sp, sp, -6
    sw   lr, 4(sp)
    sw   s0, 2(sp)
    sw   s1, 0(sp)
    li   s0, 0          # quotient sign
    li   s1, 0          # dividend sign
    bgez a0, .Ldiv_num_pos
    neg  a0, a0
    li   s1, 1
    xori s0, s0, 1
.Ldiv_num_pos:
    bgez a1, .Ldiv_den_pos
    neg  a1, a1
    xori s0, s0, 1
.Ldiv_den_pos:
    call __udivhi3
    beqz s1, .Ldiv_rem_pos
    neg  a1, a1
.Ldiv_rem_pos:
    beqz s0, .Ldiv_q_done
    neg  a0, a0
.Ldiv_q_done:
    lw   s1, 0(sp)
    lw   s0, 2(sp)
    lw   lr, 4(sp)
    addi sp, sp, 6
    ret
.Ldiv_zero:
    mv   a1, a0
    li   a0, 0xFFFF
    ret

.globl __modhi3
# __modhi3(a0, a1) -> signed remainder in a0.
__modhi3:
    addi sp, sp, -2
    sw   lr, 0(sp)
    call __divhi3
    mv   a0, a1
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret

.globl __umodhi3
# __umodhi3(a0, a1) -> unsigned remainder in a0.
__umodhi3:
    addi sp, sp, -2
    sw   lr, 0(sp)
    call __udivhi3
    mv   a0, a1
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret
