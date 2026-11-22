# vo:klipper-launcher
"""Starts Klipper for klipper.service: QIDI's Klipper, or Valve Oil's beside it when the operator chose it and it can
run. Installed by Valve Oil as /usr/local/lib/vo_klipper_launch.py; klipper.service.d/vo-klipper.conf runs it with the
klippy-env Python and QIDI's $KLIPPER_ARGS instead of QIDI's own command.

At every start it runs Valve Oil's Klipper (/home/qidi/vo-klipper) only when all of these hold:
  - printer_data/.vo_klipper.json says {"use": "valve_oil"};
  - no Valve Oil QIDI firmware update is pending;
  - the tree has the installer's marker, made for the firmware ~/update/firmware_manifest.json names now;
  - QIDI's ~/klipper/klippy has the compiled modules the installer copied from it, and no others;
  - every file the installer put in the tree is still as it was, and c_helper.so is the newest file in its folder;
  - the drop-in that runs this launcher is in place;
  - fewer than three starts in a row on Valve Oil's Klipper went without Klipper acknowledging it was ready.
It then replaces QIDI's klippy.py in the arguments with the tree's, starts there with VO_KLIPPER_LAUNCH_ID set, and
Valve Oil's Klipper writes printer_data/.vo_klipper_ready.json {"launch_id": ...} once ready. Otherwise, and on any
error, it starts QIDI's command unchanged. Each start is recorded in printer_data/.vo_klipper_status.json.

`--check` prints whether Valve Oil's Klipper can run here and what the next start would run, as JSON, and starts and
writes nothing. Each `reason` is a sentence shown to operators as it is; `code` names it for programs.
"""
import hashlib
import json
import os
import stat
import sys
import time
import uuid

DEFAULTS = {
    "python": "/home/qidi/klippy-env/bin/python",
    "qidi_klippy_py": "/home/qidi/klipper/klippy/klippy.py",
    "qidi_workdir": "/home/qidi/klipper",
    "qidi_klippy": "/home/qidi/klipper/klippy",
    "tree_root": "/home/qidi/vo-klipper",
    "printer_data": "/home/qidi/printer_data",
    "firmware_manifest": "/home/qidi/update/firmware_manifest.json",
    "dropin": "/etc/systemd/system/klipper.service.d/vo-klipper.conf",
}
MARKER = ".vo-klipper.json"
CHOICE = ".vo_klipper.json"
STATUS = ".vo_klipper_status.json"
READY = ".vo_klipper_ready.json"
FIRMWARE_UPDATE = ".tltg_firmware_update_pending.json"
DROPIN_COMMAND = "/usr/local/lib/vo_klipper_launch.py $KLIPPER_ARGS"
LAUNCH_ENV = "VO_KLIPPER_LAUNCH_ID"
MAX_UNACKNOWLEDGED = 3
QIDI_CHELPER = "chelper/c_helper.so"


def _json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _safe(relative):
    parts = relative.split("/") if isinstance(relative, str) else []
    return bool(parts) and not relative.startswith("/") and all(part not in ("", ".", "..") for part in parts)


def _same(path, record):
    """Whether the file is the one recorded: by size and time, or by content when its time changed."""
    try:
        status = os.lstat(path)
        if not stat.S_ISREG(status.st_mode) or status.st_size != record["size"]:
            return False
        if status.st_mtime_ns == record["mtime_ns"]:
            return True
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest() == record["sha256"]
    except (OSError, KeyError, TypeError):
        return False


def _qidi_modules_match(qidi_klippy, recorded):
    found = set()
    for directory, dirnames, filenames in os.walk(qidi_klippy):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for name in filenames:
            relative = os.path.relpath(os.path.join(directory, name), qidi_klippy).replace(os.sep, "/")
            if name.endswith(".so") and relative != QIDI_CHELPER:
                found.add(relative)
    return (isinstance(recorded, dict) and bool(recorded) and found == set(recorded)
            and all(_safe(relative) and _same(os.path.join(qidi_klippy, relative), record)
                    for relative, record in recorded.items()))


