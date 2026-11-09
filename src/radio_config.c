#include "radio_config.h"
#include "mapping.h"
#include "s110.h"

/* Fixed ATT-MTU23 protocol; no notification/CCCD or browser access to HID needed.
 * UUID base 85300000-2015-4c53-8f44-58426c65434d (Nordic little endian). */
static const uint8_t uuid_base[16] = {
    0x4d,0x43,0x65,0x6c,0x42,0x58,0x44,0x8f,0x53,0x4c,0x15,0x20,0,0,0x30,0x85
};
struct config_state {
    uint8_t mapping[24], staged[24], request[20], response[20];
    uint32_t token, expected, sent_tick, host_status;
    uint16_t service, request_handle, response_handle;
    uint8_t uuid_type, loaded, stage, pending, queued, dirty, active;
};
static struct config_state cfg;

static uint32_t characteristic(uint16_t id, int writable, uint16_t *handle) {
    uint8_t uuid[4] = {0,0,0,0};
    uint8_t metadata[4] = {0,0,2,0}; /* stack value location, fixed length */
    uint32_t properties[7] = {0}; /* ble_gatts_char_md_t, 28B */
    uint32_t attribute[5] = {0}; /* ble_gatts_attr_t, 20B */
    uint16_t handles[4] = {0};
    uint8_t initial[20] = {0};
    put16(uuid, id);
    uuid[2] = cfg.uuid_type;
    metadata[writable ? 1 : 0] = 0x21; /* encrypted Mode1 Level2 */
    properties[0] = writable ? 8 : 2; /* write request / read */
    attribute[0] = PTR(uuid);
    attribute[1] = PTR(metadata);
    attribute[2] = 20; /* init_len=20, init_offs=0 */
    attribute[3] = 20; /* max_len=20 */
    attribute[4] = PTR(initial);
    uint32_t result = sd_characteristic_add(cfg.service, PTR(properties), PTR(attribute), PTR(handles));
    if (!result) *handle = handles[0];
    return result;
}

uint32_t config_init(void) {
    uint8_t uuid[4] = {1,0,0,0};
    mapping_default(cfg.mapping);
    cfg.token = mapping_token(cfg.mapping);
    uint32_t result = sd_uuid_add(PTR(uuid_base), PTR(&cfg.uuid_type), 0, 0);
    if (result) return result;
    uuid[2] = cfg.uuid_type;
    result = sd_service_add(1, PTR(uuid), PTR(&cfg.service), 0);
    if (!result) result = characteristic(2, 1, &cfg.request_handle);
    if (!result) result = characteristic(3, 0, &cfg.response_handle);
    if (!result) cfg.active = 1;
    return result;
}

const uint8_t *config_mapping(void) { return cfg.mapping; }

static void rpc(uint8_t write, uint32_t ticks) {
    uint8_t packet[37];
    packet[0] = write ? 0x5e : 0x5d;
    put32(packet + 1, CONFIG_RPC_MAGIC);
    put32(packet + 5, write ? 28 : 24);
    if (write) {
        put32(packet + 9, cfg.expected);
        memcpy(packet + 13, cfg.staged, 24);
    }
    cfg.pending = write ? 2 : 1;
    cfg.sent_tick = ticks;
    /* Direct UART framing does not replace the original Steam config callback. */
    FN(0x16a46, void, const uint8_t *, uint32_t)(packet, write ? 37 : 9);
}

static void commit_reply(uint8_t status) {
    cfg.response[2] = status;
    put32(cfg.response + 4, cfg.token);
    put32(cfg.response + 8, cfg.host_status);
    cfg.dirty = 1;
}

int config_receive(const uint8_t *packet, uint32_t length) {
    if (length < 5 || packet[0] != 0x5d || get32(packet + 1) != CONFIG_RPC_MAGIC) return 0;
    if (!cfg.active || !cfg.pending || length != 37) return 1;
    if (!mapping_valid(packet + 9) || mapping_token(packet + 9) != get32(packet + 33)) return 1;
    uint32_t status = get32(packet + 5);
    if (cfg.pending == 2 && !status) {
        /* A stale read response is not a write acknowledgement. */
        for (unsigned i = 0; i < 24; ++i) if (packet[9 + i] != cfg.staged[i]) return 1;
    }
    cfg.host_status = status;
    memcpy(cfg.mapping, packet + 9, 24);
    cfg.token = get32(packet + 33);
    cfg.loaded = 1;
    if (cfg.pending == 2) {
        commit_reply(status <= 3 ? (uint8_t)status : 7);
        cfg.stage = 0;
    }
    cfg.pending = 0;
    return 1;
}

