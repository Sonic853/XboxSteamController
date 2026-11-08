#ifndef XSC_INPUT_H
#define XSC_INPUT_H

#include "mapping.h"

/* Steam Controller BLE delta chunks, in ascending flag order. */
#define SC_CHUNK_BUTTONS  0x0010u
#define SC_CHUNK_TRIGGERS 0x0020u
#define SC_CHUNK_EXTRA    0x0040u
#define SC_CHUNK_STICK    0x0080u
#define SC_CHUNK_LEFTPAD  0x0100u
#define SC_CHUNK_RIGHTPAD 0x0200u
#define SC_CHUNK_ACCEL    0x0400u
#define SC_CHUNK_GYRO     0x0800u
#define SC_CHUNK_QUAT     0x1000u
#define SC_CHUNKS_ALL     0x1ff0u
#define SC_CHUNKS_INPUT   0x03b0u

/* Raw 24-bit Steam Controller button field. */
#define SC_BUTTON_RT_BOTTOM   0x000001u
#define SC_BUTTON_LT_BOTTOM   0x000002u
#define SC_BUTTON_RB          0x000004u
#define SC_BUTTON_LB          0x000008u
#define SC_BUTTON_Y           0x000010u
#define SC_BUTTON_B           0x000020u
#define SC_BUTTON_X           0x000040u
#define SC_BUTTON_A           0x000080u
#define SC_BUTTON_VIEW        0x001000u
#define SC_BUTTON_STEAM       0x002000u
#define SC_BUTTON_MENU        0x004000u
#define SC_BUTTON_LEFT_BACK   0x008000u
#define SC_BUTTON_RIGHT_BACK  0x010000u
#define SC_BUTTON_LEFT_CLICK  0x020000u
#define SC_BUTTON_RIGHT_CLICK 0x040000u
#define SC_BUTTON_LEFT_TOUCH  0x080000u
#define SC_BUTTON_RIGHT_TOUCH 0x100000u
#define SC_BUTTON_STICK_CLICK 0x400000u
#define SC_BUTTON_STARTUP (SC_BUTTON_RB | SC_BUTTON_STEAM | SC_BUTTON_Y)

#define XBOX_INPUT_REPORT_SIZE 16u

/* Signed raw axes are cached even when the corresponding pad is not touched.
 * received permits the caller to detect an incomplete first delta snapshot.
 * No timers or connection state belong here. Cortex-M0 sizeof is 24 bytes. */
struct xbox_input {
    uint32_t buttons;
    uint32_t suppress;
    int16_t stick_x, stick_y;
    int16_t left_x, left_y;
    int16_t right_x, right_y;
    uint8_t left_trigger, right_trigger;
    uint16_t received;
};

void input_reset(struct xbox_input *state);
/* Only call for a newly selected startup gesture, never automatically on
 * reconnect. Pass RB|STEAM, adding Y only if Y chose this boot mode. */
void input_suppress_startup(struct xbox_input *state, uint32_t buttons);
/* 1 = valid delta accepted (possibly unchanged); 0 = invalid, state untouched.
 * packet begins with the two-byte delta header, not a BLE transport wrapper. */
int input_update(struct xbox_input *state, const uint8_t *packet, size_t len);
/* HOGP characteristic value: exactly 16 bytes, WITHOUT Report ID 1.
 * input_report uses the default mapping. Invalid/NULL mappings also fall back
 * to defaults. Reporting never changes state or mapping; buffers cannot alias. */
void input_report(const struct xbox_input *state, uint8_t report[16]);
void input_report_mapped(const struct xbox_input *state, uint8_t report[16],
                         const uint8_t mapping[24]);

#endif
