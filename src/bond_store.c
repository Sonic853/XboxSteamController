#include "bond_store.h"
#include "mapping.h"

#define SECTOR_SIZE 4096u
#define RECORD_COMMIT 0x4b4f4258u /* "XBOK" */
#define BODY_OFFSET 16u
#define MAPPING_MAGIC_OFFSET 144u
#define MAPPING_OFFSET 148u
#define COMMIT_OFFSET 248u
#define CRC_OFFSET 252u
#define CLOCK_HZ_ADDRESS 0x100007d8u
#define IAP_ENTRY 0x1fff1ff1u

/* No persistent RAM allocation: scans are bounded to 32 records. The aligned
 * program page is an automatic object, live only in the deferred write task. */
static uint32_t crc32(const uint8_t *data, uint32_t size) {
    uint32_t crc = 0xffffffffu;
    while (size--) {
        crc ^= *data++;
        for (uint32_t bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static int valid(const uint8_t *p) {
    return get32(p) == BOND_MAGIC && get16(p + 4) == BOND_VERSION &&
           get16(p + 6) == BOND_SIZE && get32(p + 12) == ~get32(p + 8) &&
           get32(p + COMMIT_OFFSET) == RECORD_COMMIT &&
           get32(p + CRC_OFFSET) == crc32(p, CRC_OFFSET);
}

static const uint8_t *latest(void) {
    const uint8_t *best = 0;
    for (uintptr_t address = BOND_FLASH_BEGIN; address < BOND_FLASH_END;
         address += BOND_RECORD_SIZE) {
        const uint8_t *p = (const uint8_t *)address;
        if (valid(p) && (!best || (int32_t)(get32(p + 8) - get32(best + 8)) > 0))
            best = p;
    }
    return best;
}

static int erased(uintptr_t address) {
    const volatile uint32_t *p = (const volatile uint32_t *)address;
    for (uint32_t n = 0; n < BOND_RECORD_SIZE / 4; ++n)
        if (p[n] != 0xffffffffu) return 0;
    return 1;
}

static int in_range(uint32_t offset, uint32_t length) {
    return offset <= BOND_SIZE && length <= BOND_SIZE - offset;
}

static uint8_t body_byte(const uint8_t *record, uint32_t offset, int reset) {
    if (!record) return 0xff;
    /* nRF 0x18fa0: byte 0x6c==1 marks its seven-byte local address record.
     * Never copy an address or binding from the official Steam EEPROM slots. */
    if (reset && !(offset >= 0x6cu && offset < 0x73u &&
                   record[BODY_OFFSET + 0x6c] == 1)) return 0xff;
    return record[BODY_OFFSET + offset];
}

EXPORT int bond_store_present(void) {
    const uint8_t *p = latest();
    if (p) for (uint32_t i = 0; i < BOND_SIZE; ++i)
        if (p[BODY_OFFSET + i] != 0xff) return 1;
    return 0; /* A mapping-only record must not suppress first-bond pairing. */
}

EXPORT uint32_t bond_store_generation(void) {
    const uint8_t *p = latest();
    return p ? get32(p + 8) : 0;
}

EXPORT int bond_store_read(uint8_t *dst, uint32_t offset, uint32_t length, int reset) {
    if (!in_range(offset, length)) return 0;
    const uint8_t *record = latest();
    for (uint32_t i = 0; i < length; ++i) dst[i] = body_byte(record, offset + i, reset);
    return 1;
}

static void read_mapping(const uint8_t *record, uint8_t *out) {
    if (record && get32(record + MAPPING_MAGIC_OFFSET) == MAPPING_STORE_MAGIC &&
        mapping_valid(record + MAPPING_OFFSET))
        memcpy(out, record + MAPPING_OFFSET, MAPPING_SIZE);
    else mapping_default(out);
}

EXPORT void mapping_store_read(uint8_t out[MAPPING_SIZE]) {
    read_mapping(latest(), out);
}

static inline uint32_t irq_save(void) {
    uint32_t prior;
    __asm volatile("mrs %0, primask\n\tcpsid i" : "=r"(prior) :: "memory");
    return prior;
}
static inline void irq_restore(uint32_t prior) {
    __asm volatile("msr primask, %0" :: "r"(prior) : "memory");
}

/* The original image already reserves [0x10001fe0,0x10002000) for ROM work.
 * Leave it untouched. Refuse to call ROM if the active stack lacks a
 * conservative 256-byte budget above 0x10001c00. ROM uses the caller's MSP. */
__attribute__((noinline)) static uint32_t iap(uint32_t command, uint32_t a,
                                             uint32_t b, uint32_t c, uint32_t d) {
    uint32_t args[5] = {command, a, b, c, d};
    uint32_t result[4] = {BOND_ERROR_VERIFY, 0, 0, 0};
    uintptr_t sp;
    __asm volatile("mov %0, sp" : "=r"(sp));
    if (sp < 0x10001d00u) return BOND_ERROR_STACK;
    FN(IAP_ENTRY, void, uint32_t *, uint32_t *)(args, result);
    return result[0];
}

__attribute__((noinline)) static uint32_t program(uintptr_t address,
                                                 const uint8_t *page, int erase_sector) {
    if (address < BOND_FLASH_BEGIN || address >= BOND_FLASH_END ||
        (address & (BOND_RECORD_SIZE - 1u))) return BOND_ERROR_RANGE;
    uint32_t hz = WORD(CLOCK_HZ_ADDRESS);
    if (hz < 1000000u || hz > 50000000u) return BOND_ERROR_CLOCK;
    uint32_t khz = FN(0x20ec, uint32_t, uint32_t, uint32_t)(hz, 1000);
    uint32_t sector = (uint32_t)address >> 12;
    uint32_t prior = irq_save();
    uint32_t status = iap(50, sector, sector, 0, 0);
    if (!status && erase_sector) {
        status = iap(52, sector, sector, khz, 0);
        if (!status) status = iap(50, sector, sector, 0, 0);
    }
    if (!status) status = iap(51, (uint32_t)address, (uint32_t)(uintptr_t)page,
                              BOND_RECORD_SIZE, khz);
    irq_restore(prior);
    if (!status) {
        const volatile uint8_t *stored = (const volatile uint8_t *)address;
        for (uint32_t i = 0; i < BOND_RECORD_SIZE; ++i)
            if (stored[i] != page[i]) return BOND_ERROR_VERIFY;
        if (!valid((const uint8_t *)address)) status = BOND_ERROR_VERIFY;
    }
    return status;
}

__attribute__((noinline)) static uint32_t write_record(uint32_t offset, const uint8_t *src,
                                                      uint32_t length, uint32_t reset_or_token) {
    const uint8_t *previous = latest();
    uint8_t page[BOND_RECORD_SIZE] __attribute__((aligned(4)));
    if (previous) memcpy(page, previous, sizeof(page));
    else memset(page, 0xff, sizeof(page));
    int changed = !previous;
    if (offset == MAPPING_RPC_MAGIC) {
        /* Reuse the page itself as the current-config scratch. No extra cache. */
        read_mapping(previous, page + MAPPING_OFFSET);
        if (mapping_token(page + MAPPING_OFFSET) != reset_or_token) return MAPPING_CONFLICT;
        changed = 0;
        for (uint32_t i = 0; i < MAPPING_SIZE; ++i)
            if (page[MAPPING_OFFSET + i] != src[i]) changed = 1;
        if (!changed) return 0; /* FF legacy default need not consume a page */
        put32(page + MAPPING_MAGIC_OFFSET, MAPPING_STORE_MAGIC);
        memcpy(page + MAPPING_OFFSET, src, MAPPING_SIZE);
    } else {
        for (uint32_t i = 0; i < BOND_SIZE; ++i) {
            uint8_t value = body_byte(previous, i, reset_or_token != 0);
            if (i >= offset && i - offset < length) value = src[i - offset];
            page[BODY_OFFSET + i] = value;
            if (previous && value != previous[BODY_OFFSET + i]) changed = 1;
        }
        if (!changed) return 0;
    }
    uint32_t generation = previous ? get32(previous + 8) + 1u : 1u;
    put32(page, BOND_MAGIC);
    put16(page + 4, BOND_VERSION);
    put16(page + 6, BOND_SIZE);
    put32(page + 8, generation);
    put32(page + 12, ~generation);
    put32(page + COMMIT_OFFSET, RECORD_COMMIT);
    put32(page + CRC_OFFSET, crc32(page, CRC_OFFSET));

    uintptr_t destination = BOND_FLASH_BEGIN;
    int erase_sector = 1;
    if (previous) {
        uintptr_t sector = (uintptr_t)previous & ~(uintptr_t)(SECTOR_SIZE - 1u);
        destination = (uintptr_t)previous + BOND_RECORD_SIZE;
        while (destination < sector + SECTOR_SIZE && !erased(destination))
            destination += BOND_RECORD_SIZE;
        if (destination < sector + SECTOR_SIZE) erase_sector = 0;
        else destination = sector == BOND_FLASH_BEGIN ? sector + SECTOR_SIZE : BOND_FLASH_BEGIN;
    }
    /* Never erase the sector containing the most recent valid record. */
    uint32_t status = program(destination, page, erase_sector);
    if (offset == MAPPING_RPC_MAGIC && status && status < 0x10000u) status |= 0x20000u;
    return status;
}

EXPORT uint32_t bond_store_write(uint32_t offset, const uint8_t *src,
                                 uint32_t length, int reset) {
    /* Check BEFORE allocating the 256-byte page. GCC9 -Os write_record +
     * program + iap consumes 392 bytes; reserve 416 plus 256 for ROM below it.
     * The second check in iap also enforces the ROM boundary after allocation. */
    uintptr_t sp;
    __asm volatile("mov %0, sp" : "=r"(sp));
    if (sp < 0x10001ea0u) return BOND_ERROR_STACK;
    if (!in_range(offset, length) || length == 0) return BOND_ERROR_RANGE;
    return write_record(offset, src, length, reset);
}

EXPORT uint32_t mapping_store_write(uint32_t expected_token, const uint8_t config[MAPPING_SIZE]) {
    uintptr_t sp;
    __asm volatile("mov %0, sp" : "=r"(sp));
    if (sp < 0x10001ea0u) return BOND_ERROR_STACK;
    if (!mapping_valid(config)) return MAPPING_INVALID;
    return write_record(MAPPING_RPC_MAGIC, config, MAPPING_SIZE, expected_token);
}
