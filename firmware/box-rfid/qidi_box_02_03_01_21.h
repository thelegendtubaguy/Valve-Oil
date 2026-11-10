// Addresses inside QIDI Box application 02.03.01.21 (load address 0x08008000),
// recovered from the stock image. The handlers call QIDI's own FM17550 routines.
#ifndef QIDI_BOX_02_03_01_21_H
#define QIDI_BOX_02_03_01_21_H

#include <stdint.h>

#define FN(addr) ((addr) | 1u)

// Klipper core.
typedef void (*sendf_fn)(const void *encoder, ...);
#define command_sendf ((sendf_fn)FN(0x080083a4))
typedef void (*noreturn_fn)(void) __attribute__((noreturn));
#define shutdown_invalid_oid ((noreturn_fn)FN(0x08009708))
#define OID_COUNT (*(volatile uint8_t *)0x20000244)
#define OID_TABLE (*(uint32_t *volatile *)0x20000248)
#define FM17550_TYPE 0x08009755u  // config_fm17550 handler, Klipper's oid type tag

// spidev_transfer(spi, receive_data, len, data)
typedef void (*spi_fn)(void *spi, uint32_t receive, uint32_t len, uint8_t *data);
#define spi_transfer ((spi_fn)FN(0x0800cc48))

// FM17550 helpers. obj is the reader object; obj+0x10 holds its SPI device.
typedef void (*obj_fn)(uint8_t *obj);
typedef void (*obj_u_fn)(uint8_t *obj, uint32_t value);
typedef void (*setbits_fn)(uint8_t *obj, uint32_t reg, uint32_t bits);
typedef uint32_t (*xfer_fn)(uint8_t *obj, uint32_t command, const uint8_t *tx, uint32_t tx_len,
                            uint8_t *rx, int32_t *rx_bits);
typedef uint32_t (*anticoll_fn)(uint8_t *obj, uint32_t sel, uint8_t *uid5);
typedef uint32_t (*select_fn)(uint8_t *obj, uint32_t sel, uint8_t *uid5, uint8_t *sak);
#define fm_set_bits ((setbits_fn)FN(0x0800fbfc))      // reg |= bits
#define fm_init ((obj_fn)FN(0x0800fc4c))              // QIDI's per-read register setup
#define fm_set_timeout ((obj_u_fn)FN(0x0800f9d4))     // timer reload for n units
#define fm_execute ((xfer_fn)FN(0x0800a970))          // run Transceive/MFAuthent, read FIFO
#define fm_anticollision ((anticoll_fn)FN(0x0800ac8c)) // returns nonzero on failure
#define fm_select ((select_fn)FN(0x0800adf0))         // returns nonzero on failure

// Response encoders retargeted by the patch to "oid=%c status=%c data=%*s".
#define ENCODER_SELECT_RETURN ((const void *)0x080102c8)     // was fm17550_write_card_return
#define ENCODER_TRANSCEIVE_RETURN ((const void *)0x080102d8) // was fm17550_read_fifo_return

#define OBJ_SPI(obj) (*(void **)((obj) + 0x10))

static inline __attribute__((always_inline)) uint8_t *lookup_reader(uint32_t oid)
{
    uint32_t *table = OID_TABLE;
    if (oid >= OID_COUNT || table[oid * 2] != FM17550_TYPE)
        shutdown_invalid_oid();
    return (uint8_t *)table[oid * 2 + 1];
}

static __attribute__((noinline)) uint8_t reg_read(void *spi, uint32_t reg)
{
    // QIDI's access pattern: address byte, then a dummy byte that returns the value.
    uint8_t b = (uint8_t)((reg << 1) | 0x80);
    spi_transfer(spi, 1, 1, &b);
    b = 0;
    spi_transfer(spi, 1, 1, &b);
    return b;
}

static __attribute__((noinline)) void reg_write(void *spi, uint32_t reg, uint32_t value)
{
    uint8_t b[2];
    b[0] = (uint8_t)(reg << 1);
    b[1] = (uint8_t)value;
    spi_transfer(spi, 0, 2, b);
}

#endif
