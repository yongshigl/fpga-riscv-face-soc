    .section .text.start
    .globl _start
_start:
    li sp, 0x10008000
    la t0, _bss_start
    la t1, _bss_end
1:
    bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:
    la t0, _trap
    csrw mtvec, t0
    call main
3:
    j 3b

    .align 2
_trap:
    csrr a0, mcause
    csrr a1, mepc
    csrr a2, mtval
    call firmware_trap
4:
    j 4b
