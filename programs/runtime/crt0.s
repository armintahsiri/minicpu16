# MiniCPU-16 crt0.
# Entry point at instruction index 0.  Sets SP to the top of RAM, zeroes .bss,
# calls main(argc=0, argv=NULL), and halts.  Reset does not reload .data and does
# not clear registers, so this is the only guaranteed initialisation.

.text
.globl _start
_start:
    li   sp, RAM_BYTES
    la   t0, __bss_start
    la   t1, __bss_end
.Lbss_loop:
    bgeu t0, t1, .Lbss_done
    sb   zero, 0(t0)
    addi t0, t0, 1
    j    .Lbss_loop
.Lbss_done:
    li   a0, 0
    li   a1, 0
    call main
    halt
