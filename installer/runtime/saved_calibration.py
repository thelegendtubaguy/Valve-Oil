"""The operator's saved calibration across the QIDI firmware package a firmware update installs.

QIDI's SOC package deletes the config folder except six file names and installs its stock printer.cfg, so Klipper's
SAVE_CONFIG block (bed mesh profiles, input shaper, PID, probe and endstop offsets, whatever SAVE_CONFIG wrote) is
replaced by QIDI's. Its postinst also replaces saved_variables.cfg with the package's defaults when
saved_variables.cfg.bak was missing beforehand. The firmware update captures both before dpkg and puts them back
afterwards, before anything reads the new config:

- A saved section is carried when the new firmware's config still has that section (in printer.cfg, a file it
  includes, or QIDI's own SAVE_CONFIG block), or it is a profile of one that it has (`bed_mesh <name>`,
  `skew_correction <name>`). Every other section is named as not carried.
- A saved option Valve Oil's install sets in printer.cfg is not carried, since its patch needs the option in the file.
- The operator's values win over QIDI's block, option by option.
- The new printer.cfg body is changed only as Klipper's own SAVE_CONFIG would change it: a body option the restored
  block also sets is commented out (on loading, Klipper lets the body win over the block otherwise).
"""
from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path

from .fs_atomic import atomic_write_text
from .klipper_cfg import AUTOSAVE_HEADER

# Klipper's own header (klippy/configfile.py); its SAVE_CONFIG writes the block below it exactly so.
KLIPPER_HEADER = ("\n" + AUTOSAVE_HEADER + "\n#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n"
                  "#*#\n")
PROFILE_SECTIONS = ("bed_mesh", "skew_correction")
SECTION_LINE = re.compile(r"^\[(?P<name>[^\]]+)\]")
# Klipper's _strip_duplicates: what a body line's comment and option name are.
COMMENT = re.compile(r"[#;].*$")
OPTION_NAME = re.compile(r"[^A-Za-z0-9_].*$")


@dataclass
class Restore:
    carried: list[str] = field(default_factory=list)  # sections whose saved values were put back
    dropped: list[str] = field(default_factory=list)  # what wasn't, each with why
    saved_variables: str | None = None  # "restored" when QIDI's package had replaced them

    def summary(self) -> str:
        notes = []
        if self.carried:
            notes.append("Restored the saved calibration from before the update: "
                         + ", ".join(f"[{name}]" for name in self.carried) + ".")
        if self.dropped:
            notes.append("Not carried over: " + "; ".join(self.dropped) + ".")
        if self.saved_variables == "restored":
            notes.append("Restored saved_variables.cfg, which QIDI's package had reset to its defaults.")
        return " ".join(notes)


def capture(config_root: Path) -> dict:
    """What has to survive QIDI's package: printer.cfg's SAVE_CONFIG block and saved_variables.cfg, as text."""
    printer = config_root / "printer.cfg"
    variables = config_root / "saved_variables.cfg"
    return {
        "autosave": _block_contents(printer.read_text(encoding="utf-8")) if printer.is_file() else [],
        "saved_variables": variables.read_text(encoding="utf-8") if variables.is_file() else None,
    }


def restore(config_root: Path, captured: dict, *, protected: set[tuple[str, str]], firmware: str) -> Restore:
    """Put the captured calibration back into the config QIDI's package installed."""
    result = Restore()
    old = _sections(captured.get("autosave") or [])
    printer = config_root / "printer.cfg"
    if old:
        text = printer.read_text(encoding="utf-8")
        position = text.find(AUTOSAVE_HEADER)
        body = text if position < 0 else text[:position]
        new = _sections(_block_contents(text))
        available = _config_sections(config_root, body) | set(new)
        merged = {name: dict(options) for name, options in new.items()}
        for name, options in old.items():
            base = name.split(None, 1)[0]
            if name not in available and not (base in PROFILE_SECTIONS and base != name and base in available):
                result.dropped.append(f"[{name}] (QIDI firmware {firmware}'s config has no such section)")
                continue
            if (name, "__section__") in protected:
                result.dropped.append(f"[{name}] (Valve Oil's install sets it)")
                continue
            kept = {option: lines for option, lines in options.items() if (name, option) not in protected}
            result.dropped.extend(f"[{name}] {option} (Valve Oil's install sets it)"
                                  for option in options if option not in kept)
            if kept:
                merged.setdefault(name, {}).update(kept)
                result.carried.append(name)
        if result.carried:
            newline = "\r\n" if "\r\n" in body else "\n"
            block = []
            for name, options in merged.items():
                block.append(f"[{name}]")
                for lines in options.values():
                    block.extend(lines)
                block.append("")
            block.pop()  # Klipper ends the block with its last option, not with an empty `#*#` line
            body = _comment_duplicates(body, merged).rstrip()
            text = newline.join([body, *KLIPPER_HEADER.rstrip().split("\n"),
                                 *(("#*# " + line).strip() for line in block), ""])
            atomic_write_text(printer, text)
    result.saved_variables = _restore_saved_variables(config_root / "saved_variables.cfg",
                                                      captured.get("saved_variables"))
    return result


