    .section .text
    .globl _start
_start:
    la   t0, trap_entry
    csrw mtvec, t0
    li   t0, 0x800
    csrw mie, t0
    csrsi mstatus, 8
wait_irq:
    csrr t0, mscratch
    beqz t0, wait_irq
    li   a0, 0x0e17
    sw   a0, 256(zero)
done:
    j done

trap_entry:
    csrr t0, mcause
    li   t1, 0x8000000b
    bne  t0, t1, fail
    li   t0, 1
    csrw mscratch, t0
    li   t0, 0x800
    csrc mie, t0
    mret
fail:
    li   a0, 0xdead
    sw   a0, 256(zero)
    j fail
