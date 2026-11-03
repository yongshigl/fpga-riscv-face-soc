    .section .text
    .globl _start
_start:
    addi x1, x0, 5
    addi x2, x0, 7
    add  x3, x1, x2
    sw   x3, 0(x0)
    lw   x4, 0(x0)
    beq  x3, x4, pass
    addi x10, x0, 238
    ecall
pass:
    addi x10, x0, 12
    sw   x10, 256(x0)
    ecall
