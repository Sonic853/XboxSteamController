#include "mapping.h"

void mapping_default(uint8_t mapping[24]) {
    static const uint8_t defaults[MAPPING_SIZE] = {
        1, 0x64, /* click required, 30-degree diagonal sectors */
        1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 10, 10, 0, 11, 16, 17,
        0xb8, 0x0b, 0xac, 0x0d, 0x40, 0x1f /* 3000, 3500, 8000 */
    };
    memcpy(mapping, defaults, sizeof(defaults));
}

int mapping_valid(const uint8_t mapping[24]) {
    unsigned int i;
    if (!mapping || mapping[0] != 1 || (mapping[1] & 0x08u)) return 0;
    for (i = 0; i < MAPPING_BINDING_COUNT; ++i) {
        if (mapping[2 + i] >= MAPPING_TARGET_COUNT) return 0;
    }
    return get16(mapping + 18) <= 16000 && get16(mapping + 20) <= 16000 &&
           get16(mapping + 22) >= 256 && get16(mapping + 22) <= 30000;
}

uint32_t mapping_token(const uint8_t mapping[24]) {
    uint32_t crc = 0xffffffffu;
    unsigned int i, bit;
    for (i = 0; i < MAPPING_SIZE; ++i) {
        crc ^= mapping[i];
        for (bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ ((0u - (crc & 1u)) & 0xedb88320u);
        }
    }
    return crc ^ 0xffffffffu;
}
