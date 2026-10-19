# Recursive factorial, exercising the stack, LR saving and a nested multiply call.
# main stores 8! into result.

.data
.align 1
result: .word 0

.text
.globl fact
fact:
    li   t0, 1
    bgt  a0, t0, .Lfact_rec
    li   a0, 1
    ret
.Lfact_rec:
    addi sp, sp, -4
    sw   lr, 2(sp)
    sw   a0, 0(sp)
    addi a0, a0, -1
    call fact
    lw   t0, 0(sp)
    mv   a1, t0
    call __mulhi3
    lw   lr, 2(sp)
    addi sp, sp, 4
    ret

.globl main
main:
    addi sp, sp, -2
    sw   lr, 0(sp)
    li   a0, 8
    call fact
    la   t0, result
    sw   a0, 0(t0)
    li   a0, 0
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret
