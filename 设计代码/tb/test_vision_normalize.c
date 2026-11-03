#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include "vision_normalize.h"

static uint32_t random_state = 0x26102502u;
static uint64_t checked_cases;

static uint32_t next_random(void) {
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}

static int check(uint32_t magnitude, uint32_t denominator, uint32_t reciprocal) {
    uint64_t expected = (((uint64_t)magnitude << 30) + denominator / 2u) / denominator;
    if (expected > 32767u) expected = 32767u;
    uint32_t actual = vision_normalize_magnitude(magnitude, denominator, reciprocal);
    ++checked_cases;
    if (actual != expected) {
        fprintf(stderr, "FAIL magnitude=%" PRIu32 " denominator=%" PRIu32
                " expected=%" PRIu64 " actual=%" PRIu32 "\n", magnitude, denominator, expected, actual);
        return 1;
    }
    return 0;
}

int main(void) {
    for (uint32_t denominator = 32768; denominator <= 98304; ++denominator) {
        uint32_t reciprocal = vision_normalize_reciprocal(denominator);
        for (uint32_t magnitude = 0; magnitude <= 255; ++magnitude)
            if (check(magnitude, denominator, reciprocal)) return 1;
    }
    for (uint32_t power = 15; power <= 26; ++power) {
        for (int32_t offset = -32; offset <= 32; ++offset) {
            uint32_t denominator = (uint32_t)((INT64_C(1) << power) + offset);
            if (denominator < 32768) continue;
            uint32_t reciprocal = vision_normalize_reciprocal(denominator);
            for (uint32_t magnitude = 0; magnitude <= 255; ++magnitude)
                if (check(magnitude, denominator, reciprocal)) return 1;
        }
    }
    for (uint32_t index = 0; index < 1000000; ++index) {
        uint32_t denominator = 32768u + next_random() % (94536546u - 32768u + 1u);
        uint32_t magnitude = next_random() & 255u;
        if (check(magnitude, denominator, vision_normalize_reciprocal(denominator))) return 1;
    }
    printf("PASS exact reciprocal normalization cases=%" PRIu64 "\n", checked_cases);
    return 0;
}
