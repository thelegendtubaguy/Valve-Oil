// fm17550_transceive oid=%c data=%*s  ->  fm17550_transceive_return oid=%c status=%c data=%*s
//
// data: reader command (0x0c Transceive or 0x0e MFAuthent), timeout units for
// QIDI's timer setup, then the bytes for the FIFO. Frames go out with the
// CRC setting left by fm17550_select (on), so the reader appends and checks CRC.
// status: 1 when the reader reported no error, else 0.
// data: error flags, Status2Reg (bit 3 = Crypto1 on), received bit count
// (little-endian u16), then the received bytes (at most 44).
#include "qidi_box_02_03_01_21.h"

void fm17550_transceive_cb(uint32_t *args)
{
    uint32_t oid = args[0], len = args[1];
    const uint8_t *data = (const uint8_t *)args[2];
    uint8_t *obj = lookup_reader(oid);
    void *spi = OBJ_SPI(obj);
    uint8_t out[48];
    uint8_t rx[96];
    int32_t bits = 0;
    uint32_t status = 0, count = 4, i, error = 0xff;

    for (i = 0; i < 4; i++)
        out[i] = 0;
    if (len >= 3 && len <= 34 && (data[0] == 0x0c || data[0] == 0x0e)) {
        fm_set_timeout(obj, data[1]);
        error = fm_execute(obj, data[0], data + 2, len - 2, rx, &bits);
        if (bits < 0)
            bits = 0;
        count = ((uint32_t)bits + 7) / 8;
        if (count > sizeof(out) - 4)
            count = sizeof(out) - 4;
        for (i = 0; i < count; i++)
            out[4 + i] = rx[i];
        count += 4;
        status = error == 0;
        out[1] = reg_read(spi, 0x08);
        out[2] = (uint8_t)bits;
        out[3] = (uint8_t)(bits >> 8);
    }
    out[0] = (uint8_t)error;
    command_sendf(ENCODER_TRANSCEIVE_RETURN, oid, status, count, out);
}
