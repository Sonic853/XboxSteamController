#include "abi.h"
#include "input.h"
#include "s110.h"
#include "radio_config.h"

/* Everything in this file is bound to the original radio SHA-256 by the builder. */
#define HIDS ((uint8_t *)0x2000251c)
#define CONN HALF(0x20002006)
#define PROFILE_MARKER 0x58420180u
#define DM_LINK ((const uint8_t *)0x20002f14)
#define DM_SYSATTR ((uint8_t *)0x20002ee4)
#define BOND_CACHE ((const uint8_t *)0x20002ce0)

struct radio_patch_state {
    struct xbox_input input;
    uint32_t timer[8];
    uint32_t *timer_pointer;
    volatile uint32_t ticks;
    uint32_t last_attempt;
    uint32_t last_error;
    uint8_t last_report[16];
    uint8_t profile;
    uint8_t initialized;
    uint8_t last_valid;
    uint8_t suspended;
    uint8_t boot_protocol;
    uint32_t rx, parsed, rejected, attempts, sent, services;
    uint32_t security_error, cccd_error;
    uint16_t last_length, last_header, cccd;
    uint8_t security[2], blocked;
    uint32_t cccd_writes, cccd_write_tick, sysattr_tick;
    uint16_t cccd_written, sysattr_missing, cccd_read_length;
    uint16_t cccd_enables, cccd_disables, cccd_rises, cccd_falls, cccd_previous;
    uint32_t snapshot_attempts, snapshot_queued, snapshot_error, snapshot_tick;
    uint16_t snapshot_length;
    uint8_t snapshot_pending;
};
static struct radio_patch_state state;
extern uint8_t __bss_start__, __bss_end__;

/* Xbox Series 1914 descriptor; licensed reference in docs/reference/xbox-ble/. */
static const uint8_t report_map[] = {
    0x05,0x01,0x09,0x05,0xa1,0x01,0x85,0x01,0x09,0x01,0xa1,0x00,0x09,0x30,0x09,0x31,0x15,0x00,0x27,0xff,
    0xff,0x00,0x00,0x95,0x02,0x75,0x10,0x81,0x02,0xc0,0x09,0x01,0xa1,0x00,0x09,0x32,0x09,0x35,0x15,0x00,
    0x27,0xff,0xff,0x00,0x00,0x95,0x02,0x75,0x10,0x81,0x02,0xc0,0x05,0x02,0x09,0xc5,0x15,0x00,0x26,0xff,
    0x03,0x95,0x01,0x75,0x0a,0x81,0x02,0x15,0x00,0x25,0x00,0x75,0x06,0x95,0x01,0x81,0x03,0x05,0x02,0x09,
    0xc4,0x15,0x00,0x26,0xff,0x03,0x95,0x01,0x75,0x0a,0x81,0x02,0x15,0x00,0x25,0x00,0x75,0x06,0x95,0x01,
    0x81,0x03,0x05,0x01,0x09,0x39,0x15,0x01,0x25,0x08,0x35,0x00,0x46,0x3b,0x01,0x66,0x14,0x00,0x75,0x04,
    0x95,0x01,0x81,0x42,0x75,0x04,0x95,0x01,0x15,0x00,0x25,0x00,0x35,0x00,0x45,0x00,0x65,0x00,0x81,0x03,
    0x05,0x09,0x19,0x01,0x29,0x0f,0x15,0x00,0x25,0x01,0x75,0x01,0x95,0x0f,0x81,0x02,0x15,0x00,0x25,0x00,
    0x75,0x01,0x95,0x01,0x81,0x03,0x05,0x0c,0x0a,0xb2,0x00,0x15,0x00,0x25,0x01,0x95,0x01,0x75,0x01,0x81,
    0x02,0x15,0x00,0x25,0x00,0x75,0x07,0x95,0x01,0x81,0x03,0x05,0x0f,0x09,0x21,0x85,0x03,0xa1,0x02,0x09,
    0x97,0x15,0x00,0x25,0x01,0x75,0x04,0x95,0x01,0x91,0x02,0x15,0x00,0x25,0x00,0x75,0x04,0x95,0x01,0x91,
    0x03,0x09,0x70,0x15,0x00,0x25,0x64,0x75,0x08,0x95,0x04,0x91,0x02,0x09,0x50,0x66,0x01,0x10,0x55,0x0e,
    0x15,0x00,0x26,0xff,0x00,0x75,0x08,0x95,0x01,0x91,0x02,0x09,0xa7,0x15,0x00,0x26,0xff,0x00,0x75,0x08,
    0x95,0x01,0x91,0x02,0x65,0x00,0x55,0x00,0x09,0x7c,0x15,0x00,0x26,0xff,0x00,0x75,0x08,0x95,0x01,0x91,
    0x02,0xc0,0xc0
};
_Static_assert(sizeof(report_map) == 283, "Series report map length");
_Static_assert(sizeof(state) <= 472, "patch state exceeds reserved RAM");

