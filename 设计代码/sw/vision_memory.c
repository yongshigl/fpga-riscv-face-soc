#include <stddef.h>

void *memcpy(void *destination, const void *source, size_t bytes) {
    unsigned char *out = destination;
    const unsigned char *in = source;
    for (size_t index = 0; index < bytes; ++index) out[index] = in[index];
    return destination;
}

void *memset(void *destination, int value, size_t bytes) {
    unsigned char *out = destination;
    for (size_t index = 0; index < bytes; ++index) out[index] = (unsigned char)value;
    return destination;
}
