.text
.globl main
main:
    addi r0, r0, 5
    add  r1, r0, r0
    li   r2, 0x0100
    li   r3, 0x1234
    sw   r3, 0(r2)
    lw   r0, 0(r2)
    add  r4, r0, r0
    jal  r0, target
    addi r5, r0, 1
target:
    jalr r0, r2, 0
