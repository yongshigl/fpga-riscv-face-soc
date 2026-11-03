/* 32 KiB instruction ROM: the size the Q53 bitstream actually has (IMEM_WORDS=8192).
   The op-22 image is 8,053 words at -Os, so it fits with ~550 B to spare; the linker
   refuses to place code past 32 KiB instead of letting the ROM silently wrap.
   Pair this script with vision_soc.v's .IMEM_WORDS(8192).

   Why 32 KiB and not 64 KiB: Pango rounds an inferred block-RAM depth up to 16,384 words
   for anything between 8,193 and 16,384 words, so 48 KiB and 64 KiB cost the same 15 DRM
   blocks and the 2026-10-29 build at that size was unroutable (see 设计文档 §7.5).
   8,192 words costs 8 blocks, and that geometry routes. */
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
