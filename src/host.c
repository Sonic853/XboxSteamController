#include "abi.h"
#include "bond_store.h"
#include "mapping.h"

#define STATE_MAGIC 0x31485358u
#define PROFILE_MARKER 0x58420180u
#define BUTTON_RB 6u /* Original GPIO mapping P1_14; BLE buttons bit 2. */
#define WRITE_BUFFER ((uint8_t *)0x10001264u)

struct host_state {
    uint32_t magic;
    uint8_t xbox, transport, pairing, first_frame;
    uint32_t last_store_error;
    uint32_t stored_generation;
};
/* The linker reserves exactly 16 bytes by lowering both application MSP words.
 * Original zero-init covers this area. There are no additional RAM globals. */
volatile struct host_state host_state;
_Static_assert(sizeof(struct host_state) == 16, "host scratch ABI");

static int xbox(void) { return host_state.magic == STATE_MAGIC && host_state.xbox == 1; }
static int button(uint32_t id) { return FN(0x8034, int, uint32_t)(id) != 0; }

static uint32_t irq_save(void) {
    uint32_t prior;
    __asm volatile("mrs %0, primask\n\tcpsid i" : "=r"(prior) :: "memory");
    return prior;
}
static void irq_restore(uint32_t prior) {
    __asm volatile("msr primask, %0" :: "r"(prior) : "memory");
}

static void raw_settings(void) {
    FN(0x50ac, void, void)();
    FN(0x52fc, void, uint32_t, uint32_t)(49, 2);
    FN(0x52fc, void, uint32_t, uint32_t)(7, 7);
    FN(0x52fc, void, uint32_t, uint32_t)(8, 7);
    FN(0x52fc, void, uint32_t, uint32_t)(48, 0x18);
}

EXPORT void host_connection(uint32_t connected) {
    FN(0x7a40, void, uint32_t)(connected);
    if (xbox()) {
        raw_settings();
        host_state.first_frame = connected != 0;
    }
}

EXPORT void host_selector(void) {
    memset((void *)&host_state, 0, sizeof(host_state));
    if (!button(BUTTON_RB) || !button(10)) {
        FN(0x64b4, void, void)();
        return;
    }
    host_state.magic = STATE_MAGIC;
    host_state.xbox = 1;
    host_state.transport = 3;
    host_state.pairing = button(2) || !bond_store_present();
    host_state.stored_generation = bond_store_generation();
    BYTE(0x100000d0) = host_state.pairing ? 2 : 0;
    BYTE(0x100000d1) = 0;
    WORD(0x100000dc) = 0;
    WORD(0x100000e0) = host_state.pairing ? 4000 : 2000;
    FN(0xd7e4, void, void (*)(uint32_t))(host_connection);
}

EXPORT const volatile uint8_t *host_transport(void) {
    return xbox() ? &host_state.transport : (const volatile uint8_t *)0x10000998u;
}

EXPORT void host_radio_start(void) {
    if (xbox()) raw_settings();
    FN(0xd560, void, void)();
}

/* Official 8e/reset can restore setting8=0 after startup/connection callbacks.
 * Restore the profile immediately before cd3c tests the raw-input gate. */
EXPORT uint32_t host_input_build(uint32_t type) {
    if (xbox() && type == 0 &&
        (FN(0x524c, uint32_t, uint32_t)(8) != 7 ||
         FN(0x524c, uint32_t, uint32_t)(7) != 7 ||
         FN(0x524c, uint32_t, uint32_t)(49) != 2 ||
         FN(0x524c, uint32_t, uint32_t)(48) != 0x18)) raw_settings();
    return FN(0xcd3c, uint32_t, uint32_t)(type);
}

/* Both 0xd6d8 and 0xd7b2 have this exact four-register ABI. */
EXPORT int host_config_read(uint32_t slot, uint8_t *destination,
                            uint32_t offset, uint32_t length) {
    if (!xbox()) return FN(0x6bc8, int, uint32_t, uint8_t *, uint32_t, uint32_t)
                         (slot, destination, offset, length);
    return bond_store_read(destination, offset, length, host_state.pairing);
}

EXPORT void host_reply_send(uint8_t *packet, uint32_t length) {
    if (xbox() && length == 137 && packet[0] == 0x5d &&
        get32(packet + 1) == 0 && get32(packet + 5) == 128)
        put32(packet + 5, PROFILE_MARKER);
    FN(0xd400, void, uint8_t *, uint32_t)(packet, length);
}

EXPORT void host_startup_send(uint8_t *packet, uint32_t length) {
    if (xbox() && length == 137 && packet[0] == 0x5d) {
        put32(packet + 1, 0);
        put32(packet + 5, PROFILE_MARKER);
    }
    FN(0xd400, void, uint8_t *, uint32_t)(packet, length);
}

