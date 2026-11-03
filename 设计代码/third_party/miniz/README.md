# miniz 3.0.2

Source: https://github.com/richgel999/miniz/tree/3.0.2

The upstream files and MIT license are preserved. Only the `tinfl` decoder is
compiled, with allocation, compression, archives, stdio and time disabled.
`miniz_export.h` supplies the empty export declaration for a static bare-metal
build. The compiler has no C library headers; `sw/baremetal_include` supplies
the required memory-function declarations and requires disabled assertions and
allocation. The host uses Python's standard `zlib` implementation.

Each request contains a complete zlib stream. The firmware checks its Adler-32,
exact input consumption and bounded output length before writing shared DDR.