def _tree_matches(root, files):
    if not isinstance(files, dict) or "klippy/klippy.py" not in files:
        return False
    if not all(_safe(relative) and _same(os.path.join(root, relative), record) for relative, record in files.items()):
        return False
    # Klipper rebuilds c_helper.so when anything beside it is newer, and the printers have no compiler.
    chelper = os.path.join(root, "klippy", "chelper")
    built = os.stat(os.path.join(chelper, "c_helper.so")).st_mtime
    return all(os.stat(os.path.join(chelper, name)).st_mtime <= built for name in os.listdir(chelper)
               if os.path.isfile(os.path.join(chelper, name)))


def _stamp(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


# Shown to operators as they are: what the next start runs, and why.
REASONS = {
    "selected": "Valve Oil's Klipper is selected.",
    "not_selected": "QIDI's Klipper is selected.",
    "choice_unreadable": "The Klipper choice couldn't be read, so QIDI's Klipper runs.",
    "firmware_update_pending": "A QIDI firmware update is in progress.",
    "not_installed": "Valve Oil's Klipper isn't installed on this printer.",
    "firmware_changed": "QIDI's firmware changed since Valve Oil's Klipper was installed.",
    "qidi_modules_changed": "QIDI's Klipper modules changed since Valve Oil's Klipper was installed.",
    "tree_changed": "Valve Oil's Klipper files changed since they were installed.",
    "launcher_not_installed": "Klipper doesn't start through Valve Oil's launcher.",
    "not_ready": f"Valve Oil's Klipper didn't reach ready in {MAX_UNACKNOWLEDGED} starts.",
    "arguments_unrecognized": "QIDI's Klipper command isn't the one Valve Oil's launcher knows.",
    "error": "Valve Oil's launcher hit an error, so QIDI's Klipper runs.",
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _unavailable(paths, marker, soc):
    """Why Valve Oil's Klipper can't run on this printer now, or None when it can."""
    for code, failed in (
        ("firmware_update_pending", lambda: os.path.lexists(os.path.join(paths["printer_data"], FIRMWARE_UPDATE))),
        ("not_installed", lambda: marker is None),
        ("firmware_changed", lambda: soc is None or marker.get("soc") != soc),
        ("qidi_modules_changed", lambda: not _qidi_modules_match(paths["qidi_klippy"], marker.get("qidi_modules"))),
        ("tree_changed", lambda: not _tree_matches(paths["tree_root"], marker.get("files"))),
        ("launcher_not_installed", lambda: not _dropin_active(paths["dropin"])),
    ):
        if failed():
            return code
    return None


def _dropin_active(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return DROPIN_COMMAND in handle.read()
    except OSError:
        return False


def decide(paths=None, check=False):
    """What the next start runs. Valve Oil's Klipper is checked only when it is selected, or for `check`."""
    paths = dict(DEFAULTS, **(paths or {}))
    data = paths["printer_data"]
    manifest = _json(paths["firmware_manifest"])
    soc = manifest.get("SOC") if manifest is not None else None
    soc = {"version": soc.get("version"), "file": soc.get("file")} if isinstance(soc, dict) else None
    marker = _json(os.path.join(paths["tree_root"], MARKER))
    if marker is not None and marker.get("schema") != 1:
        marker = None
    choice_path = os.path.join(data, CHOICE)
    choice = _json(choice_path)
    selected = "valve_oil" if choice is not None and choice.get("use") == "valve_oil" else "qidi"
    decision = {"selected": selected, "running": "qidi", "code": "not_selected", "launch_id": None, "attempts": 0,
                "available": None, "unavailable_code": None,
                "firmware": soc["version"] if soc else None, "version": marker.get("klipper_version") if marker else None,
                "checked_at": _now()}
    if selected == "valve_oil" or check:
        unavailable = _unavailable(paths, marker, soc)
        decision.update(available=unavailable is None, unavailable_code=unavailable)
    if selected != "valve_oil":
        if choice is None and os.path.lexists(choice_path):
            decision["code"] = "choice_unreadable"
        return decision
    if decision["unavailable_code"]:
        decision["code"] = decision["unavailable_code"]
        return decision
    # Starts in a row without Klipper's ready acknowledgement, counted afresh when the operator chooses Valve Oil's
    # Klipper again or the tree is installed again.
    basis = f"{_stamp(choice_path)}:{_stamp(os.path.join(paths['tree_root'], MARKER))}"
    previous = _json(os.path.join(data, STATUS)) or {}
    ready = _json(os.path.join(data, READY)) or {}
    attempts = previous.get("attempts")
    attempts = attempts if previous.get("basis") == basis and isinstance(attempts, int) and attempts >= 0 else 0
    if previous.get("running") == "valve_oil" and previous.get("launch_id") and ready.get("launch_id") == previous["launch_id"]:
        attempts = 0
    decision.update(basis=basis, attempts=attempts)
    if attempts >= MAX_UNACKNOWLEDGED:
        decision["code"] = "not_ready"
        return decision
    decision.update(running="valve_oil", code="selected")
    return decision


def status_record(decision):
    """What printer_data/.vo_klipper_status.json holds after a start."""
    return {"selected": decision["selected"], "running": decision["running"], "reason": REASONS[decision["code"]],
            "code": decision["code"], "launch_id": decision["launch_id"], "attempts": decision["attempts"],
            "firmware": decision["firmware"], "version": decision["version"], "checked_at": decision["checked_at"],
            "basis": decision.get("basis")}


def check_record(decision):
    """What `--check` prints."""
    unavailable = decision["unavailable_code"]
    return {"available": decision["available"], "unavailable_reason": REASONS[unavailable] if unavailable else None,
            "unavailable_code": unavailable, "selected": decision["selected"], "would_run": decision["running"],
            "reason": REASONS[decision["code"]], "code": decision["code"], "attempts": decision["attempts"],
            "firmware": decision["firmware"], "version": decision["version"], "checked_at": decision["checked_at"]}


def _write_status(paths, decision):
    path = os.path.join(paths["printer_data"], STATUS)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(status_record(decision), handle, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _error():
    return {"selected": "qidi", "running": "qidi", "code": "error", "launch_id": None, "attempts": 0,
            "available": None, "unavailable_code": None, "firmware": None, "version": None, "checked_at": _now()}


def main(argv=None, paths=None, execv=os.execv, execve=os.execve):
    argv = list(sys.argv[1:] if argv is None else argv)
    paths = dict(DEFAULTS, **(paths or {}))
    if argv[:1] == ["--check"]:
        try:
            decision = decide(paths, check=True)
        except Exception:
            decision = _error()
        print(json.dumps(check_record(decision), sort_keys=True))
        return 0
    qidi = [paths["python"], *argv]
    command = qidi
    try:
        decision = decide(paths)
        if decision["running"] == "valve_oil":
            found = [index for index, argument in enumerate(argv) if argument == paths["qidi_klippy_py"]]
            if len(found) != 1:
                decision.update(running="qidi", code="arguments_unrecognized")
            else:
                command = list(qidi)
                command[found[0] + 1] = os.path.join(paths["tree_root"], "klippy", "klippy.py")
                decision.update(launch_id=uuid.uuid4().hex, attempts=decision["attempts"] + 1)
    except Exception:
        decision = _error()
    try:
        _write_status(paths, decision)
    except Exception:
        pass
    if decision["running"] == "valve_oil":
        try:
            os.chdir(paths["tree_root"])
            execve(paths["python"], command, dict(os.environ, **{LAUNCH_ENV: decision["launch_id"]}))
        except Exception:
            try:
                _write_status(paths, _error())
            except Exception:
                pass
    try:
        os.chdir(paths["qidi_workdir"])
    except Exception:
        pass
    os.environ.pop(LAUNCH_ENV, None)
    execv(paths["python"], qidi)
    return 0


if __name__ == "__main__":
    sys.exit(main())
