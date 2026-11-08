#ifndef XSC_RADIO_CONFIG_H
#define XSC_RADIO_CONFIG_H
#include "abi.h"
#define CONFIG_RPC_MAGIC 0x58434d31u
uint32_t config_init(void);
int config_receive(const uint8_t *packet, uint32_t length);
void config_event(const uint8_t *event);
void config_service(uint32_t ticks);
const uint8_t *config_mapping(void);
void radio_diagnostics(uint8_t page, uint8_t out[16]);
#endif
