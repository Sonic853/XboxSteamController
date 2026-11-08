#ifndef XSC_ABI_H
#define XSC_ABI_H
#include <stdint.h>
#include <stddef.h>

/* Original Thumb entry points are version-bound by tools/build.py. */
#define FN(address, result, ...) ((result (*)(__VA_ARGS__))((uintptr_t)(address) | 1u))
#define BYTE(address) (*(volatile uint8_t *)(uintptr_t)(address))
#define WORD(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define HALF(address) (*(volatile uint16_t *)(uintptr_t)(address))
#define EXPORT __attribute__((used, externally_visible))

static inline uint16_t get16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | (uint16_t)p[1] << 8);
}
static inline uint32_t get32(const uint8_t *p) {
    return (uint32_t)get16(p) | (uint32_t)get16(p + 2) << 16;
}
static inline void put16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}
static inline void put32(uint8_t *p, uint32_t v) {
    put16(p, (uint16_t)v); put16(p + 2, (uint16_t)(v >> 16));
}
void *memset(void *dst, int value, size_t length);
void *memcpy(void *dst, const void *src, size_t length);
#endif
