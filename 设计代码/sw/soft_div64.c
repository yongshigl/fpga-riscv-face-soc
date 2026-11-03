#include <stdint.h>

/* The toolchain's RV32IM libgcc uses DIVU inside its 64-bit divider. */
uint64_t __udivmoddi4(uint64_t numerator, uint64_t denominator, uint64_t *remainder) {
    uint64_t quotient = 0, bit = 1;
    if (denominator == 0) {
        if (remainder) *remainder = numerator;
        return UINT64_MAX;
    }
    while (denominator <= (numerator >> 1) && !(denominator >> 63)) {
        denominator <<= 1;
        bit <<= 1;
    }
    do {
        if (numerator >= denominator) {
            numerator -= denominator;
            quotient |= bit;
        }
        denominator >>= 1;
        bit >>= 1;
    } while (bit);
    if (remainder) *remainder = numerator;
    return quotient;
}

uint64_t __udivdi3(uint64_t numerator, uint64_t denominator) {
    return __udivmoddi4(numerator, denominator, 0);
}

uint64_t __umoddi3(uint64_t numerator, uint64_t denominator) {
    uint64_t remainder;
    __udivmoddi4(numerator, denominator, &remainder);
    return remainder;
}

int64_t __divdi3(int64_t numerator, int64_t denominator) {
    uint64_t first = numerator < 0 ? 0u - (uint64_t)numerator : (uint64_t)numerator;
    uint64_t second = denominator < 0 ? 0u - (uint64_t)denominator : (uint64_t)denominator;
    uint64_t quotient = __udivdi3(first, second);
    return (int64_t)((numerator < 0) != (denominator < 0) ? 0u - quotient : quotient);
}
