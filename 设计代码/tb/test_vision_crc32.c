#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include "vision_crc32.h"

static uint64_t checked_cases;
static uint32_t random_state = 0x26102503u;

static uint32_t next_random(void) {
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}

static uint32_t reference_byte(uint32_t crc, uint8_t value) {
    crc ^= value;
    for (unsigned bit = 0; bit < 8; ++bit)
        crc = crc & 1u ? (crc >> 1) ^ 0xedb88320u : crc >> 1;
    return crc;
}

static int check(uint32_t crc, uint8_t value) {
    uint32_t expected = reference_byte(crc, value);
    uint32_t actual = vision_crc32_byte(crc, value);
    ++checked_cases;
    if (actual != expected) {
        fprintf(stderr, "FAIL crc=%08" PRIx32 " byte=%02x expected=%08" PRIx32
                " actual=%08" PRIx32 "\n", crc, (unsigned)value, expected, actual);
        return 1;
    }
    return 0;
}

int main(void) {
    static const uint8_t message[] = "123456789";
    uint32_t crc = UINT32_MAX;
    for (unsigned index = 0; index < sizeof(message) - 1; ++index)
        crc = vision_crc32_byte(crc, message[index]);
    if (~crc != 0xcbf43926u) return 1;
    for (uint32_t state = 0; state < 65536u; ++state)
        for (unsigned value = 0; value < 256; ++value)
            if (check(state, (uint8_t)value)) return 1;
    for (unsigned bit = 0; bit < 32; ++bit)
        for (unsigned value = 0; value < 256; ++value)
            if (check(UINT32_C(1) << bit, (uint8_t)value)) return 1;
    for (unsigned index = 0; index < 1000000; ++index) {
        uint32_t state = next_random();
        uint8_t value = (uint8_t)next_random();
        if (check(state, value)) return 1;
    }
    printf("PASS exact CRC32 byte update cases=%" PRIu64 "\n", checked_cases);
    return 0;
}
