# SPDX-License-Identifier: GPL-2.0-only
"""Validate a compiled payload against the source and recipe being tested."""

import os
import subprocess
import sys


def read_prebuilt(directory: str, arch: str, source: str) -> str:
    """Require the source revision, architecture and build recipe to match."""
    command = ["git", "-c", "safe.directory=" + os.path.realpath(source),
               "-C", source, "rev-parse", "HEAD"]
    revision = subprocess.check_output(command, text=True).strip()
    recipe = os.path.join(os.path.dirname(__file__), "ci", "build-key.py")
    key = subprocess.check_output([sys.executable, recipe, "rasdaemon"], text=True).strip()
    expected = {"source-revision": revision, "architecture": arch, "build-key": key}

    for name, value in expected.items():
        with open(os.path.join(directory, name), encoding="utf-8") as stream:
            actual = stream.read().strip()

        if actual != value:
            raise ValueError(f"Cached rasdaemon {name} mismatch: {actual} != {value}")

    if not os.path.isfile(os.path.join(directory, "rasdaemon-install.tar")):
        raise ValueError("Cached rasdaemon install archive is missing")

    with open(os.path.join(directory, "config.h"), encoding="utf-8") as stream:
        return stream.read()
