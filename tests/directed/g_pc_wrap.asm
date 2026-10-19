.text
.globl main
main:
    beq  r10, r0, far
    halt
.org 0xFFFE
far:
    addi r1, r1, 1
    addi r10, r0, 1
