#ifndef FPGA_BAREMETAL_STRING_H
#define FPGA_BAREMETAL_STRING_H
#include <stddef.h>
void *memcpy(void *destination, const void *source, size_t bytes);
void *memset(void *destination, int value, size_t bytes);
#endif
