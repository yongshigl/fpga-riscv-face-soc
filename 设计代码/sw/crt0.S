    .section .text.start
    .globl _start
_start:
    li   sp, 0x100
    call main
    li   t0, 0x100
    sw   a0, 0(t0)
    ecall
