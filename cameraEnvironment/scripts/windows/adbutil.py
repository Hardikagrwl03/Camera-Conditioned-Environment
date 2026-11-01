"""adb helpers shared by the Windows scripts. Python 3.10+, standard library only."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence


class AdbError(RuntimeError):
    pass


def find_adb(explicit: Optional[str] = None) -> str:
    """--adb, then ANDROID_HOME / ANDROID_SDK_ROOT / ANDROID_SDK platform-tools, then PATH."""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        raise AdbError(f"--adb {explicit} does not exist")
    exe = "adb.exe" if os.name == "nt" else "adb"
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_SDK"):
        base = os.environ.get(var)
        if base:
            cand = Path(base) / "platform-tools" / exe
            if cand.is_file():
                return str(cand)
    found = shutil.which("adb")
    if found:
        return found
    raise AdbError("adb not found: install Android platform-tools, set ANDROID_HOME, or pass --adb <path>")


def norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run(cmd: Sequence[str], timeout: Optional[float] = None, check: bool = True, cwd: Optional[str] = None) -> str:
    """Run a command, return stdout with normalised line endings."""
    try:
        p = subprocess.run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, cwd=cwd)
    except FileNotFoundError as e:
        raise AdbError(f"cannot execute {cmd[0]}: {e}") from e
    out = norm(p.stdout.decode("utf-8", errors="replace"))
    if check and p.returncode != 0:
        err = norm(p.stderr.decode("utf-8", errors="replace"))
        raise AdbError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{err or out}")
    return out


class Adb:
    def __init__(self, adb: str, serial: Optional[str] = None):
        self.adb = adb
        self.serial = serial

    def base(self) -> List[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else [])

    def __call__(self, *args: str, timeout: Optional[float] = 120, check: bool = True) -> str:
        return run(self.base() + list(args), timeout=timeout, check=check)

    def shell(self, *args: str, timeout: Optional[float] = 120, check: bool = True) -> str:
        return self("shell", *args, timeout=timeout, check=check)


def parse_devices(text: str) -> List[Dict[str, str]]:
    """Parse ``adb devices -l`` output into [{serial, state}]."""
    devs = []
    for line in norm(text).split("\n")[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devs.append({"serial": parts[0], "state": parts[1]})
    return devs


def select_device(adb_path: str, serial: Optional[str] = None) -> Adb:
    """Exactly one authorised device, or --serial."""
    devs = parse_devices(run([adb_path, "devices", "-l"], timeout=30))
    ready = [d for d in devs if d["state"] == "device"]
    bad = [d for d in devs if d["state"] != "device"]
    if serial:
        if not any(d["serial"] == serial for d in ready):
            raise AdbError(f"device {serial} not found/ready; adb sees: {devs}")
        return Adb(adb_path, serial)
    if len(ready) == 0:
        hint = f" (not ready: {bad})" if bad else ""
        raise AdbError("no authorised adb device found" + hint + ". Enable USB debugging and accept the prompt.")
    if len(ready) > 1:
        raise AdbError(f"{len(ready)} devices attached ({[d['serial'] for d in ready]}); pass --serial")
    return Adb(adb_path, ready[0]["serial"])


_PROP = re.compile(r"^\[([^\]]+)\]:\s*\[(.*)\]\s*$")


def parse_getprop(text: str) -> Dict[str, str]:
    props = {}
    for line in norm(text).split("\n"):
        m = _PROP.match(line.strip())
        if m:
            props[m.group(1)] = m.group(2)
    return props
