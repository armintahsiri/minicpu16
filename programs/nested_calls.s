# Nested calls with callee-saved registers: outer(5) = 34.
.data
.align 1
out: .word 0

.text
.globl leaf
leaf:
    addi a0, a0, 1
    ret

.globl middle
middle:
    addi sp, sp, -4
    sw   lr, 2(sp)
    sw   s0, 0(sp)
    mv   s0, a0
    call leaf
    add  a0, a0, s0
    lw   s0, 0(sp)
    lw   lr, 2(sp)
    addi sp, sp, 4
    ret

.globl outer
outer:
    addi sp, sp, -6
    sw   lr, 4(sp)
    sw   s1, 2(sp)
    sw   s0, 0(sp)
    call middle
    mv   s0, a0
    call middle
    add  a0, a0, s0
    lw   s0, 0(sp)
    lw   s1, 2(sp)
    lw   lr, 4(sp)
    addi sp, sp, 6
    ret

.globl main
main:
    addi sp, sp, -2
    sw   lr, 0(sp)
    li   a0, 5
    call outer
    la   t0, out
    sw   a0, 0(t0)
    li   a0, 0
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret
