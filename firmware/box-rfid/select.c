// fm17550_select oid=%c  ->  fm17550_select_return oid=%c status=%c data=%*s
//
// Wakes the tag with WUPA (so a tag left halted by an earlier read answers),
// runs anticollision and select through every cascade level, and leaves the
// tag selected with CRC on for fm17550_transceive.
// status: 0 no tag or select failed, 1 selected, 2 reader not responding.
// data (20 bytes): ATQA[2], cascade level 1..3 UID bytes and BCC [5 each], SAK[3].
#include "qidi_box_02_03_01_21.h"

void fm17550_select_cb(uint32_t *args)
{
    uint32_t oid = args[0];
    uint8_t *obj = lookup_reader(oid);
    void *spi = OBJ_SPI(obj);
    uint8_t out[20];
    uint8_t rx[64];
    int32_t bits = 0;
    uint32_t status = 0, i;

    for (i = 0; i < sizeof(out); i++)
        out[i] = 0;
    if ((reg_read(spi, 0x14) & 3) != 3)
        fm_set_bits(obj, 0x14, 3);  // both antenna drivers on, as QIDI's read does
    fm_init(obj);
    if (reg_read(spi, 0x14) != 0x83 && reg_read(spi, 0x15) != 0x40) {
        status = 2;  // same reader check as QIDI's read
        goto done;
    }
    // Crypto1 off, CRC off, 7-bit short frame.
    reg_write(spi, 0x08, reg_read(spi, 0x08) & 0xf7);
    reg_write(spi, 0x12, reg_read(spi, 0x12) & 0x7f);
    reg_write(spi, 0x13, reg_read(spi, 0x13) & 0x7f);
    reg_write(spi, 0x0d, 0x07);
    fm_set_timeout(obj, 1);
    rx[0] = 0x52;  // WUPA
    if (fm_execute(obj, 0x0c, rx, 1, rx, &bits) != 0 || bits != 16)
        goto done;
    out[0] = rx[0];
    out[1] = rx[1];
    for (i = 0; i < 3; i++) {
        uint8_t *uid = &out[2 + 5 * i];
        uint8_t *sak = &out[17 + i];
        if (fm_anticollision(obj, 0x93 + 2 * i, uid) != 0)
            goto done;
        if (fm_select(obj, 0x93 + 2 * i, uid, sak) != 0)
            goto done;
        if (!(*sak & 0x04)) {
            status = 1;
            break;
        }
    }
done:
    command_sendf(ENCODER_SELECT_RETURN, oid, status, (uint32_t)sizeof(out), out);
}
