from __future__ import annotations

from .errors import InstallerError


OPERATION = "klipper_box_heater_settings"

BOX_TRACKING_STOCK = ("        access_tracking = {}",)
# Generated Box heaters otherwise record their options in a throwaway map, so configfile.settings omits
# heater_generic heater_boxN and Fluidd rejects every nonzero target. Fluidd limits targets to target_max_temp,
# which QIDI's box.cfg sets to 90 C, above the Box's advertised 65 C; box.cfg is regenerated per Box count,
# so the limit is capped here. QIDI's SET_HEATER_TEMPERATURE enforces target_max_temp only for the chamber.
BOX_TRACKING_PATCHED = (
    "        if module == 'heater_generic':  # vo: cap Box heater targets at 65 C",
    "            fileconfig.set(section_name, 'target_max_temp', str(min(fileconfig.getfloat(section_name, 'target_max_temp'), 65.)))",
    "        access_tracking = config.access_tracking if module == 'heater_generic' else {}  # vo: expose Box heater settings",
)


class KlipperBoxConfigError(InstallerError):
    pass


def patch_box_config(text: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    stock = newline.join(BOX_TRACKING_STOCK) + newline
    patched = newline.join(BOX_TRACKING_PATCHED) + newline
    current = text.count(patched)
    if current == 1 and text.count(stock) == 0:
        return text
    if current == 0 and text.count(stock) == 1 and "def build_config(self, name, num, module, config):" in text:
        return text.replace(stock, patched, 1)
    raise KlipperBoxConfigError("Klipper box_config.py does not match the reviewed QIDI source; preserving it.")
