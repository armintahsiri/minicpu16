# Post-synthesis (gate-level) simulation vector.  Uses EX/MEM forwarding at
# distance 1, an I-type consumer of an ALU producer, a load-use stall, and a
# taken backward branch, then writes a distinctive value to GPIO_OUT and halts.
# Registers: t0-t3 = r5-r8, s0-s4 = r9-r13.
.text
.globl main
main:
    li   t0, 5
    li   t1, 6
    add  t2, t0, t1        # EX/MEM -> EX at distance 1
    addi t3, t2, 5         # I-type consumer of the ALU producer
    add  s0, t2, t2        # 22
    add  s1, s0, t3        # MEM/WB and EX/MEM mix
    li   s2, GPIO_OUT
    sw   s1, 0(s2)         # store a forwarded value to GPIO_OUT
    li   s3, 0
    li   a0, 0x1111
    sw   a0, 8(s3)
    lw   a1, 8(s3)         # load
    add  a2, a1, t2        # load-use at distance 1 -> one stall
    li   a3, 3
    mv   t0, zero
loop:
    addi t0, t0, 1
    blt  t0, a3, loop      # taken backward branch twice
    halt