static void check(uint32_t result) {
    if (result) FN(0x1af68, void, uint32_t, uint32_t, uint32_t)(result, 0, 0);
}

EXPORT void radio_boot(void) {
    memset(&__bss_start__, 0, (size_t)(&__bss_end__ - &__bss_start__));
    input_reset(&state.input);
    FN(0x1d2f0, void, void)();
}

EXPORT void radio_config(uint8_t *packet, uint32_t length) {
    if (state.profile && config_receive(packet, length)) return;
    if (!state.initialized && length == 137 && packet[0] == 0x5d &&
        get32(packet + 1) == 0 && get32(packet + 5) == PROFILE_MARKER) {
        if (!state.profile) input_suppress_startup(&state.input, SC_BUTTON_STARTUP);
        state.profile = 1;
    }
    FN(0x189c8, void, uint8_t *, uint32_t)(packet, length);
}

static void tick(void *context) {
    (void)context;
    ++state.ticks;
}

EXPORT void radio_init(uint32_t erase_bonds) {
    state.initialized = 1; /* Freeze the profile before the first GATT registration. */
    FN(0x18394, void, uint32_t)(erase_bonds);
    if (state.profile) {
        check(config_init());
        state.timer_pointer = state.timer;
        check(FN(0x16cd8, uint32_t, uint32_t **, uint32_t, void (*)(void *))(
            &state.timer_pointer, 1, tick));
        check(FN(0x16dc8, uint32_t, uint32_t *, uint32_t, void *)(state.timer, 328, 0));
    }
}

EXPORT void radio_gap(void) {
    FN(0x18d10, void, void)();
    if (state.profile) {
        static const uint8_t name[] = "Xbox Wireless Controller";
        uint8_t permission = 0x11;
        check(sd_name(PTR(&permission), PTR(name), sizeof(name) - 1, 0));
        check(sd_appearance(0x03c4, 0, 0, 0));
    }
}

EXPORT uint32_t radio_dis(uint8_t *init) {
    /* ble_dis_pnp_id_t is naturally aligned: source, pad, VID, PID, version. */
    static const uint8_t pnp[8] __attribute__((aligned(4))) = {2, 0, 0x5e, 4, 0x13, 0x0b, 9, 5};
    if (state.profile) put32(init + 56, PTR(pnp));
    return FN(0x1b7b0, uint32_t, uint8_t *)(init);
}

static void hids_event(void *context, const uint8_t *event) {
    (void)context;
    /* 0/1: suspend/exit suspend, 5/6: boot/report protocol. Report writes
     * are consumed by the original HIDS implementation; motor output is ignored. */
    if (event[0] <= 1) state.suspended = event[0] == 0;
    if (event[0] == 5 || event[0] == 6) state.boot_protocol = event[0] == 5;
    state.last_valid = 0;
}

