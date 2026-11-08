#include "abi.h"

void *memset(void *dst, int value, size_t length) {
    uint8_t *out = dst;
    while (length--) *out++ = (uint8_t)value;
    return dst;
}
void *memcpy(void *dst, const void *src, size_t length) {
    uint8_t *out = dst;
    const uint8_t *in = src;
    while (length--) *out++ = *in++;
    return dst;
}
void __aeabi_memcpy(void *dst, const void *src, size_t length) { memcpy(dst, src, length); }
void __aeabi_memcpy4(void *dst, const void *src, size_t length) { memcpy(dst, src, length); }
void __aeabi_memclr(void *dst, size_t length) { memset(dst, 0, length); }
void __aeabi_memclr4(void *dst, size_t length) { memset(dst, 0, length); }
