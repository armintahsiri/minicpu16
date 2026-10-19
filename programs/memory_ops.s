# Byte/string operations: memcpy, strlen, strcmp.
.data
.align 1
src:   .asciz "Hello, MiniCPU-16!"
other: .asciz "Hello, MiniCPU-17!"
dst:   .space 32
len:   .word 0
cmp1:  .word 0
cmp2:  .word 0

.text
.globl main
main:
    addi sp, sp, -2
    sw   lr, 0(sp)
    la   a0, dst
    la   a1, src
    li   a2, 32
    call memcpy
    la   a0, src
    call strlen
    la   t0, len
    sw   a0, 0(t0)
    la   a0, dst
    la   a1, src
    call strcmp
    la   t0, cmp1
    sw   a0, 0(t0)
    la   a0, src
    la   a1, other
    call strcmp
    la   t0, cmp2
    sw   a0, 0(t0)
    li   a0, 0
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret
