# THR firmware changelog

## max4-thr-valve-oil-v1

- The THR reports its version as `02.02.01.08-valve-oil.1`, which is QIDI's version, the project name, and a revision that goes up with each new THR firmware. The data dictionary is recompressed with the new version into its own space, with matching `identify` length constants; it is otherwise identical. The FIFO fix is unchanged.
- The installer updates a THR with `max4-thr-lis2dw-fifo-v1` from QIDI's stock THR image in the THR firmware backups or `~/update`, and falls back to that stock image on a failed transfer.

## max4-thr-lis2dw-fifo-v1

- Corrected the inverted LIS2DW FIFO-empty decision in the Max 4 THR `02.02.01.08` image from QIDI firmware `01.01.07.01`, retaining scheduler wake behavior and changing only five image bytes.
- Added hash-locked generation of a separately named full firmware ZIP and standalone THR image, with checksums and provenance. Other component payloads, the firmware manifest, and vendor version identities are unchanged.
- Added restricted THR-only snapshot, non-erasing probe, confirmed transfer, and readback verification commands with known-image admission, service-ownership checks, preserved stock recovery artifacts, and bounded offline transfer checks. An ambiguous first-data-packet acknowledgement stops the transfer instead of repeating a non-idempotent write. Physical recovery entry must be verified before erasing firmware.