EXPORT uint32_t radio_hids(void *context, uint8_t *init) {
    static const uint8_t input_config[8] __attribute__((aligned(4))) = {16, 0, 1, 1, 0x21, 0x21, 0x21, 0};
    /* Security bytes are CCCD-write, value-read, value-write (in that order). */
    static const uint8_t output_config[8] __attribute__((aligned(4))) = {8, 0, 3, 2, 0, 0x21, 0x21, 0};
    if (state.profile) {
        put32(init, PTR(hids_event));
        init[8] = init[9] = 0; /* No boot keyboard/mouse. */
        init[10] = 1;
        put32(init + 12, PTR(input_config));
        init[16] = 1;
        put32(init + 20, PTR(output_config));
        init[24] = 0;
        put32(init + 28, 0);
        put32(init + 32, PTR(report_map));
        put16(init + 36, sizeof(report_map));
        put16(init + 48, 0x0111);
        init[50] = 0;
        init[51] = 3;
    }
    return FN(0x1b960, uint32_t, void *, uint8_t *)(context, init);
}

EXPORT uint32_t radio_advertising(uint8_t *advert, uint8_t *scan, void *modes,
                                 void *callback, void *error_callback) {
    static const uint8_t hid_uuid[4] __attribute__((aligned(4))) = {0x12, 0x18, 1, 0};
    if (state.profile) {
        memset(advert, 0, 56);
        memset(scan, 0, 56);
        advert[2] = 1; /* Appearance. Name goes in scan response (24 bytes). */
        advert[3] = 5;
        put16(advert + 16, 1); /* Complete 16-bit service list. */
        put32(advert + 20, PTR(hid_uuid));
        scan[0] = 2;
    }
    return FN(0x1b23c, uint32_t, uint8_t *, uint8_t *, void *, void *, void *)(
        advert, scan, modes, callback, error_callback);
}

EXPORT void radio_input(const uint8_t *data, uint32_t length) {
    if (!state.profile) {
        FN(0x19418, void, const uint8_t *, uint32_t)(data, length);
        return;
    }
    ++state.rx;
    state.last_length = (uint16_t)length;
    state.last_header = length >= 2 ? get16(data) : 0;
    if (input_update(&state.input, data, length)) ++state.parsed;
    else ++state.rejected;
}

EXPORT void radio_feature(uint8_t *data, uint32_t length) {
    if (!state.profile) FN(0x18948, void, uint8_t *, uint32_t)(data, length);
}

EXPORT void radio_status(uint8_t *data, uint32_t length) {
    if (!state.profile) FN(0x19518, void, uint8_t *, uint32_t)(data, length);
}

EXPORT void radio_event(const uint8_t *event) {
    if (state.profile) {
        uint16_t id = get16(event), length = get16(event + 2);
        if (id == 0x52) {
            ++state.sysattr_missing;
            state.sysattr_tick = state.ticks;
        }
        /* Observe the actual client WRITE event; do not enable CCCDs ourselves. */
        if (id == 0x50 && length == 34 && get16(event + 6) == get16(HIDS + 24) &&
            (event[8] == 1 || event[8] == 2) && !get16(event + 28) && get16(event + 30) == 2) {
            ++state.cccd_writes;
            state.cccd_written = get16(event + 32);
            state.cccd_write_tick = state.ticks;
            if (state.cccd_written & 1) ++state.cccd_enables;
            else ++state.cccd_disables;
            state.snapshot_pending = 1;
            state.snapshot_tick = state.ticks - 50u; /* First service may save immediately. */
        }
    }
    if (state.profile) config_event(event);
    if (state.profile && (get16(event) == 0x10 || get16(event) == 0x11)) {
        uint32_t suppress = state.input.suppress;
        input_reset(&state.input);
        /* Preserve only an unfinished boot gesture. Once released it stays zero
         * through every later connection, so reconnect does not swallow keys. */
        input_suppress_startup(&state.input, suppress);
        state.last_valid = 0;
        state.suspended = 0;
        state.boot_protocol = 0;
        state.snapshot_pending = 0; /* Never carry a write into a different link. */
    }
    FN(0x1912c, void, const uint8_t *)(event);
}

