    .section .text.start
    .globl _start
_start:
    li   sp, 0x10000c00
    call main
halt:
    j halt
