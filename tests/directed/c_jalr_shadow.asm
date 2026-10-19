.text
.globl main
main:
    la   r5, target
    jalr r15, r5, 0
    addi r6, r0, 1
    addi r7, r0, 1
target:
    addi r8, r0, 1
    halt
