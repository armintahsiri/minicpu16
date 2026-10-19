# Bubble sort of 16 signed 16-bit values, ascending.
.data
.align 1
arr:
    .word -5
    .word 30000
    .word 0
    .word -30000
    .word 42
    .word 7
    .word -1234
    .word 256
    .word 100
    .word -1
    .word 4096
    .word 999
    .word 1234
    .word -32768
    .word 20000
    .word 1

.text
.globl main
main:
    addi sp, sp, -12
    sw   lr, 10(sp)
    sw   s0, 8(sp)
    sw   s1, 6(sp)
    sw   s2, 4(sp)
    sw   s3, 2(sp)
    sw   s4, 0(sp)
    la   s0, arr
    li   s1, 16
    li   s2, 0          # i
.Louter:
    addi t0, s1, -1
    bgeu s2, t0, .Ldone
    li   s3, 0          # j
    sub  s4, s1, s2
    addi s4, s4, -1     # n-1-i
.Linner:
    bgeu s3, s4, .Lnext_i
    slli t0, s3, 1
    add  t0, s0, t0
    lw   t1, 0(t0)
    lw   t2, 2(t0)
    bgt  t1, t2, .Lswap
    j    .Lnoswap
.Lswap:
    sw   t2, 0(t0)
    sw   t1, 2(t0)
.Lnoswap:
    addi s3, s3, 1
    j    .Linner
.Lnext_i:
    addi s2, s2, 1
    j    .Louter
.Ldone:
    lw   s4, 0(sp)
    lw   s3, 2(sp)
    lw   s2, 4(sp)
    lw   s1, 6(sp)
    lw   s0, 8(sp)
    lw   lr, 10(sp)
    addi sp, sp, 12
    li   a0, 0
    ret
