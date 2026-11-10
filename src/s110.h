#ifndef XSC_S110_H
#define XSC_S110_H
#include "abi.h"

/* S110 v8 / SDK9 ABI, NOT nRF52 SoftDevice numbers. See RADIO_IMPLEMENTATION.md.
 * A leaf SVC veneer preserves the incoming AAPCS register assignment exactly. */
#define UNUSED __attribute__((unused))
#define SVC_VENEER(name, number) \
    __attribute__((naked, noinline, unused)) static uint32_t name( \
        uint32_t a UNUSED, uint32_t b UNUSED, uint32_t c UNUSED, uint32_t d UNUSED) { \
        __asm volatile("svc %0\n bx lr" : : "I" (number)); \
    }
SVC_VENEER(sd_name, 0x7c)
SVC_VENEER(sd_appearance, 0x78)
SVC_VENEER(sd_security, 0x83)
SVC_VENEER(sd_uuid_add, 0x63)
SVC_VENEER(sd_opt_set, 0x68)
SVC_VENEER(sd_opt_get, 0x69)
SVC_VENEER(sd_ecb_encrypt, 0x4d)
SVC_VENEER(sd_service_add, 0xa0)
SVC_VENEER(sd_characteristic_add, 0xa2)
SVC_VENEER(sd_value_set, 0xa4)
SVC_VENEER(sd_value_get, 0xa5)
SVC_VENEER(sd_sysattr_get, 0xaa)
#undef SVC_VENEER
#undef UNUSED
#define PTR(p) ((uint32_t)(uintptr_t)(p))

#define GAP_OPT_PRIVACY 0x23u
struct gap_privacy_option {
    uint8_t *irk;
    uint16_t interval_s;
    uint16_t padding;
};
struct ecb_block {
    uint8_t key[16], cleartext[16], ciphertext[16];
};
_Static_assert(sizeof(struct gap_privacy_option) == 8, "S110 v8 privacy option ABI");
_Static_assert(sizeof(struct ecb_block) == 48, "S110 v8 ECB block ABI");
#endif
