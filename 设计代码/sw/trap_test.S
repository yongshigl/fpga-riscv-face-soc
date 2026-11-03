    .section .text
    .globl _start
_start:
    la   t0, trap_entry
    csrw mtvec, t0
    csrw mscratch, zero
    ecall
after_ecall:
    csrr t0, mscratch
    li   t1, 1
    bne  t0, t1, fail

    li   t0, 0x80
    csrw mie, t0
    csrsi mstatus, 8
wait_irq:
    csrr t0, mscratch
    li   t1, 2
    bne  t0, t1, wait_irq

    li   a0, 0x600d
    sw   a0, 256(zero)
    ebreak

trap_entry:
    csrr t0, mcause
    bgez t0, exception_path
    li   t1, 0x80000007
    bne  t0, t1, fail
    li   t0, 2
    csrw mscratch, t0
    li   t0, 0x80
    csrc mie, t0
    mret

exception_path:
    li   t1, 11
    bne  t0, t1, fail
    csrr t0, mepc
    addi t0, t0, 4
    la   t1, after_ecall
    bne  t0, t1, fail
    li   t0, 1
    csrw mscratch, t0
    csrw mepc, t1
    mret

fail:
    li   a0, 0xdead
    sw   a0, 256(zero)
    ebreak