void config_event(const uint8_t *event) {
    if (!cfg.active) return;
    uint16_t id = get16(event), length = get16(event + 2);
    if (id == 0x10 || id == 0x11) {
        cfg.queued = 0;
        /* An in-flight persistent operation is reconciled even after disconnect. */
        if (cfg.pending != 2) cfg.stage = 0;
        return;
    }
    /* S110 v8 serializer 0x39b4: handle+6, op+8, offset+28, len+30, data+32. */
    if (id != 0x50 || length < 32 || get16(event + 6) != cfg.request_handle) return;
    if (length != 52 || event[8] != 1 || get16(event + 28) != 0 || get16(event + 30) != 20) return;
    /* Single client transaction; do not overwrite an outstanding commit identity. */
    if (cfg.pending == 2 || cfg.queued) return;
    memcpy(cfg.request, event + 32, 20);
    cfg.queued = 1;
}

static void request(uint32_t ticks) {
    const uint8_t *r = cfg.request;
    uint8_t op = r[0], arg = r[2];
    memset(cfg.response, 0, 20);
    cfg.response[0] = op;
    cfg.response[1] = r[1];
    cfg.response[3] = arg;
    cfg.dirty = 1;
    if (r[3]) { cfg.response[2] = 1; return; }
    if (op == 0x10 && arg <= 8) {
        if (!arg) {
            put32(cfg.response + 4, 0x32435358u);
            put16(cfg.response + 8, 4);
            cfg.response[10] = 1;
            cfg.response[11] = cfg.loaded | (cfg.pending ? 2 : 0) | (cfg.stage ? 4 : 0);
            put32(cfg.response + 12, ticks);
            put32(cfg.response + 16, cfg.host_status);
        } else radio_diagnostics(arg, cfg.response + 4);
    } else if (op == 1 && arg <= 1) {
        if (!cfg.loaded) { cfg.response[2] = 6; return; }
        if (!arg) memcpy(cfg.response + 4, cfg.mapping, 16);
        else {
            memcpy(cfg.response + 4, cfg.mapping + 16, 8);
            put32(cfg.response + 12, cfg.token);
            put32(cfg.response + 16, cfg.host_status);
        }
    } else if (op == 2 && arg <= 1) {
        if (!cfg.loaded) { cfg.response[2] = 6; return; }
        if (!arg) {
            cfg.expected = get32(r + 4);
            memcpy(cfg.staged, r + 8, 12);
            cfg.stage = 1;
        } else if (cfg.stage == 1 && !get32(r + 16)) {
            memcpy(cfg.staged + 12, r + 4, 12);
            cfg.stage = mapping_valid(cfg.staged) ? 2 : 0;
            if (!cfg.stage) cfg.response[2] = 1;
        } else { cfg.stage = 0; cfg.response[2] = 1; }
    } else if (op == 3 && !arg) {
        if (!cfg.loaded) { cfg.response[2] = 6; return; }
        if (cfg.stage != 2) { cfg.response[2] = 1; return; }
        if (cfg.pending) { cfg.response[2] = 3; return; }
        if (cfg.expected != cfg.token || cfg.expected != get32(r + 8) ||
            mapping_token(cfg.staged) != get32(r + 4)) {
            cfg.stage = 0; commit_reply(2); return;
        }
        if (get32(r + 12) || get32(r + 16)) { cfg.response[2] = 1; return; }
        commit_reply(4);
        rpc(1, ticks);
    } else cfg.response[2] = 1;
}

void config_service(uint32_t ticks) {
    if (!cfg.active) return;
    if (cfg.pending && (uint32_t)(ticks - cfg.sent_tick) >= 500) {
        if (cfg.pending == 2) { cfg.stage = 0; commit_reply(5); }
        cfg.pending = 0;
        cfg.loaded = 0; /* timeout means unknown, never a claimed persisted success */
        cfg.sent_tick = ticks;
    }
    if (cfg.queued) { cfg.queued = 0; request(ticks); }
    if (!cfg.loaded && !cfg.pending && (!cfg.sent_tick || (uint32_t)(ticks - cfg.sent_tick) >= 50)) rpc(0, ticks);
    if (cfg.dirty) {
        struct { uint16_t length, offset; const uint8_t *data; } value = {20, 0, cfg.response};
        if (!sd_value_set(0xffff, cfg.response_handle, PTR(&value), 0)) cfg.dirty = 0;
    }
}
