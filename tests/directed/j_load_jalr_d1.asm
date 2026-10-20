.text
.globl main
main:
    li   r1, 0x0100
    la   r2, target
    sw   r2, 0(r1)
    lw   r3, 0(r1)
    jalr r0, r3, 0
    addi r4, r0, 1
target:
    addi r5, r0, 2
    halt
