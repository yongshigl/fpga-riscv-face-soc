#ifndef VISION_BENCH_H
#define VISION_BENCH_H

#include <stdint.h>

/* Runs the stock CoreMark benchmark inside the vision firmware image.
   The caller sets the iteration count, exactly like `-i` does on a hosted build. */
void vision_bench_run(uint32_t iterations);

#endif