def _block_contents(text: str) -> list[str]:
    """The SAVE_CONFIG block's lines without their `#*# ` prefix, as Klipper reads them; [] without a block."""
    position = text.find(AUTOSAVE_HEADER)
    if position < 0:
        return []
    contents = []
    for line in text[position + len(AUTOSAVE_HEADER):].splitlines():
        if not line.strip():
            continue
        if not line.startswith("#*#"):
            raise ValueError("printer.cfg has lines after its SAVE_CONFIG block that Klipper wouldn't read")
        contents.append(line[4:])
    return contents


def _sections(contents: list[str]) -> dict[str, dict[str, list[str]]]:
    """Sections -> option (lower case, as Klipper compares them) -> the option's lines, continuation lines included."""
    sections: dict[str, dict[str, list[str]]] = {}
    options = lines = None
    for content in contents:
        if not content.strip():
            continue
        match = SECTION_LINE.match(content)
        if match:
            options = sections.setdefault(match.group("name").strip(), {})
            lines = None
        elif content[0].isspace():
            if lines is not None:
                lines.append(content)
        elif options is not None:
            lines = options.setdefault(re.split(r"[:=]", content, maxsplit=1)[0].strip().lower(), [])
            lines[:] = [content]
    return sections


def _config_sections(config_root: Path, body: str) -> set[str]:
    """Every section the config defines: printer.cfg's body and the files it includes."""
    names: set[str] = set()
    seen: set[Path] = set()

    def read(text: str, directory: Path) -> None:
        for line in text.splitlines():
            match = SECTION_LINE.match(line.strip())
            if not match:
                continue
            name = match.group("name").strip()
            if not name.startswith("include "):
                names.add(name)
                continue
            for included in sorted(glob.glob(str(directory / name[len("include "):].strip()))):
                path = Path(included).resolve()
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                included_text = path.read_text(encoding="utf-8", errors="replace")
                position = included_text.find(AUTOSAVE_HEADER)
                read(included_text if position < 0 else included_text[:position], path.parent)

    read(body, config_root)
    return names


def _comment_duplicates(body: str, sections: dict[str, dict[str, list[str]]]) -> str:
    """Klipper's SAVE_CONFIG step: comment out body options the SAVE_CONFIG block sets, with their continuations."""
    lines = body.split("\n")
    section = None
    duplicate = False
    for index, line in enumerate(lines):
        pruned = COMMENT.sub("", line).rstrip()
        if not pruned:
            continue
        if pruned[0].isspace():
            if duplicate:
                lines[index] = "#" + line
            continue
        duplicate = False
        if pruned[0] == "[":
            section = pruned[1:-1].strip()
            continue
        if OPTION_NAME.sub("", pruned).lower() in sections.get(section, {}):
            duplicate = True
            lines[index] = "#" + line
    return "\n".join(lines)


def _variables(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and not line.lstrip().startswith(("[", "#", ";")):
            values[key.strip()] = value.strip()
    return values


def _restore_saved_variables(path: Path, captured: str | None) -> str | None:
    """Put the operator's saved variables back when QIDI's postinst replaced them with its defaults, keeping any
    variable the package added. Returns "restored", or None when the file kept them."""
    if captured is None:
        return None
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    old, new = _variables(captured), _variables(current)
    if all(new.get(key) == value for key, value in old.items()):
        return None
    added = [f"{key} = {value}" for key, value in new.items() if key not in old]
    text = captured if captured.endswith("\n") or not captured else captured + "\n"
    atomic_write_text(path, text + "".join(line + "\n" for line in added))
    return "restored"
