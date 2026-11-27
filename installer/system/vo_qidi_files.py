# vo:qidi-files
"""The print file list, its statistics, and the official filament list, answered for QIDI's Moonraker as QIDI's app did.

Installed by Valve Oil as /usr/local/lib/vo_qidi_files.py beside vo_qidi_agent.py, which loads it. QIDI's extensions
component serves /api/qidiclient/files/list, files/stats, and config/offical_filament_list by asking the extension
agent, which was QIDI's touchscreen app until Valve Oil patched that duty out of it. These answers keep the app's result
shapes: the same keys, sorted as the app's JSON library sorts them, the same strings where the app sent strings, and the
same time and weight forms.

The file list holds every .gcode and .gcode.3mf file under printer_data/gcodes and printer_data/.cache, sorted by path,
so the copy of the current or last print in .cache is listed beside the original as the app lists it. A .gcode.3mf
file's plates come from Metadata/slice_info.config, with the bed type and nozzle from each plate's JSON and the
thumbnail sizes from its plate PNG. A plain .gcode file gets one plate with only what the app read from it: the colors,
types, and estimated time the slicer wrote in its comments. Each file is parsed once and kept until its modification
time or size changes, and the walk and parsing run in a worker thread so the agent keeps answering Moonraker meanwhile.

The filament list is rebuilt from printer_data/config/officiall_filas_list.cfg with the color and vendor tables the app
carries compiled in, which differ from that file's own [colordict] and [vendor_list].
"""
import asyncio
import json
import logging
import os
import re
import threading
import xml.etree.ElementTree as ElementTree
import zipfile

LOG = logging.getLogger("vo_qidi_files")

FILAMENT_LIST = "config/officiall_filas_list.cfg"
FILAMENT_COUNT = 50
# The app's compiled-in tables, which its answer carries instead of the cfg's sections of the same names.
COLORDICT = {
    "1": "#FAFAFA", "2": "#060606", "3": "#D9E3ED", "4": "#5CF30E", "5": "#63E492", "6": "#2850FF", "7": "#FF98FE",
    "8": "#DFD628", "9": "#228332", "10": "#99DEFF", "11": "#1714B0", "12": "#CEC0FE", "13": "#CADF4B",
    "14": "#1353AB", "15": "#5EA9FD", "16": "#A878FF", "17": "#FF7174", "18": "#FF362D", "19": "#E2DFCD",
    "20": "#898F8B", "21": "#6E3812", "22": "#CAC59F", "23": "#F28636", "24": "#B87F2B",
}
VENDOR_LIST = {
    "1": "Anycubic", "2": "Bambu", "3": "Creality", "4": "Elegoo", "5": "Generic", "6": "HATCHBOX", "7": "Overture",
    "8": "Polymaker", "9": "QIDI",
}

# How much of a plain .gcode file is read for the slicer's comments: its header and its closing block.
GCODE_HEAD_BYTES = 64 * 1024
GCODE_TAIL_BYTES = 512 * 1024
GCODE_COMMENTS = {
    "filament_colour": "; filament_colour = ",
    "filament_type": "; filament_type = ",
    "estimated_time": "; estimated printing time (normal mode) = ",
}
# The app upper-cases object names a byte at a time, leaving letters outside ASCII as they are.
ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _sorted(mapping):
    """A dict in the key order the app's JSON objects serialize in."""
    return {key: mapping[key] for key in sorted(mapping)}


def compact_time(seconds):
    """A plate's print time as the app writes it: whole minutes, no space, hours only when there are any."""
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    return f"{hours}h{minutes}m" if hours else f"{minutes}m"


def spaced_time(seconds):
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def slicer_seconds(text):
    """Seconds in a slicer's "1d 2h 3m 4s" time."""
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    return sum(int(number) * units[unit] for number, unit in re.findall(r"(\d+)\s*([dhms])", text))


def _grams(value):
    try:
        return f"{int(float(value))}g"
    except (TypeError, ValueError):
        return ""


def _plate(*, bed_type="", colours=(), types=(), weight="", nozzle="", index="", seconds=0, extruders=(),
           print_time=None):
    return {
        "bed_type": bed_type,
        "filament_colour": ";".join(colours),
        "filament_type": ";".join(types),
        "filament_weight": weight,
        "nozzle_diameter": nozzle,
        "plate_index": index,
        "print_time": compact_time(seconds) if print_time is None else print_time,
        "print_time_seconds": seconds,
        "used_extruders": ";".join(extruders),
    }


