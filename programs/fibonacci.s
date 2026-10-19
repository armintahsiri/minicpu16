# Iterative Fibonacci. fib(16) = 987, stored in result.
.data
.align 1
result: .word 0

.text
.globl main
main:
    li   t0, 0          # a = fib(0)
    li   t1, 1          # b = fib(1)
    li   t2, 16         # count
.Lfib_loop:
    beqz t2, .Lfib_done
    add  t3, t0, t1
    mv   t0, t1
    mv   t1, t3
    addi t2, t2, -1
    j    .Lfib_loop
.Lfib_done:
    la   t3, result
    sw   t0, 0(t3)
    li   a0, 0
    ret
