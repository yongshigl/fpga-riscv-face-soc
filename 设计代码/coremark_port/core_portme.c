#include "coremark.h"

#ifndef ITERATIONS
#define ITERATIONS 1
#endif

#ifndef CLOCKS_PER_SEC
#define CLOCKS_PER_SEC 50000000u
#endif

volatile ee_s32 seed1_volatile = 0;
volatile ee_s32 seed2_volatile = 0;
volatile ee_s32 seed3_volatile = 0x66;
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;

static CORE_TICKS start_ticks;
static CORE_TICKS stop_ticks;
ee_u32 default_num_contexts = 1;

static CORE_TICKS read_cycle(void)
{
    CORE_TICKS value;
    __asm__ volatile ("csrr %0, cycle" : "=r" (value));
    return value;
}

void start_time(void)
{
    start_ticks = read_cycle();
}

void stop_time(void)
{
    stop_ticks = read_cycle();
}

CORE_TICKS get_time(void)
{
    return stop_ticks - start_ticks;
}

secs_ret time_in_secs(CORE_TICKS ticks)
{
    return ticks / CLOCKS_PER_SEC;
}

void portable_init(core_portable *portable, int *argc, char *argv[])
{
    (void)argc;
    (void)argv;
    portable->portable_id = 1;
}

void portable_fini(core_portable *portable)
{
    portable->portable_id = 0;
}
