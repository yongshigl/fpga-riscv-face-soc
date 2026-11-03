#ifndef CORE_PORTME_H
#define CORE_PORTME_H

#define HAS_FLOAT 0
#define HAS_TIME_H 0
#define USE_CLOCK 0
#define HAS_STDIO 0
#define HAS_PRINTF 0

#define COMPILER_VERSION "GCC " __VERSION__
#define COMPILER_FLAGS FLAGS_STR
#define MEM_LOCATION "STATIC RAM"

typedef signed short ee_s16;
typedef unsigned short ee_u16;
typedef signed int ee_s32;
typedef unsigned char ee_u8;
typedef unsigned int ee_u32;
typedef ee_u32 ee_ptr_int;
typedef ee_u32 ee_size_t;
typedef ee_u32 CORE_TICKS;

#define CORETIMETYPE ee_u32
#define NULL ((void *)0)
#define align_mem(value) (void *)(4 + (((ee_ptr_int)(value) - 1) & ~3))

#define SEED_METHOD SEED_VOLATILE
#define MEM_METHOD MEM_STATIC
#define MULTITHREAD 1
#define USE_PTHREAD 0
#define USE_FORK 0
#define USE_SOCKET 0
#define MAIN_HAS_NOARGC 1
#define MAIN_HAS_NORETURN 0

extern ee_u32 default_num_contexts;

typedef struct CORE_PORTABLE_S {
    ee_u8 portable_id;
} core_portable;

void portable_init(core_portable *portable, int *argc, char *argv[]);
void portable_fini(core_portable *portable);

#if !defined(PROFILE_RUN) && !defined(PERFORMANCE_RUN) && !defined(VALIDATION_RUN)
#define PERFORMANCE_RUN 1
#endif

int ee_printf(const char *format, ...);

#endif
