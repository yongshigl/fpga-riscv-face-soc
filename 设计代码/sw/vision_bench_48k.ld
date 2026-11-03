/* SUPERSEDED — keep only to reproduce the §7.5 experiment. Do NOT ship this one.
   It was created on the theory that a 48 KiB ROM (IMEM_WORDS=12288) would cost 11 DRM
   blocks instead of 64 KiB's 15. Measurement killed that theory: Pango rounds an
   inferred block-RAM depth up to 16,384 words, so 12,288 words still mapped to DRM 77,
   byte-for-byte the same as 64 KiB. There is no useful size between 32 KiB and 64 KiB.
   The shipped Q53 bitstream uses 32 KiB (vision_bench_32k.ld + IMEM_WORDS=8192) with
   an -Os image of 8,054 words. See 设计文档_赛题一_20261029.md §7.5. */
ENTRY(_start)
MEMORY {
    ROM (rx) : ORIGIN = 0x0, LENGTH = 48K
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
