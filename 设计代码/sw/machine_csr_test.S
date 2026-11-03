    .section .text
    .globl _start

    .macro expect_readonly_illegal operation, csr
    li   t0, 0
    li   s4, 0x55
    la   s2, 1f
1:  \operation s4, \csr, t0
    addi s0, s0, 1
    .endm

_start:
    csrr t1, mstatus
    li   t2, 0x1800
    bne  t1, t2, fail
    li   t0, -1
    csrw mstatus, t0
    csrr t1, mstatus
    li   t2, 0x1888
    bne  t1, t2, fail
    csrw mstatus, zero
    csrr t1, mstatus
    li   t2, 0x1800
    bne  t1, t2, fail
    li   t0, -1
    csrrs zero, mstatus, t0
    csrr t1, mstatus
    li   t2, 0x1888
    bne  t1, t2, fail
    csrrc zero, mstatus, t0
    csrr t1, mstatus
    li   t2, 0x1800
    bne  t1, t2, fail

    li   t0, -1
    csrw mie, t0
    csrr t1, mie
    li   t2, 0x880
    bne  t1, t2, fail
    li   t0, 8                    # software IRQ is not implemented
    csrw mie, t0
    csrr t1, mie
    bnez t1, fail

    # All mtvec mode encodings have a defined WARL readback.
    li   t0, 0x12345672
    csrw mtvec, t0
    csrr t1, mtvec
    li   t2, 0x12345670
    bne  t1, t2, fail
    li   t0, 0x12345673
    csrw mtvec, t0
    csrr t1, mtvec
    bne  t1, t2, fail
    li   t0, 0x12345671
    csrw mtvec, t0
    csrr t1, mtvec
    bne  t1, t0, fail
    li   t0, 0x12345670
    csrw mtvec, t0
    csrr t1, mtvec
    bne  t1, t0, fail

    # misa is a fixed WARL value; legal CSR writes cannot enable missing M.
    csrr t1, misa
    li   t2, 0x40000100
    bne  t1, t2, fail
    li   t0, -1
    csrrw t1, misa, t0
    bne  t1, t2, fail
    csrr t1, misa
    bne  t1, t2, fail
    csrrw t1, misa, zero
    bne  t1, t2, fail
    csrr t1, misa
    bne  t1, t2, fail

    .irp csr, mvendorid, marchid, mimpid, mhartid
    csrr t1, \csr
    bnez t1, fail
    csrrsi t1, \csr, 0
    bnez t1, fail
    csrrci t1, \csr, 0
    bnez t1, fail
    .endr

    # Supported mip bits are hardware pending state.  Writes, including
    # attempted set/clear, neither invent MSIP nor clear MTIP/MEIP.
    li   t0, -1
    csrw mip, t0
    csrr t1, mip
    bnez t1, fail
    li   t0, 1
    sw   t0, 0x180(zero)          # testbench asserts MTIP and MEIP
    nop                           # allow the external pending inputs to settle
    csrr t1, mip
    li   t2, 0x880
    bne  t1, t2, fail
    csrw mip, zero
    csrr t1, mip
    bne  t1, t2, fail
    li   t0, -1
    csrrc zero, mip, t0
    csrr t1, mip
    bne  t1, t2, fail
    sw   zero, 0x180(zero)
    nop
    csrr t1, mip
    bnez t1, fail

    # mepc obeys IALIGN=32 and MRET uses the aligned value.
    la   t0, mret_target
    ori  t1, t0, 3
    csrw mepc, t1
    csrr t1, mepc
    bne  t1, t0, fail
    mret
    j    fail
mret_target:
    csrr t1, mstatus
    li   t2, 0x1880               # MPIE=1, MIE restored from former MPIE=0
    bne  t1, t2, fail

    # Trap entry saves MIE to MPIE and MRET restores it, with MPP fixed M.
    la   t0, trap_handler
    csrw mtvec, t0
    li   t0, 8
    csrw mstatus, t0
    li   s0, 0
    la   s2, ecall_test
ecall_test:
    ecall
    csrr t1, mstatus
    li   t2, 0x1888
    bne  t1, t2, fail
    csrw mstatus, zero

    .irp csr, mvendorid, marchid, mimpid, mhartid
    expect_readonly_illegal csrrs, \csr
    expect_readonly_illegal csrrc, \csr
    .endr
    li   t0, 8
    bne  s0, t0, fail
    li   a0, 0x600d
    sw   a0, 0x100(zero)
1:  j    1b

trap_handler:
    csrr t3, mepc
    bne  t3, s2, fail
    csrr t4, mcause
    li   t5, 11
    beq  t4, t5, ecall_handler
    li   t5, 2
    bne  t4, t5, fail
    csrr t4, mtval
    lw   t5, 0(t3)
    bne  t4, t5, fail
    li   t4, 0x55
    bne  s4, t4, fail
    j    resume
ecall_handler:
    csrr t4, mstatus
    li   t5, 0x1880
    bne  t4, t5, fail
resume:
    addi t3, t3, 4
    csrw mepc, t3
    mret

fail:
    li   a0, 0xdead
    sw   a0, 0x100(zero)
1:  j    1b
