    .section .text
    .globl _start
_start:
    la   sp, 0x10008000
    la   t0, trap_entry
    csrw mtvec, t0
    li   t0, 0x20000018
    li   t1, 80
    sw   t1, 0(t0)
    sw   zero, 4(t0)
    li   t0, 0x80
    csrw mie, t0
    csrsi mstatus, 8
wait_interrupt:
    csrr t0, mscratch
    beqz t0, wait_interrupt
    li   t0, 0x20000008
wait_uart:
    lw   t1, 0(t0)
    beqz t1, wait_uart
    li   t0, 0x20000004
    li   t1, 0x41
    sw   t1, 0(t0)
    li   t0, 0x20000000
    li   t1, 0x51c0
    sw   t1, 0(t0)
done:
    j done

trap_entry:
    csrr t0, mcause
    li   t1, 0x80000007
    bne  t0, t1, fail
    li   t0, 1
    csrw mscratch, t0
    li   t0, 0x80
    csrc mie, t0
    mret
fail:
    li   t0, 0x20000000
    li   t1, 0xdead
    sw   t1, 0(t0)
    j fail
