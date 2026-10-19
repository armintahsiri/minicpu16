.text
.globl main
main:
    la   r5, target
    jalr r5, r5, 0
    addi r6, r0, 1
    addi r7, r0, 1
target:
    addi r8, r5, 0
    halt
