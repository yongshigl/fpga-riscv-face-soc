#ifndef COREMARK_RAM_BYTES
#error COREMARK_RAM_BYTES must match the linker and physical RAM
#endif
    .section .text.start
    .globl _start
_start:
    li   sp, 0x10000000 + COREMARK_RAM_BYTES
    call main
halt:
    j halt