/* The shipped device manager snapshots system attributes during auth/disconnect,
 * not CCCD WRITE. Windows subscribes after auth; a controller power-off can end
 * the radio before its disconnect snapshot/deferred LPC write. Save the actual
 * stack attributes after an accepted WRITE, while the bonded link is alive.
 * Layout/ABI: original 1cd04 (SYS|USR, 38 bytes), 1ccd4/1cc24 (restore), and
 * 1e448/1e600 (pstorage handle/staging). No CCCD is synthesized or enabled here. */
static void subscription_snapshot(void) {
    if (!state.snapshot_pending || (uint32_t)(state.ticks - state.snapshot_tick) < 50) return;
    state.snapshot_tick = state.ticks;
    if (CONN == 0xffff || get16(DM_LINK + 8) != CONN || DM_LINK[11] != 0 ||
        (DM_LINK[10] & 0xca) != 0x8a || BYTE(0x20002ec6) == 0xff ||
        !BYTE(0x20002194) || !WORD(0x20002f20)) return;
    uint8_t security[2] = {0};
    state.snapshot_error = sd_security(CONN, PTR(security), 0, 0);
    if (state.snapshot_error || (security[0] & 15) != 1 ||
        (security[0] >> 4) < 2 || security[1] < 7) return;
    uint8_t snapshot[48] __attribute__((aligned(4))) = {0};
    uint16_t length = 38;
    ++state.snapshot_attempts;
    state.snapshot_error = sd_sysattr_get(CONN, PTR(snapshot + 8), PTR(&length), 3);
    state.snapshot_length = length;
    if (state.snapshot_error) return;
    if (!length || length > 38) { state.snapshot_error = 0x10; return; }
    put32(snapshot, 3); /* BLE_GATTS_SYS_ATTR_FLAG_SYS_SRVCS | USR_SRVCS */
    put32(snapshot + 4, length);
    uint32_t storage[2];
    state.snapshot_error = FN(0x1e448, uint32_t, const void *, uint32_t, void *)(
        (const void *)0x2000219c, DM_LINK[11], storage);
    if (state.snapshot_error) return;
    uint8_t changed = 0;
    for (unsigned i = 0; i < sizeof(snapshot); ++i)
        changed |= snapshot[i] != BOND_CACHE[0x38 + i];
    if (changed) {
        /* Use the status-returning staging routine directly. Its public wrappers
         * 1e5a4/1e5d0 discard errors. The original timer batches the LPC write. */
        state.snapshot_error = FN(0x1e600, uint32_t, void *, const void *, uint32_t, uint32_t)(
            storage, snapshot, sizeof(snapshot), 0x38);
        if (state.snapshot_error) return;
        ++state.snapshot_queued; /* Staged for LPC, not a flash-commit ACK. */
    }
    memcpy(DM_SYSATTR, snapshot, sizeof(snapshot));
    state.snapshot_pending = 0;
}

static int ready(void) {
    uint16_t cccd = 0;
    struct { uint16_t length, offset; uint16_t *value; } value = {2, 0, &cccd};
    state.blocked = 0;
    memset(state.security, 0, 2);
    state.cccd = 0;
    state.security_error = state.cccd_error = 0;
    if (CONN == 0xffff) { state.blocked = 1; return 0; }
    if (state.suspended) { state.blocked = 2; return 0; }
    if (state.boot_protocol) { state.blocked = 3; return 0; }
    state.security_error = sd_security(CONN, PTR(state.security), 0, 0);
    if (state.security_error) { state.blocked = 4; return 0; }
    if ((state.security[0] & 15) != 1 || (state.security[0] >> 4) < 2 || state.security[1] < 7) {
        state.blocked = 5; return 0;
    }
    /* The SDK restores bonded CCCDs without necessarily generating a write event.
     * Read the actual input[0] CCCD instead of assuming a callback was seen. */
    state.cccd_error = sd_value_get(CONN, get16(HIDS + 24), PTR(&value), 0);
    state.cccd_read_length = value.length;
    if (state.cccd_error) { state.blocked = 6; return 0; }
    state.cccd = cccd;
    if ((cccd ^ state.cccd_previous) & 1) {
        if (cccd & 1) ++state.cccd_rises;
        else ++state.cccd_falls;
    }
    state.cccd_previous = cccd;
    if (!(cccd & 1)) { state.blocked = 7; return 0; }
    return 1;
}

