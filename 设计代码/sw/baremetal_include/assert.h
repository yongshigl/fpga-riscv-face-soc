#ifndef FPGA_BAREMETAL_ASSERT_H
#define FPGA_BAREMETAL_ASSERT_H
#ifndef NDEBUG
#error "Bare-metal miniz requires NDEBUG"
#endif
#define assert(expression) ((void)0)
#endif
