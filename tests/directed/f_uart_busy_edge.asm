# UART busy edge: sample STATUS on the last busy cycle (write MEM 6 + 10)
# and on the first idle cycle (delta 11).  Busy must read 1 then 0.
.text
.globl main
main:
    li t0, UART_TX
    li t1, 0x0041
    sw t1, 0(t0)
    addi r9, r0, 1
    addi r10, r0, 2
    addi r11, r0, 3
    addi r12, r0, 4
    addi r13, r0, 5
    addi r9, r0, 6
    addi r10, r0, 7
    addi r11, r0, 8
    li t3, UART_STATUS
    lw r12, 0(t3)
    lw r13, 0(t3)
    li t2, 0x0100
    sw r12, 0(t2)
    sw r13, 2(t2)
    halt

