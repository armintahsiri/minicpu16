# Hello over UART with polling.
.data
.align 1
msg: .asciz "Hello, MiniCPU-16!\n"

.text
.globl main
main:
    la   t0, msg
.Lhello_loop:
    lbu  t1, 0(t0)
    beqz t1, .Lhello_done
    li   t2, UART_STATUS
.Lhello_wait:
    lw   t3, 0(t2)
    andi t3, t3, 1
    bnez t3, .Lhello_wait
    li   t2, UART_TX
    sw   t1, 0(t2)
    addi t0, t0, 1
    j    .Lhello_loop
.Lhello_done:
    li   a0, 0
    ret