/* Reply always describes the committed journal, including errors/conflicts. */
__attribute__((noinline)) static void mapping_reply(uint32_t status) {
    uint8_t reply[37];
    reply[0] = 0x5d;
    put32(reply + 1, MAPPING_RPC_MAGIC);
    put32(reply + 5, status);
    mapping_store_read(reply + 9);
    put32(reply + 33, mapping_token(reply + 9));
    FN(0xd400, void, uint8_t *, uint32_t)(reply, sizeof(reply));
}

static void release_write_buffer(void) {
    uint32_t prior = irq_save();
    WRITE_BUFFER[0] = 0;
    irq_restore(prior);
}

/* Runs through the original event queue, not UART IRQ/PendSV. This replaces
 * the deferred 0xd8bc callback and therefore bypasses its Steam slot rotation. */
EXPORT void host_config_write(uint32_t index) {
    uint8_t *packet = WRITE_BUFFER;
    if (index == 0 && packet[0] == 0x5e && get32(packet + 1) == MAPPING_RPC_MAGIC) {
        uint32_t status = mapping_store_write(get32(packet + 9), packet + 13);
        host_state.last_store_error = status;
        if (!status) host_state.stored_generation = bond_store_generation();
        /* Hold the buffer busy through reply, avoiding a second transaction's
         * request overwriting the completed one before its ACK is enqueued. */
        mapping_reply(status);
        release_write_buffer();
        return;
    }
    if (!xbox()) { FN(0xd8bc, void, uint32_t)(index); return; }
    if (index != 0) { host_state.last_store_error = BOND_ERROR_RANGE; return; }
    if (packet[0] != 0x5e) return;
    uint32_t status = bond_store_write(get32(packet + 1), packet + 9,
                                       get32(packet + 5), host_state.pairing);
    host_state.last_store_error = status;
    if (!status) {
        host_state.pairing = 0;
        host_state.stored_generation = bond_store_generation();
    }
    release_write_buffer();
}

/* Called before original 0xd604 receives a packet. Prevent its unchecked 5e
 * length copy from overrunning the sole 137-byte buffer in the Xbox branch. */
EXPORT void host_uart_receive(uint8_t *packet, uint32_t length) {
    if (length >= 5 && (packet[0] == 0x5d || packet[0] == 0x5e) &&
        get32(packet + 1) == MAPPING_RPC_MAGIC) {
        if (packet[0] == 0x5d) {
            mapping_reply(length == 9 && get32(packet + 5) == MAPPING_SIZE ? 0 : MAPPING_INVALID);
            return;
        }
        if (length != 37 || get32(packet + 5) != MAPPING_SIZE + 4 || !mapping_valid(packet + 13)) {
            mapping_reply(MAPPING_INVALID);
            return;
        }
        uint32_t prior = irq_save();
        if (WRITE_BUFFER[0]) {
            irq_restore(prior);
            mapping_reply(MAPPING_BUSY);
            return;
        }
        memcpy(WRITE_BUFFER, packet, length);
        irq_restore(prior);
        if (!FN(0x7e10, uint32_t, void (*)(uint32_t), uint32_t)(host_config_write, 0)) {
            release_write_buffer();
            mapping_reply(MAPPING_BUSY);
        }
        return; /* successful enqueue is NOT a successful commit ACK */
    }
    if (xbox() && length && packet[0] == 0x5e) {
        if (length < 9) return;
        uint32_t offset = get32(packet + 1), size = get32(packet + 5);
        if (offset > BOND_SIZE || size > BOND_SIZE - offset ||
            size == 0 || length != size + 9) {
            host_state.last_store_error = BOND_ERROR_RANGE;
            return;
        }
    }
    FN(0xd604, void, uint8_t *, uint32_t)(packet, length);
}

EXPORT uint32_t host_full_mask(uint32_t mask) {
    if (xbox()) {
        BYTE(0x1000032b) = 1; /* original builder's forced-send flag */
        mask |= 0x03f0u; /* six control fields on every original builder tick */
        if (host_state.first_frame) {
            host_state.first_frame = 0;
            mask |= 0x0c00u; /* first frame also carries enabled accel/gyro */
        }
    }
    return mask;
}

/* Mid-function hook at 0x91d0. r5 is the builder's live delta mask, deliberately
 * changed; all original memcpy arguments and other callee-saved regs survive.
 * Each BL occurs with an 8-byte-aligned SP. */
EXPORT __attribute__((naked)) void host_snapshot_copy(void) {
    __asm volatile(
        "push {r0-r3, r4, lr}\n"
        "mov r0, r5\n"
        "bl host_full_mask\n"
        "mov r5, r0\n"
        "pop {r0-r3}\n"
        "ldr r4, =0x218d\n"
        "blx r4\n"
        "pop {r4, pc}\n");
}
