.text
.globl main
main:
    li   r1, 0x0100
    li   r2, 0x0001
    sw   r2, 0(r1)
    lw   r3, 0(r1)
    beq  r3, r2, target
    addi r4, r0, 1
target:
    addi r5, r0, 2
    halt