def _entry(path, extension, plates, objects, thumbnails, show_time):
    return {
        "extension": extension,
        "filename": os.path.basename(path),
        "filepath": path,
        "plate_count": str(len(plates)),
        "plates": plates,
        "print_objects": objects,
        "show_filament_weight": plates[0]["filament_weight"] if plates else "",
        "show_print_time": show_time,
        "thumbnails": thumbnails,
    }


def _number(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_3mf(path):
    """The list entry for a .gcode.3mf file."""
    plates, objects, thumbnails = [], [], []
    try:
        with zipfile.ZipFile(path) as archive:
            names = {info.filename: info for info in archive.infolist()}
            root = ElementTree.fromstring(archive.read("Metadata/slice_info.config"))
            for plate in root.iter("plate"):
                metadata = {item.get("key"): item.get("value") for item in plate.findall("metadata")}
                index = str(_number(metadata.get("index"), len(plates) + 1))
                details = {}
                json_name = f"Metadata/plate_{index}.json"
                if json_name in names:
                    try:
                        details = json.loads(archive.read(json_name).decode("utf-8"))
                    except ValueError:
                        details = {}
                nozzle = details.get("nozzle_diameter")
                if nozzle is None:
                    nozzle = (metadata.get("nozzle_diameters") or "").split(" ")[0] or None
                filaments = plate.findall("filament")
                plates.append(_plate(
                    bed_type=str(details.get("bed_type") or ""),
                    colours=[item.get("color", "") for item in filaments],
                    types=[item.get("type", "") for item in filaments],
                    weight=_grams(metadata.get("weight")),
                    nozzle="" if nozzle is None else "%f" % float(nozzle),
                    index=index,
                    seconds=_number(metadata.get("prediction")),
                    extruders=[str(_number(item.get("id"), 1) - 1) for item in filaments],
                ))
                for item in plate.findall("object"):
                    objects.append({
                        "identify_id": _number(item.get("identify_id")),
                        "name": (item.get("name") or "").translate(ASCII_UPPER),
                        "skipped": (item.get("skipped") or "").lower() == "true",
                    })
                png = names.get(f"Metadata/plate_{index}.png")
                if png is not None:
                    thumbnails.append({"data_size": png.file_size, "height": int(index), "width": -int(index)})
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        LOG.info("could not read %s: %s", path, exc)
    show_time = spaced_time(plates[0]["print_time_seconds"]) if plates else ""
    return _entry(path, ".3mf", plates, objects, thumbnails, show_time)


def _gcode_comments(path):
    found = {}
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            head = handle.read(GCODE_HEAD_BYTES)
            tail = b""
            if size > GCODE_HEAD_BYTES:
                handle.seek(max(GCODE_HEAD_BYTES, size - GCODE_TAIL_BYTES))
                tail = handle.read()
    except OSError as exc:
        LOG.info("could not read %s: %s", path, exc)
        return found
    for block in (head, tail):
        for line in block.decode("utf-8", errors="replace").splitlines():
            for key, prefix in GCODE_COMMENTS.items():
                if key not in found and line.startswith(prefix):
                    found[key] = line[len(prefix) - 1:].rstrip("\r")  # the app keeps the space after "="
    return found


def parse_gcode(path):
    """The list entry for a plain .gcode file: one plate with what the app read from the slicer's comments."""
    comments = _gcode_comments(path)
    estimated = comments.get("estimated_time", "")
    seconds = slicer_seconds(estimated)
    # The app shows the slicer's own text, to the minute.
    show_time = re.sub(r"\s+\d+s\s*$", "", estimated) if re.search(r"[dhm]", estimated) else estimated
    colours = comments.get("filament_colour", "").strip()
    types = comments.get("filament_type", "").strip()
    plate = _plate(colours=[colours] if colours else [], types=[types] if types else [], index="1", seconds=seconds)
    return _entry(path, ".gcode", [plate], [], [], show_time)


def _extension(name):
    if name.endswith(".gcode.3mf"):
        return ".3mf"
    if name.endswith(".gcode"):
        return ".gcode"
    return None


class FileList:
    """The app's print file list, each file parsed once per modification time and size."""

    def __init__(self, printer_data):
        self.roots = [os.path.join(printer_data, ".cache"), os.path.join(printer_data, "gcodes")]
        self.root_path = os.path.join(printer_data, "gcodes")
        self._cache = {}
        self._lock = threading.Lock()

    def _paths(self):
        for root in self.roots:
            for directory, folders, files in os.walk(root):
                folders.sort()
                for name in files:
                    extension = _extension(name)
                    if extension is None:
                        continue
                    path = os.path.join(directory, name)
                    try:
                        path.encode("utf-8")
                    except UnicodeEncodeError:
                        LOG.info("skipping a file whose name is not UTF-8 in %s", directory)
                        continue
                    yield path, extension

    def scan(self):
        """Every listed file's entry, sorted by path. Runs in a worker thread."""
        with self._lock:
            entries, seen = [], set()
            for path, extension in self._paths():
                try:
                    info = os.stat(path)
                except OSError:
                    continue
                seen.add(path)
                key = (info.st_mtime_ns, info.st_size)
                cached = self._cache.get(path)
                if cached is None or cached[0] != key:
                    entry = parse_3mf(path) if extension == ".3mf" else parse_gcode(path)
                    cached = self._cache[path] = (key, entry)
                entries.append(cached[1])
            for path in set(self._cache) - seen:
                del self._cache[path]
            entries.sort(key=lambda entry: entry["filepath"])
            return entries


def file_stats(files, root_path):
    """The app's statistics over its file list: plate 1's time and weight only, .cache copies included."""
    types = {}
    weight = seconds = 0.0
    plates = 0
    for entry in files:
        types[entry["extension"]] = types.get(entry["extension"], 0) + 1
        match = re.match(r"\s*(\d+(?:\.\d+)?)", entry.get("show_filament_weight") or "")
        weight += float(match.group(1)) if match else 0.0
        plates += _number(entry.get("plate_count"))
        if entry.get("plates"):
            seconds += entry["plates"][0].get("print_time_seconds") or 0
    return {
        "file_types": _sorted(types),
        "root_path": root_path,
        "total_filament_weight_grams": float(weight),
        "total_files": len(files),
        "total_plates": plates,
        "total_print_time_seconds": float(seconds),
    }


def read_cfg(path):
    """A Klipper-style cfg file's sections: {section: {key: value}}."""
    sections, current = {}, None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line[0] in "#;":
                continue
            if line.startswith("[") and line.endswith("]"):
                current = sections.setdefault(line[1:-1].strip(), {})
                continue
            if current is None:
                continue
            match = re.match(r"([^=:]+?)\s*[=:]\s*(.*)$", line)
            if match:
                current[match.group(1).strip().lower()] = match.group(2).strip()
    return sections


def filament_list(cfg_path):
    """The app's official filament list from officiall_filas_list.cfg and its compiled-in tables."""
    try:
        sections = read_cfg(cfg_path)
    except OSError as exc:
        LOG.info("could not read %s: %s", cfg_path, exc)
        sections = {}
    keys = sorted(str(number) for number in range(1, FILAMENT_COUNT + 1))
    result = {}
    for field, number in (("box_max_temp", True), ("box_min_temp", True), ("filament", False),
                          ("max_temp", True), ("min_temp", True), ("type", False)):
        values = {}
        for key in keys:
            value = sections.get("fila" + key, {}).get(field, "")
            values[key] = _number(value) if number else value
        result[field] = values
    result["colordict"] = _sorted(COLORDICT)
    result["vendor_list"] = _sorted(VENDOR_LIST)
    return _sorted(result)


class QidiFiles:
    """The agent methods, answered from a worker thread."""

    def __init__(self, context):
        self.files = FileList(context.printer_data)
        self.cfg_path = os.path.join(context.printer_data, FILAMENT_LIST)
        self._filaments = None
        self._lock = None

    async def _in_thread(self, function, *args):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(None, function, *args)

    async def list(self, params=None):
        return await self._in_thread(self.files.scan)

    async def stats(self, params=None):
        files = await self._in_thread(self.files.scan)
        return file_stats(files, self.files.root_path)

    def _filament_list(self):
        try:
            info = os.stat(self.cfg_path)
            key = (info.st_mtime_ns, info.st_size)
        except OSError:
            key = None
        if self._filaments is None or self._filaments[0] != key:
            self._filaments = (key, filament_list(self.cfg_path))
        return self._filaments[1]

    async def offical_filament_list(self, params=None):
        return await self._in_thread(self._filament_list)


def setup(context):
    files = QidiFiles(context)
    context.method("qidiclient.files.list", files.list)
    context.method("qidiclient.files.stats", files.stats)
    context.method("qidiclient.config.offical_filament_list", files.offical_filament_list)
    return files
