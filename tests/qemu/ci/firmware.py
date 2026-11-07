#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

"""Select installed QEMU firmware using its supplied descriptors."""

import argparse
import fnmatch
import glob
import json
import os
import re
import shutil
import subprocess
import sys


def machine_names(binary: str, machine: str) -> set[str]:
    """Resolve aliases using the selected QEMU binary, not a version table."""
    listing = subprocess.run([binary, "-machine", "help"], check=True,
                             capture_output=True, text=True).stdout
    machines = {machine}

    for line in listing.splitlines():
        fields = line.split()

        if fields and fields[0] == machine:
            alias = re.search(r"\(alias of ([^)]+)\)", line)

            if alias:
                machines.add(alias.group(1))

    return machines


def firmware_pair(descriptor: dict, arch: str, machines: set[str]) -> dict:
    """Read a compatible pair supported by the harness's raw flash interface."""
    compatible = any(target.get("architecture") == arch and
                     any(fnmatch.fnmatchcase(name, pattern)
                         for pattern in target.get("machines", [])
                         for name in machines) for target in descriptor.get("targets", []))
    features = set(descriptor.get("features", []))

    if not compatible or "uefi" not in descriptor.get("interface-types", []):
        return {}

    if features.intersection({"requires-smm", "secure-boot", "enrolled-keys"}):
        return {}

    mapping = descriptor.get("mapping", {})
    executable = mapping.get("executable", {})
    nvram = mapping.get("nvram-template", {})

    if mapping.get("device") != "flash" or any(
            item.get("format") != "raw" for item in (executable, nvram)):
        return {}

    return {"code": executable.get("filename", ""),
            "vars": nvram.get("filename", "")}


def select_firmware(root: str, binary: str, arch: str, machine: str) -> dict:
    """Return a usable raw flash pair for the requested architecture and machine."""
    machines = machine_names(binary, machine)
    prefix = os.path.dirname(os.path.dirname(os.path.abspath(binary)))
    directories = {os.path.join(prefix, "share", "qemu", "firmware"),
                   os.path.join(root, "usr", "share", "qemu", "firmware"),
                   os.path.join(root, "usr", "local", "share", "qemu", "firmware")}
    descriptors = sorted(path for directory in directories
                         for path in glob.glob(os.path.join(directory, "*.json")))
    errors = []

    for path in descriptors:
        with open(path, encoding="utf-8") as stream:
            descriptor = json.load(stream)

        pair = firmware_pair(descriptor, arch, machines)

        if not pair:
            continue

        missing = [name for name in pair.values() if not os.path.isabs(name) or
                   not os.path.isfile(os.path.join(root, name.lstrip("/")))]

        if missing:
            errors.append(f"{path}: missing firmware files: {', '.join(missing)}")
            continue

        return {"pairs": [pair]}

    detail = "; ".join(errors) or "no compatible raw UEFI flash descriptor found"
    raise ValueError(f"QEMU firmware for {arch}/{machine}: {detail}")


def main() -> int:
    """Print manifest firmware metadata or a code path for guest provisioning."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/")
    parser.add_argument("--binary", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--code-only", action="store_true")
    args = parser.parse_args()
    binary = shutil.which(args.binary)

    if not binary:
        parser.error(f"QEMU binary not found: {args.binary}")

    try:
        firmware = select_firmware(args.root, binary, args.arch, args.machine)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(firmware["pairs"][0]["code"] if args.code_only else json.dumps(firmware))
    return 0


if __name__ == "__main__":
    sys.exit(main())
