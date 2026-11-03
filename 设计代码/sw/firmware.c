#include <stdint.h>

static uint32_t fibonacci(uint32_t count)
{
    uint32_t previous = 0;
    uint32_t current = 1;

    for (uint32_t index = 0; index < count; ++index) {
        uint32_t next = previous + current;
        previous = current;
        current = next;
    }

    return previous;
}

int main(void)
{
    return (int)(fibonacci(10) + 0x5600u);
}
