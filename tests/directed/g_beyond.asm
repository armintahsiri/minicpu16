.text
.globl main
main:
    li   r1, 0xFFF0
    jalr r0, r1, 0
    halt
