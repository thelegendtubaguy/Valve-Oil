"""Reads gcode_macro sections and their static calls out of Klipper config files, for the checkers and tests that
hold macros to invariants."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from installer.runtime.manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]

MACRO_ROOT = REPO_ROOT / "installer" / "klipper" / "macros"
PRINTER_MACRO_ROOT = REPO_ROOT / "installer" / "klipper" / "printers"


def installed_macro_files(printer: str) -> tuple[Path, ...]:
    """The optimized macro files a printer receives: the common macros and the printer's own folder."""
    return tuple(
        sorted((*MACRO_ROOT.glob("*.cfg"), *(PRINTER_MACRO_ROOT / printer).glob("*.cfg")))
    )


SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*(?:[#;].*)?$")
GCODE_MACRO_RE = re.compile(r"^gcode_macro\s+(?P<name>.+)$", re.IGNORECASE)
OPTION_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.-]+)(?P<sep>\s*[:=]\s*)(?P<rest>.*)$")
JINJA_BLOCK_RE = re.compile(r"\{%.*?%\}")
COMMAND_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*)(?=$|[\s\[])")
COMMENT_PREFIXES = ("#", ";")
EXCLUDED_CONFIG_DIRS = {"kamp"}
EXCLUDED_CONFIG_FILES = {"fluidd.cfg"}


@dataclass(frozen=True)
class MacroDefinition:
    name: str
    path: Path
    line_number: int
    gcode_lines: tuple[tuple[int, str], ...]

    @property
    def key(self) -> str:
        return self.name.upper()


@dataclass(frozen=True)
class MacroCall:
    caller: str
    callee: str
    path: Path
    line_number: int
    raw_line: str


def installed_config_trees() -> tuple[tuple[str, Path, str | None], ...]:
    """Each printer with the config tree its optimized macros are installed into, and that tree's firmware (None for
    the repository's own config/, the Max 4's, which every Max 4 firmware's patches apply to)."""
    plus5_root = REPO_ROOT / "installer/stock/qidi-plus5-defaults/firmwares/01.01.02.01"
    smart5_root = REPO_ROOT / "installer/stock/qidi-smart5-defaults/firmwares/01.01.02.01"
    return (("max4", REPO_ROOT, None), ("plus5", plus5_root, "01.01.02.01"), ("smart5", smart5_root, "smart5-01.01.02.01"))


def macro_call_graph_errors() -> list[str]:
    """Duplicate gcode_macro definitions and recursive macro calls in what each printer runs once installed."""
    errors: list[str] = []
    for printer, root, firmware in installed_config_trees():
        files = unique_paths((*collect_config_tree_files(root / "config"), *installed_macro_files(printer)))
        macros, duplicates = parse_macro_definitions(files, excluded_sections=manifest_deleted_sections(root, firmware))
        if duplicates:
            errors.append(f"{printer}: duplicate gcode_macro definitions:\n"
                          + "\n".join(format_duplicate(item) for item in duplicates))
            continue
        calls = tuple(iter_static_macro_calls(macros))
        cycles = find_cycles(macros, calls)
        if cycles:
            errors.append(f"{printer}: recursive gcode_macro calls:\n" + format_cycles(cycles, macros, calls))
    return errors


def collect_config_tree_files(config_root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in config_root.rglob("*.cfg")
            if path.name.lower() not in EXCLUDED_CONFIG_FILES
            and not any(
                part.lower() in EXCLUDED_CONFIG_DIRS
                for part in path.relative_to(config_root).parts
            )
        )
    )


def unique_paths(paths) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return tuple(result)


def manifest_deleted_sections(
    root: Path = REPO_ROOT, firmware: str | None = None
) -> frozenset[tuple[Path, str]]:
    """Sections the installer removes, under a tree laid out like the printer's: every firmware's by default."""
    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    return frozenset(
        ((root / patch.file).resolve(), patch.section.lower())
        for patch in manifest.patches.delete_sections
        if firmware is None or any(firmware in variant.firmwares for variant in patch.variants)
    )


def parse_macro_definitions(
    files: tuple[Path, ...],
    *,
    excluded_sections: frozenset[tuple[Path, str]] = frozenset(),
) -> tuple[dict[str, MacroDefinition], list[tuple[MacroDefinition, MacroDefinition]]]:
    macros: dict[str, MacroDefinition] = {}
    duplicates: list[tuple[MacroDefinition, MacroDefinition]] = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        resolved_path = path.resolve()
        for section_name, header_index, end_index in iter_sections(lines):
            if (resolved_path, section_name.lower()) in excluded_sections:
                continue
            match = GCODE_MACRO_RE.match(section_name)
            if not match:
                continue
            macro = MacroDefinition(
                name=match.group("name").strip(),
                path=path,
                line_number=header_index + 1,
                gcode_lines=extract_gcode_lines(lines, header_index + 1, end_index),
            )
            existing = macros.get(macro.key)
            if existing is not None:
                duplicates.append((existing, macro))
                continue
            macros[macro.key] = macro
    return macros, duplicates


