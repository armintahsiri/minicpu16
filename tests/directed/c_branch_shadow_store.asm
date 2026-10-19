.text
.globl main
main:
    li   r1, 0x0100
    li   r5, 0x1234
    beq  r0, r0, target
    sw   r5, 0(r1)
    addi r6, r0, 1
target:
    halt
