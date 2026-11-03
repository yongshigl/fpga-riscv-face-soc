    .section .text
    .globl _start
    .macro check reg, value
    li t6, \value
    bne \reg, t6, failed
    .endm
_start:
    la t0, handler
    csrw mtvec, t0
    li s0, 0x20000000
    li s8, 0
    li s7, 0
    li t0, 0x11223344
    sw t0, 0(s0)
    li t0, 0xaa
    sb t0, 1(s0)
    li t0, 0xbb
    sb t0, 2(s0)
    li t0, 0xcc
    sb t0, 3(s0)
    li t0, 0x80
    sb t0, 0(s0)
    lw t0, 0(s0)
    check t0, 0xccbbaa80
    lb t0, 0(s0)
    check t0, -128
    lbu t0, 0(s0)
    check t0, 0x80
    lbu t0, 1(s0)
    check t0, 0xaa
    lbu t0, 2(s0)
    check t0, 0xbb
    lb t0, 3(s0)
    check t0, -52
    lh t0, 0(s0)
    check t0, 0xffffaa80
    lhu t0, 2(s0)
    check t0, 0xccbb
    li t0, 0xdd66
    sh t0, 2(s0)
    li t0, 0xee22
    sh t0, 0(s0)
    lw t0, 0(s0)
    check t0, 0xdd66ee22

    // Fixed compare register makes all four byte lanes independently visible.
    sw zero, 0x18(s0)
    li t0, 0x11
    sb t0, 0x18(s0)
    li t0, 0x22
    sb t0, 0x19(s0)
    li t0, 0x33
    sb t0, 0x1a(s0)
    li t0, 0x44
    sb t0, 0x1b(s0)
    lw t0, 0x18(s0)
    check t0, 0x44332211
    lbu t0, 0x19(s0)
    check t0, 0x22
    lhu t0, 0x1a(s0)
    check t0, 0x4433
    li t0, 0xabcd
    sh t0, 0x1a(s0)
    lw t0, 0x18(s0)
    check t0, 0xabcd2211
    sw zero, 0x14(s0)
    li t0, 0x100
    sw t0, 0x10(s0)
    li t0, 0x55
    sb t0, 0x14(s0)
    li t0, 0x66
    sb t0, 0x15(s0)
    li t0, 0x77
    sb t0, 0x16(s0)
    li t0, 0x88
    sb t0, 0x17(s0)
    lw t0, 0x14(s0)
    check t0, 0x88776655
    lbu t0, 0x17(s0)
    check t0, 0x88
    li t0, 0xffee
    sh t0, 0x16(s0)
    lw t0, 0x14(s0)
    check t0, 0xffee6655

    // Data-cache counters remain zero: this program only touches MMIO.
    lb t0, 0x28(s0)
    check t0, 0
    lbu t0, 0x29(s0)
    check t0, 0
    lh t0, 0x2a(s0)
    check t0, 0
    lw t0, 0x2c(s0)
    check t0, 0
    lbu t0, 0x21(s0)
    lhu t0, 0x26(s0)

    // Peripheral command side effects remain limited to byte lane zero.
    li t0, 0x41
    sb t0, 4(s0)
    li t0, 0x7a
    sb t0, 5(s0)
    sb t0, 6(s0)
    sb t0, 7(s0)
    lbu t0, 4(s0)
    check t0, 0x41
    lbu t0, 5(s0)
    check t0, 0
    li t0, 1
    sb t0, 0x31(s0)
    sb t0, 0x35(s0)
    sb t0, 0x30(s0)
    sb t0, 0x34(s0)

    // Misaligned instructions must trap before the device accepts a request.
    li s7, 6
    sh t0, 1(s0)
    sw t0, 2(s0)
    li s7, 4
    lh t0, 1(s0)
    lw t0, 1(s0)
    li s7, 5
    lw t0, 0x38(s0)
    li t1, 0x1fffffff
    lbu t0, 0(t1)
    li s7, 7
    sw t0, 0x38(s0)
    li t1, 0x2000ffff
    sb t0, 0(t1)
    li s7, 0
    check s8, 8
    lw t0, 0(s0)
    check t0, 0xdd66ee22

    // The testbench supplies three actual serial characters after this mark.
    li t0, 0x1234
    sw t0, 0(s0)
1:  lw t0, 8(s0)
    andi t0, t0, 2
    beqz t0, 1b
    lbu t0, 0x0c(s0)
    check t0, 0x5a
2:  lw t0, 8(s0)
    andi t0, t0, 2
    beqz t0, 2b
    lb t0, 0x0c(s0)
    check t0, 0xffffffc3
3:  lw t0, 8(s0)
    andi t0, t0, 2
    beqz t0, 3b
    lbu t0, 0x0d(s0)
    check t0, 0
    lw t0, 8(s0)
    andi t0, t0, 2
    bnez t0, failed
    li t0, 0x600d
    sw t0, 0(s0)
4:  j 4b
handler:
    csrr t0, mcause
    beqz s7, failed
    bne t0, s7, failed
    addi s8, s8, 1
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret
failed:
    li t0, 0xbad
    sw t0, 0(s0)
5:  j 5b
