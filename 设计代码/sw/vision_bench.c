#include "vision_bench.h"

/* core_main.c is compiled with -Dmain=coremark_entry so the vision firmware keeps
   the single entry point the CRT0 calls. */
extern int coremark_entry(void);

/* CoreMark's runtime iteration knob. core_portme.h selects SEED_VOLATILE, so
   get_seed_32(4) reads this variable; a hosted build writes it from `-i`. */
extern volatile signed int seed4_volatile;

void vision_bench_run(uint32_t iterations) {
    seed4_volatile = (signed int)iterations;
    (void)coremark_entry();
}
