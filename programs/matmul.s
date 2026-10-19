# 8x8 integer matrix multiply, C = A*B (16-bit words, values are small).
.data
.align 1
A:
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
    .word 5
    .word 1
    .word 2
    .word 3
    .word 4
B:
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 3
    .word 1
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4
    .word 2
    .word 4

.bss
.align 1
C: .space 128

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
    la   s0, A
    la   s1, B
    la   s2, C
    li   s3, 0          # i
.Lmm_i:
    li   t0, 8
    bgeu s3, t0, .Lmm_done
    li   s4, 0          # j
.Lmm_j:
    li   t0, 8
    bgeu s4, t0, .Lmm_next_i
    slli t0, s3, 4
    add  t2, s0, t0     # A + i*16
    slli t0, s4, 1
    add  t3, s1, t0     # B + j*2
    li   t1, 0          # sum
    li   t0, 0          # k
.Lmm_k:
    li   a1, 8
    bgeu t0, a1, .Lmm_store
    lw   a2, 0(t2)
    lw   a3, 0(t3)
    li   a0, 0
    mv   a1, a3
.Lmm_mul:
    beqz a1, .Lmm_mul_done
    add  a0, a0, a2
    addi a1, a1, -1
    j    .Lmm_mul
.Lmm_mul_done:
    add  t1, t1, a0
    addi t2, t2, 2
    addi t3, t3, 16
    addi t0, t0, 1
    j    .Lmm_k
.Lmm_store:
    slli t2, s3, 4
    add  t2, s2, t2
    slli t3, s4, 1
    add  t2, t2, t3
    sw   t1, 0(t2)
    addi s4, s4, 1
    j    .Lmm_j
.Lmm_next_i:
    addi s3, s3, 1
    j    .Lmm_i
.Lmm_done:
    lw   s4, 0(sp)
    lw   s3, 2(sp)
    lw   s2, 4(sp)
    lw   s1, 6(sp)
    lw   s0, 8(sp)
    lw   lr, 10(sp)
    addi sp, sp, 12
    li   a0, 0
    ret
