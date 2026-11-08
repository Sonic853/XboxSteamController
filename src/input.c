#include "input.h"

_Static_assert(sizeof(struct xbox_input) == 24, "input state must remain 24 bytes");

static const uint8_t chunk_sizes[] = {3, 2, 3, 4, 4, 4, 6, 6, 8};

/* Avoid implementation-defined unsigned-to-signed conversion of wire values. */
static int16_t signed16(const uint8_t *p) {
    uint32_t value = get16(p);
    int32_t result = (int32_t)value;
    if (value & 0x8000u) result -= 65536;
    return (int16_t)result;
}

void input_reset(struct xbox_input *state) {
    memset(state, 0, sizeof(*state));
}

void input_suppress_startup(struct xbox_input *state, uint32_t buttons) {
    state->suppress = buttons & SC_BUTTON_STARTUP;
}

int input_update(struct xbox_input *state, const uint8_t *packet, size_t len) {
    uint16_t header, flags;
    size_t required = 2;
    unsigned int i;
    const uint8_t *p;

    if (!state || !packet || len < 2) return 0;
    header = get16(packet);
    if ((header & 0x000fu) != 4u || (header & 0xe000u)) return 0;
    flags = header & SC_CHUNKS_ALL;
    for (i = 0; i < sizeof(chunk_sizes); ++i) {
        if (flags & (0x10u << i)) required += chunk_sizes[i];
    }
    /* Validate the whole packet before any state write, including suppression. */
    if (len != required) return 0;

    p = packet + 2;
    if (flags & SC_CHUNK_BUTTONS) {
        state->buttons = (uint32_t)get16(p) | (uint32_t)p[2] << 16;
        p += 3;
        /* Retire the latch only after ALL selected startup keys are released. */
        if (!(state->buttons & state->suppress)) state->suppress = 0;
    }
    if (flags & SC_CHUNK_TRIGGERS) {
        state->left_trigger = p[0];
        state->right_trigger = p[1];
        p += 2;
    }
    if (flags & SC_CHUNK_EXTRA) p += 3;
    if (flags & SC_CHUNK_STICK) {
        state->stick_x = signed16(p);
        state->stick_y = signed16(p + 2);
        p += 4;
    }
    if (flags & SC_CHUNK_LEFTPAD) {
        state->left_x = signed16(p);
        state->left_y = signed16(p + 2);
        p += 4;
    }
    if (flags & SC_CHUNK_RIGHTPAD) {
        state->right_x = signed16(p);
        state->right_y = signed16(p + 2);
    }
    /* EXTRA and IMU bytes are accepted/length-checked but have no Xbox input. */
    state->received |= flags;
    return 1;
}

/* The quotient fits 15 bits. Explicit long division keeps Cortex-M0 builds
 * freestanding: no hardware divide, libgcc helper, floating point or libm. */
static uint32_t divide_axis(uint32_t numerator, uint32_t denominator) {
    uint32_t bit = 1u << 14, quotient = 0;
    denominator <<= 14;
    do {
        if (numerator >= denominator) {
            numerator -= denominator;
            quotient |= bit;
        }
        denominator >>= 1;
        bit >>= 1;
    } while (bit);
    return quotient;
}

static uint16_t axis(int16_t input, uint16_t deadzone, int invert) {
    int32_t value = input;
    uint32_t absolute, scaled;
    /* Receiver-compatible symmetric range, including safe INT16_MIN invert. */
    if (value < -32767) value = -32767;
    if (invert) value = -value;
    absolute = (uint32_t)(value < 0 ? -value : value);
    if (absolute <= deadzone) return 0x8000u;
    scaled = divide_axis((absolute - deadzone) * 32767u, 32767u - deadzone);
    return (uint16_t)(value < 0 ? 32768u - scaled : 32768u + scaled);
}

static uint16_t trigger(uint8_t value) {
    return (uint16_t)(((uint16_t)value << 2) | (value >> 6));
}

