#include "coremark.h"
#include <stdarg.h>

#define UART_DATA (*(volatile ee_u32 *)0x20000004u)
#define UART_STAT (*(volatile ee_u32 *)0x20000008u)
#define GPIO_OUT  (*(volatile ee_u32 *)0x20000000u)

static void put_char(char value)
{
    while ((UART_STAT & 1u) == 0u) {
    }
    UART_DATA = (ee_u32)(ee_u8)value;
}

static int starts_with(const char *value, const char *prefix)
{
    while (*prefix != '\0') {
        if (*value++ != *prefix++)
            return 0;
    }
    return 1;
}

static int print_string(const char *value)
{
    int count = 0;
    while (*value != '\0') {
        put_char(*value++);
        count++;
    }
    return count;
}

static int print_unsigned(ee_u32 value, ee_u32 base, int width, char pad)
{
    char buffer[16];
    int length = 0;
    int count = 0;

    do {
        ee_u32 digit = value % base;
        buffer[length++] = digit < 10u ? (char)('0' + digit) : (char)('a' + digit - 10u);
        value /= base;
    } while (value != 0u);

    while (length < width) {
        put_char(pad);
        width--;
        count++;
    }
    while (length > 0) {
        put_char(buffer[--length]);
        count++;
    }
    return count;
}

int ee_printf(const char *format, ...)
{
    va_list arguments;
    int count = 0;

    if (starts_with(format, "Correct operation validated"))
        GPIO_OUT = 0x0000c0deu;
    if (starts_with(format, "Errors detected"))
        GPIO_OUT = 0x0000deadu;

    va_start(arguments, format);
    while (*format != '\0') {
        int width = 0;
        char pad = ' ';
        int long_value = 0;

        if (*format != '%') {
            put_char(*format++);
            count++;
            continue;
        }
        format++;
        if (*format == '0') {
            pad = '0';
            format++;
        }
        while (*format >= '0' && *format <= '9') {
            width = width * 10 + (*format - '0');
            format++;
        }
        if (*format == 'l') {
            long_value = 1;
            format++;
        }
        switch (*format) {
            case 's': count += print_string(va_arg(arguments, const char *)); break;
            case 'c': put_char((char)va_arg(arguments, int)); count++; break;
            case 'd': {
                ee_s32 value = va_arg(arguments, ee_s32);
                if (value < 0) {
                    put_char('-');
                    count++;
                    value = -value;
                }
                count += print_unsigned((ee_u32)value, 10u, width, pad);
                break;
            }
            case 'u':
                count += print_unsigned(long_value ? va_arg(arguments, ee_u32) : va_arg(arguments, ee_u32), 10u, width, pad);
                break;
            case 'x':
                count += print_unsigned(long_value ? va_arg(arguments, ee_u32) : va_arg(arguments, ee_u32), 16u, width, pad);
                break;
            case '%': put_char('%'); count++; break;
            default: put_char('%'); put_char(*format); count += 2; break;
        }
        if (*format != '\0')
            format++;
    }
    va_end(arguments);
    return count;
}
