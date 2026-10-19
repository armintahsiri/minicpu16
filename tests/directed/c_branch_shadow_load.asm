.text
.globl main
main:
    li   r1, 0x0100
    li   r5, 0x1234
    sw   r5, 0(r1)
    beq  r0, r0, target
    lw   r6, 0(r1)
    addi r7, r0, 1
target:
    li   r8, 0x2222
    halt