/* Direction bits: up/down/left/right = 1/2/4/8, matching binding order. */
static uint8_t pad_directions(int16_t raw_x, int16_t raw_y, uint16_t threshold,
                              uint8_t code) {
    /* tan((90 - diagonal_degrees) / 2) * 10000, rounded as in Receiver.
     * Codes: 45,5,10,15,20,25,30,35,40,0,50,55,60,65,70,75 degrees. */
    static const uint16_t ratios[16] = {
        4142, 9163, 8391, 7673, 7002, 6371, 5774, 5206,
        4663, 10000, 3640, 3153, 2679, 2217, 1763, 1317
    };
    uint32_t x = (uint32_t)(raw_x < 0 ? -(int32_t)raw_x : raw_x);
    uint32_t y = (uint32_t)(raw_y < 0 ? -(int32_t)raw_y : raw_y);
    uint8_t horizontal = raw_x < 0 ? 4 : 8;
    uint8_t vertical = raw_y < 0 ? 2 : 1;
    uint8_t directions = 0;

    /* Unsigned squares safely cover (-32768, -32768), whose sum is 2^31. */
    if (x * x + y * y < (uint32_t)threshold * threshold) return 0;
    if (code == 9) return x >= y ? horizontal : vertical;
    if (x * 10000u >= y * ratios[code]) directions |= horizontal;
    if (y * 10000u >= x * ratios[code]) directions |= vertical;
    return directions;
}

static uint8_t hat(uint8_t directions) {
    /* Opposing remaps cancel per axis, including conflicts with the pad. */
    if ((directions & 3u) == 3u) directions &= (uint8_t)~3u;
    if ((directions & 12u) == 12u) directions &= (uint8_t)~12u;
    if (directions & 1u) return directions & 8u ? 2 : (directions & 4u ? 8 : 1);
    if (directions & 2u) return directions & 8u ? 4 : (directions & 4u ? 6 : 5);
    return directions & 8u ? 3 : (directions & 4u ? 7 : 0);
}

void input_report_mapped(const struct xbox_input *state, uint8_t report[16],
                         const uint8_t mapping[24]) {
    static const uint8_t source_bits[MAPPING_BINDING_COUNT] = {
        7, 5, 6, 4, 3, 2, 12, 14, 13, 15, 16, 22, 17, 18, 1, 0
    };
    static const uint16_t target_masks[MAPPING_UP] = {
        0, 0x0001, 0x0002, 0x0008, 0x0010, 0x0040, 0x0080,
        0x0400, 0x0800, 0x1000, 0x2000, 0x4000
    };
    uint8_t defaults[MAPPING_SIZE];
    uint32_t buttons = state->buttons & ~state->suppress;
    uint16_t mapped = 0;
    uint8_t directions = 0;
    unsigned int i;

    if (!mapping_valid(mapping)) {
        mapping_default(defaults);
        mapping = defaults;
    }

    memset(report, 0, XBOX_INPUT_REPORT_SIZE);
    put16(report, axis(state->stick_x, get16(mapping + 18), 0));
    put16(report + 2, axis(state->stick_y, get16(mapping + 18), 1));
    put16(report + 4, 0x8000u);
    put16(report + 6, 0x8000u);
    if (buttons & SC_BUTTON_RIGHT_TOUCH) {
        put16(report + 4, axis(state->right_x, get16(mapping + 20),
                               mapping[1] & MAPPING_INVERT_RX));
        put16(report + 6, axis(state->right_y, get16(mapping + 20),
                               !(mapping[1] & MAPPING_INVERT_RY)));
    }
    put16(report + 8, trigger(state->left_trigger));
    put16(report + 10, trigger(state->right_trigger));
    for (i = 0; i < MAPPING_BINDING_COUNT; ++i) {
        uint8_t target;
        if (!(buttons & (1u << source_bits[i]))) continue;
        target = mapping[2 + i];
        if (target < MAPPING_UP) mapped |= target_masks[target];
        else if (target <= MAPPING_RIGHT) directions |= (uint8_t)(1u << (target - MAPPING_UP));
        else if (target == MAPPING_LT) put16(report + 8, 1023);
        else if (target == MAPPING_RT) put16(report + 10, 1023);
    }
    if ((buttons & SC_BUTTON_LEFT_TOUCH) &&
        (!(mapping[1] & MAPPING_REQUIRE_LEFT_CLICK) || (buttons & SC_BUTTON_LEFT_CLICK))) {
        directions |= pad_directions(state->left_x, state->left_y,
                                     get16(mapping + 22), mapping[1] >> 4);
    }
    report[12] = hat(directions);
    put16(report + 13, mapped);
    /* Share and all reserved/padding bits remain zero. */
}

void input_report(const struct xbox_input *state, uint8_t report[16]) {
    input_report_mapped(state, report, NULL);
}
