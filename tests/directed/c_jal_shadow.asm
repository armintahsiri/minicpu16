.text
.globl main
main:
    jal  r15, target
    addi r6, r0, 1
    addi r7, r0, 1
target:
    addi r8, r0, 1
    halt
