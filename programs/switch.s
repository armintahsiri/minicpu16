# Jump-table switch through JALR.  dispatch(0..3) returns 10,20,30,40.
.data
.align 1
table: .word case0, case1, case2, case3
out:   .space 8

.text
.globl dispatch
dispatch:
    la   t0, table
    slli t1, a0, 1
    add  t0, t0, t1
    lw   t0, 0(t0)
    jalr r0, t0, 0

case0:
    li   a0, 10
    ret
case1:
    li   a0, 20
    ret
case2:
    li   a0, 30
    ret
case3:
    li   a0, 40
    ret

.globl main
main:
    addi sp, sp, -2
    sw   lr, 0(sp)
    li   a0, 0
    call dispatch
    la   t0, out
    sw   a0, 0(t0)
    li   a0, 1
    call dispatch
    la   t0, out
    sw   a0, 2(t0)
    li   a0, 2
    call dispatch
    la   t0, out
    sw   a0, 4(t0)
    li   a0, 3
    call dispatch
    la   t0, out
    sw   a0, 6(t0)
    li   a0, 0
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret
