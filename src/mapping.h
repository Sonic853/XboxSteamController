#ifndef XSC_MAPPING_H
#define XSC_MAPPING_H

#include "abi.h"

#define MAPPING_SIZE 24u
#define MAPPING_BINDING_COUNT 16u
#define MAPPING_INVERT_RX 0x01u
#define MAPPING_INVERT_RY 0x02u
#define MAPPING_REQUIRE_LEFT_CLICK 0x04u

enum mapping_target {
    MAPPING_NONE, MAPPING_A, MAPPING_B, MAPPING_X, MAPPING_Y,
    MAPPING_LB, MAPPING_RB, MAPPING_BACK, MAPPING_START, MAPPING_GUIDE,
    MAPPING_L3, MAPPING_R3, MAPPING_UP, MAPPING_DOWN, MAPPING_LEFT,
    MAPPING_RIGHT, MAPPING_LT, MAPPING_RT, MAPPING_TARGET_COUNT
};

/* Version 1 wire record: flags, 16 bindings, three little-endian u16 values.
 * All buffers contain exactly MAPPING_SIZE bytes; no alignment is required. */
void mapping_default(uint8_t mapping[24]);
int mapping_valid(const uint8_t mapping[24]);
/* CRC32/IEEE over all 24 bytes, including version/flags. No validation. */
uint32_t mapping_token(const uint8_t mapping[24]);

#endif
