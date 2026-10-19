# Function pointers: call addfn and subfn through registers.
.data
.align 1
r1: .word 0
r2: .word 0

.text
.globl addfn
addfn:
    add  a0, a0, a1
    ret

.globl subfn
subfn:
    sub  a0, a0, a1
    ret

.globl main
main:
    addi sp, sp, -2
    sw   lr, 0(sp)
    la   t0, addfn
    li   a0, 20
    li   a1, 22
    jalr lr, t0, 0
    la   t0, r1
    sw   a0, 0(t0)
    la   t0, subfn
    li   a0, 50
    li   a1, 8
    jalr lr, t0, 0
    la   t0, r2
    sw   a0, 0(t0)
    li   a0, 0
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret
