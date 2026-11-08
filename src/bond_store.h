#ifndef XSC_BOND_STORE_H
#define XSC_BOND_STORE_H
#include "abi.h"
#include "mapping.h"

#define BOND_SIZE 128u
#define BOND_FLASH_BEGIN 0x0001e000u
#define BOND_FLASH_END   0x00020000u
#define BOND_RECORD_SIZE 256u
#define BOND_MAGIC 0x314a4258u /* "XBJ1" */
#define BOND_VERSION 1u
#define BOND_ERROR_RANGE 0x10001u
#define BOND_ERROR_CLOCK 0x10002u
#define BOND_ERROR_STACK 0x10003u
#define BOND_ERROR_VERIFY 0x10004u
#define MAPPING_STORE_MAGIC 0x314d4358u /* "XCM1" */
#define MAPPING_RPC_MAGIC 0x58434d31u
#define MAPPING_INVALID 1u
#define MAPPING_CONFLICT 2u
#define MAPPING_BUSY 3u

/* Empty EEPROM's erased-FF representation is accepted by the nRF loader.
 * reset_binding clears keys while preserving this profile's local BLE address. */
int bond_store_present(void);
int bond_store_read(uint8_t *dst, uint32_t offset, uint32_t length, int reset_binding);
uint32_t bond_store_write(uint32_t offset, const uint8_t *src,
                          uint32_t length, int reset_binding);
uint32_t bond_store_generation(void);
void mapping_store_read(uint8_t out[MAPPING_SIZE]);
uint32_t mapping_store_write(uint32_t expected_token, const uint8_t config[MAPPING_SIZE]);
#endif
