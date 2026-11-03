#ifndef VISION_NORMALIZE_H
#define VISION_NORMALIZE_H

#include <stdint.h>

static inline uint32_t vision_normalize_reciprocal(uint32_t denominator) {
    return (uint32_t)((UINT64_C(1) << 46) / denominator);
}

static inline uint32_t vision_normalize_magnitude(uint32_t magnitude, uint32_t denominator,
                                                uint32_t reciprocal) {
    uint32_t normalized = (uint32_t)(((uint64_t)magnitude * reciprocal + 32768u) >> 16);
    uint64_t numerator = ((uint64_t)magnitude << 30) + denominator / 2u;
    uint64_t product = (uint64_t)normalized * denominator;
    if (product > numerator) --normalized;
    else if (numerator - product >= denominator) ++normalized;
    return normalized > 32767u ? 32767u : normalized;
}

#endif
