# Software multiply/divide edge cases.  Results are stored as 16-bit words.
.data
.align 1
res: .space 26

.text
.globl main
main:
    addi sp, sp, -2
    sw   lr, 0(sp)

    li   a0, 1234
    li   a1, 5678
    call __mulhi3
    la   t0, res
    sw   a0, 0(t0)

    li   a0, -1234
    li   a1, 5678
    call __mulhi3
    la   t0, res
    sw   a0, 2(t0)

    li   a0, 0xFFFF
    li   a1, 0xFFFF
    call __mulhi3
    la   t0, res
    sw   a0, 4(t0)

    li   a0, 50000
    li   a1, 7
    call __udivhi3
    la   t0, res
    sw   a0, 6(t0)
    sw   a1, 8(t0)

    li   a0, 50000
    li   a1, 0
    call __udivhi3
    la   t0, res
    sw   a0, 10(t0)
    sw   a1, 12(t0)

    li   a0, -5000
    li   a1, 7
    call __divhi3
    la   t0, res
    sw   a0, 14(t0)
    sw   a1, 16(t0)

    li   a0, -32768
    li   a1, -1
    call __divhi3
    la   t0, res
    sw   a0, 18(t0)
    sw   a1, 20(t0)

    li   a0, -5000
    li   a1, 7
    call __modhi3
    la   t0, res
    sw   a0, 22(t0)

    li   a0, 50000
    li   a1, 7
    call __umodhi3
    la   t0, res
    sw   a0, 24(t0)

    li   a0, 0
    lw   lr, 0(sp)
    addi sp, sp, 2
    ret
