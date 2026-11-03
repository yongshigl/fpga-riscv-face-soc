ENTRY(_start)
MEMORY {
    ROM (rx) : ORIGIN = 0x0, LENGTH = 32K
    RAM (rwx) : ORIGIN = 0x10000000, LENGTH = 32K
}
SECTIONS {
    .text : {
        *(.text.start) *(.text*) *(.rodata*)
        . = ALIGN(4);
    } > ROM
    .data : { *(.data*) *(.sdata*) } > RAM
    .bss (NOLOAD) : {
        . = ALIGN(4);
        _bss_start = .;
        *(.bss*) *(.sbss*) *(COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > RAM
    _end = .;
    ASSERT(_end <= 0x10007000, "Vision firmware requires at least 4 KiB of stack")
}
