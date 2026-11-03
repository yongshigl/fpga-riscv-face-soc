    .section .text
    .globl _start

    # A zero value in a non-x0 source still requests a CSR write.  Each
    # fault must preserve its destination and commit the older store only.
    .macro expect_illegal operation, csr, source
    la   s2, 1f
    li   s4, 0x55
    sw   s0, 0x124(zero)
1:  \operation s4, \csr, \source
    sw   zero, 0x120(zero)       # younger side effect skipped by the handler
    addi s0, s0, 1
    .endm

_start:
    la   t0, trap_handler
    csrw mtvec, t0
    li   t0, 0
    li   s0, 0

    expect_illegal csrrs,  cycle,   t0
    expect_illegal csrrc,  cycle,   t0
    expect_illegal csrrs,  cycleh,  t0
    expect_illegal csrrc,  cycleh,  t0
    expect_illegal csrrs,  instret, t0
    expect_illegal csrrc,  instret, t0
    expect_illegal csrrs,  instreth,t0
    expect_illegal csrrc,  instreth,t0
    expect_illegal csrrw,  cycle,   zero
    expect_illegal csrrwi, cycle,   0
    expect_illegal csrrsi, cycle,   1
    expect_illegal csrrci, cycle,   1

    li   t1, 12
    bne  s0, t1, fail

    # x0 suppresses the write for register set/clear; zimm=0 does the same
    # for immediate set/clear.  All four read-only aliases must stay legal.
    .irp csr, cycle, cycleh, instret, instreth
    csrrs  t1, \csr, zero
    csrrc  t1, \csr, zero
    csrrsi t1, \csr, 0
    csrrci t1, \csr, 0
    .endr

    # Writable CSR set/clear with zero-valued t0 still reads the old value.
    li   t1, 0xa5
    csrw mscratch, t1
    csrrs t2, mscratch, t0
    bne  t2, t1, fail
    csrrc t2, mscratch, t0
    bne  t2, t1, fail
    csrr t2, mscratch
    bne  t2, t1, fail

    # A real write of the current cycle value wins over that edge's
    # increment.  The immediately following read therefore sees the same
    # value.  Read-only forms instead allow the counter to advance.
    csrrs t1, mcycle, t0
    csrrs t2, mcycle, zero
    bne  t1, t2, fail
    csrrc t1, mcycle, t0
    csrrc t2, mcycle, zero
    bne  t1, t2, fail
    csrrs t1, mcycle, zero
    csrrs t2, mcycle, zero
    addi t1, t1, 1
    bne  t1, t2, fail
    csrrsi t1, mcycle, 0
    csrrci t2, mcycle, 0
    addi t1, t1, 1
    bne  t1, t2, fail

    # CSRRW[I] always writes, including x0/zimm=0.
    csrrw zero, mscratch, zero
    csrr t1, mscratch
    bnez t1, fail
    li   t1, 0xa5
    csrw mscratch, t1
    csrrwi zero, mscratch, 0
    csrr t1, mscratch
    bnez t1, fail

    li   a0, 0x600d
    sw   a0, 0x100(zero)
1:  j    1b

trap_handler:
    csrr t3, mcause
    li   t4, 2
    bne  t3, t4, fail
    csrr t3, mepc
    bne  t3, s2, fail
    csrr t4, mtval
    lw   t5, 0(t3)
    bne  t4, t5, fail
    li   t4, 0x55
    bne  s4, t4, fail
    addi t3, t3, 8
    csrw mepc, t3
    mret

fail:
    li   a0, 0xdead
    sw   a0, 0x100(zero)
1:  j    1b
