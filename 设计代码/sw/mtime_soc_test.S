    .section .text
    .globl _start
_start:
    la t0, trap_handler
    csrw mtvec, t0
    li s0, 0x20000000
    li s9, 0
    li t0, -1
    sw t0, 0x18(s0)
    sw t0, 0x1c(s0)

    li t0, 0x12345678
    sw t0, 0x14(s0)
    li t0, -16
    sw t0, 0x10(s0)
    li t0, 32
1:  addi t0, t0, -1
    bnez t0, 1b
    lw t0, 0x14(s0)
    li t1, 0x12345679
    bne t0, t1, failed

    li t0, -1
    sw t0, 0x14(s0)
    li t0, -16
    sw t0, 0x10(s0)
    li t0, 32
2:  addi t0, t0, -1
    bnez t0, 2b
    lw t0, 0x14(s0)
    bnez t0, failed

    li t0, 0x55667788
    sw t0, 0x14(s0)
    li t0, 0x11112222
    sw t0, 0x10(s0)
    li t0, 0xaa
    sb t0, 0x14(s0)
    lw t0, 0x14(s0)
    li t1, 0x556677aa
    bne t0, t1, failed
    li t0, 0xbbcc
    sh t0, 0x14(s0)
    lw t0, 0x14(s0)
    li t1, 0x5566bbcc
    bne t0, t1, failed
    li t0, 0x33
    sb t0, 0x10(s0)
    sh t0, 0x10(s0)

    sw zero, 0x14(s0)
    li t0, 0x100
    sw t0, 0x10(s0)
    sw zero, 0x1c(s0)
    lw t0, 0x10(s0)
    addi t0, t0, 200
    sw t0, 0x18(s0)
    li t0, 0x80
    csrw mie, t0
    csrsi mstatus, 8
3:  beqz s9, 3b
    li t0, 1
    bne s9, t0, failed
    li t0, 0x600d
    sw t0, 0(s0)
4:  j 4b

trap_handler:
    csrr t0, mcause
    li t1, 0x80000007
    bne t0, t1, failed
    csrw mie, zero
    li t0, -1
    sw t0, 0x1c(s0)
    sw t0, 0x18(s0)
    addi s9, s9, 1
    mret
failed:
    li t0, 0xbad
    sw t0, 0(s0)
5:  j 5b