def parse_section_definitions(
    files: tuple[Path, ...],
    name: str,
    *,
    excluded_sections: frozenset[tuple[Path, str]] = frozenset(),
) -> list[MacroDefinition]:
    """Every [name] section's G-code in `files`, for sections that run G-code without being a gcode_macro
    (homing_override)."""
    found: list[MacroDefinition] = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for section_name, header_index, end_index in iter_sections(lines):
            if section_name.lower() != name.lower() or (path.resolve(), section_name.lower()) in excluded_sections:
                continue
            found.append(MacroDefinition(name=section_name, path=path, line_number=header_index + 1,
                                         gcode_lines=extract_gcode_lines(lines, header_index + 1, end_index)))
    return found


def iter_sections(lines: list[str]):
    current_name: str | None = None
    current_start: int | None = None
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line)
        if not match:
            continue
        if current_name is not None and current_start is not None:
            yield current_name, current_start, index
        current_name = match.group("name").strip()
        current_start = index
    if current_name is not None and current_start is not None:
        yield current_name, current_start, len(lines)


def extract_gcode_lines(lines: list[str], start_index: int, end_index: int) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    in_gcode = False
    for index in range(start_index, end_index):
        raw_line = lines[index]
        if not in_gcode:
            option = OPTION_RE.match(raw_line)
            if option and option.group("key").lower() == "gcode":
                in_gcode = True
            continue
        if raw_line.strip() and not raw_line[0].isspace() and OPTION_RE.match(raw_line):
            break
        result.append((index + 1, raw_line))
    return tuple(result)


def iter_static_macro_calls(macros: dict[str, MacroDefinition]):
    macro_keys = set(macros)
    for caller_key, macro in macros.items():
        for line_number, raw_line in macro.gcode_lines:
            for command in iter_static_commands(raw_line):
                callee_key = command.upper()
                if callee_key in macro_keys:
                    yield MacroCall(
                        caller=caller_key,
                        callee=callee_key,
                        path=macro.path,
                        line_number=line_number,
                        raw_line=raw_line,
                    )


def iter_static_commands(raw_line: str):
    without_comment = strip_gcode_comment(raw_line)
    for segment in JINJA_BLOCK_RE.split(without_comment):
        segment = segment.strip()
        if not segment or segment.startswith("{"):
            continue
        match = COMMAND_RE.match(segment)
        if match:
            yield match.group(1)


def strip_gcode_comment(raw_line: str) -> str:
    for index, char in enumerate(raw_line):
        if char == ";":
            return raw_line[:index]
        if char == "#" and (index == 0 or raw_line[index - 1].isspace()):
            return raw_line[:index]
    return raw_line


def find_cycles(macros: dict[str, MacroDefinition], calls: tuple[MacroCall, ...]) -> list[tuple[str, ...]]:
    graph: dict[str, set[str]] = {key: set() for key in macros}
    for call in calls:
        graph[call.caller].add(call.callee)

    cycles: list[tuple[str, ...]] = []
    seen_cycles: set[tuple[str, ...]] = set()
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
        visiting.add(node)
        stack.append(node)
        for child in sorted(graph[node]):
            if child in visiting:
                cycle = tuple(stack[stack.index(child) :] + [child])
                canonical = canonical_cycle(cycle)
                if canonical not in seen_cycles:
                    seen_cycles.add(canonical)
                    cycles.append(cycle)
                continue
            if child not in visited:
                visit(child)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for macro_key in sorted(macros):
        if macro_key not in visited:
            visit(macro_key)
    return cycles


def canonical_cycle(cycle: tuple[str, ...]) -> tuple[str, ...]:
    nodes = list(cycle[:-1])
    start_index = min(range(len(nodes)), key=lambda index: nodes[index])
    rotated = nodes[start_index:] + nodes[:start_index]
    return tuple(rotated + [rotated[0]])


def format_cycles(
    cycles: list[tuple[str, ...]], macros: dict[str, MacroDefinition], calls: tuple[MacroCall, ...]
) -> str:
    call_lookup = {(call.caller, call.callee): call for call in calls}
    rows: list[str] = []
    for cycle in cycles:
        rows.append("  " + " -> ".join(macros[key].name for key in cycle))
        for caller, callee in zip(cycle, cycle[1:]):
            call = call_lookup[(caller, callee)]
            rows.append(
                f"    {relative(call.path)}:{call.line_number}: "
                f"{macros[caller].name} -> {macros[callee].name}: {call.raw_line.strip()}"
            )
    return "\n".join(rows)


def format_duplicate(item: tuple[MacroDefinition, MacroDefinition]) -> str:
    first, second = item
    return (
        f"  {first.name}: {relative(first.path)}:{first.line_number} and "
        f"{relative(second.path)}:{second.line_number}"
    )


def relative(path: Path) -> Path:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path