EXPORT void radio_service(void) {
    FN(0x18744, void, void)();
    if (!state.profile || !state.initialized) return;
    ++state.services;
    config_service(state.ticks);
    subscription_snapshot();
    if (state.last_attempt == state.ticks) return;
    state.last_attempt = state.ticks;
    if (!ready()) { state.last_valid = 0; return; }
    if ((state.input.received & SC_CHUNKS_INPUT) != SC_CHUNKS_INPUT) { state.blocked = 8; return; }
    uint8_t report[16];
    input_report_mapped(&state.input, report, config_mapping());
    uint8_t changed = !state.last_valid;
    for (unsigned i = 0; i < sizeof(report); ++i) changed |= report[i] != state.last_report[i];
    if (!changed) return;
    ++state.attempts;
    uint32_t result = FN(0x1ba90, uint32_t, void *, uint32_t, uint32_t, const uint8_t *)(
        HIDS, 0, sizeof(report), report);
    state.last_error = result;
    if (!result) {
        ++state.sent;
        memcpy(state.last_report, report, sizeof(report));
        state.last_valid = 1;
    }
    /* Busy / no TX buffers / CCCD changes retain the newest state for the next tick. */
}

void radio_diagnostics(uint8_t page, uint8_t out[16]) {
    memset(out, 0, 16);
    if (page == 1) {
        put32(out, state.rx); put32(out + 4, state.parsed); put32(out + 8, state.rejected);
        put16(out + 12, state.last_length); put16(out + 14, state.input.received);
    } else if (page == 2) {
        put32(out, state.attempts); put32(out + 4, state.sent); put32(out + 8, state.last_error);
        put16(out + 12, CONN); out[14] = state.security[0]; out[15] = state.security[1];
    } else if (page == 3) {
        put32(out, state.security_error); put32(out + 4, state.cccd_error);
        put16(out + 8, state.cccd); out[10] = state.ticks ? state.blocked : 9;
        out[11] = state.profile | state.initialized << 1 | state.suspended << 2 | state.boot_protocol << 3;
        put32(out + 12, state.services);
    } else if (page == 4) {
        put32(out, state.input.buttons); put32(out + 4, state.input.suppress);
        put16(out + 8, state.last_header);
    } else if (page == 5) input_report_mapped(&state.input, out, config_mapping());
    else if (page == 6) {
        put32(out, state.cccd_writes); put16(out + 4, state.cccd_written);
        put16(out + 6, state.sysattr_missing); put16(out + 8, get16(HIDS + 0x176));
        put16(out + 10, get16(HIDS + 20)); put16(out + 12, get16(HIDS + 24));
        put16(out + 14, state.cccd_read_length);
    } else if (page == 7) {
        put16(out, state.cccd_enables); put16(out + 2, state.cccd_disables);
        put32(out + 4, state.cccd_write_tick); put32(out + 8, state.sysattr_tick);
        put16(out + 12, state.cccd_rises); put16(out + 14, state.cccd_falls);
    } else if (page == 8) {
        put32(out, state.snapshot_attempts); put32(out + 4, state.snapshot_queued);
        put32(out + 8, state.snapshot_error); put16(out + 12, state.snapshot_length);
        out[14] = state.snapshot_pending; out[15] = DM_LINK[10];
    }
}
