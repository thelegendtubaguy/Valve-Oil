from __future__ import annotations

from . import klipper_cfg
from .manifest import patch_variant_maximum, select_patch_variant
from .models import PatchLedgerEntry, PatchResult, PatchSpec, SectionPatchSpec


INSTALL_APPLIED = "applied"
INSTALL_NOOP_DESIRED = "noop_desired"
UNINSTALL_REVERTED = "reverted"
UNINSTALL_NOOP_EXPECTED = "noop_expected"
USER_MODIFIED = "user_modified"
SECTION_DELETED = "__TLTG_SECTION_DELETED__"


def classify_install_patch(current: str, patch: PatchSpec, firmware_version: str, prior_state=None) -> PatchResult:
    variant = select_patch_variant(patch, firmware_version)
    ledger_expected = None
    if current == variant.desired:
        classification = INSTALL_NOOP_DESIRED
    elif current == variant.expected:
        classification = INSTALL_APPLIED
    elif _prior_managed_value(current, prior_state, patch):
        classification = INSTALL_APPLIED
    elif _above_maximum(current, patch_variant_maximum(patch, variant)):
        classification = INSTALL_APPLIED
        ledger_expected = current
    else:
        classification = USER_MODIFIED
    return PatchResult(
        id=patch.id,
        file=patch.file,
        section=patch.section,
        option=patch.option,
        current=current,
        expected=variant.expected,
        desired=variant.desired,
        classification=classification,
        ledger_expected=ledger_expected,
    )


def _above_maximum(current: str, maximum: float | None) -> bool:
    if maximum is None:
        return False
    try:
        return float(current) > maximum
    except ValueError:
        return False



def _prior_managed_value(current: str, prior_state, patch: PatchSpec) -> bool:
    # Values earlier releases set. The Plus 5 had 565 mm/s probing travel before its 400 mm/s limit, and the error
    # macro was OPTIMIZED_CANCEL_PRINT_ON_ERROR before 26.11.18.2.
    prior_values = {
        "stepper_x_homing_speed": ("100",),
        "stepper_y_homing_speed": ("100",),
        "stepper_x_homing_retract_speed": ("1000.0",),
        "stepper_y_homing_retract_speed": ("1000.0",),
        "z_tilt_speed": ("600", "565"),
        "bed_mesh_speed": ("600", "565"),
        "virtual_sdcard_on_error_gcode": ("OPTIMIZED_CANCEL_PRINT_ON_ERROR",),
    }.get(patch.id, ())
    if current not in prior_values or prior_state is None:
        return False
    return any(
        entry.id == patch.id
        and entry.target_tuple == patch.target_tuple
        and entry.expected == select_patch_variant(patch, prior_state.runtime_firmware).expected
        and entry.desired == current
        and entry.install_result in {INSTALL_APPLIED, INSTALL_NOOP_DESIRED}
        for entry in prior_state.patch_ledger
    )


def classify_install_section_delete(
    current: str | None, patch: SectionPatchSpec, firmware_version: str
) -> PatchResult:
    variant = select_patch_variant(patch, firmware_version)
    if current is None:
        classification = INSTALL_NOOP_DESIRED
        expected = variant.expected_normalized_sha256
        current_value = SECTION_DELETED
    else:
        current_hash = klipper_cfg.normalized_section_sha256(current)
        if current_hash == variant.expected_normalized_sha256:
            classification = INSTALL_APPLIED
        else:
            classification = USER_MODIFIED
        expected = current if classification == INSTALL_APPLIED else variant.expected_normalized_sha256
        current_value = current
    return PatchResult(
        id=patch.id,
        file=patch.file,
        section=patch.section,
        option=patch.option,
        current=current_value,
        expected=expected,
        desired=SECTION_DELETED,
        classification=classification,
    )



def classify_uninstall_patch(current: str, entry: PatchLedgerEntry) -> PatchResult:
    if current == entry.expected:
        classification = UNINSTALL_NOOP_EXPECTED
    elif current == entry.desired:
        classification = UNINSTALL_REVERTED
    else:
        classification = USER_MODIFIED
    return PatchResult(
        id=entry.id,
        file=entry.file,
        section=entry.section,
        option=entry.option,
        current=current,
        expected=entry.expected,
        desired=entry.desired,
        classification=classification,
    )

