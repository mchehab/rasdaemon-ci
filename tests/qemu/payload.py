# SPDX-License-Identifier: GPL-2.0-only
"""Validate a compiled rasdaemon payload before using it in a test VM."""

import os
import subprocess

from ci import compilation


def read_prebuilt(directory: str, arch: str, source: str) -> str:
    """Require the tested source, architecture and compilation settings to match."""
    # CI mounts this checkout read-only from the runner into a root container.
    command = ["git", "-c", "safe.directory=" + os.path.realpath(source),
               "-C", source, "rev-parse", "HEAD"]
    revision = subprocess.check_output(command, text=True).strip()
    expected = {"source-revision": revision, "architecture": arch,
                "build-key": compilation.build_key(compilation.settings("rasdaemon", arch))}

    for name, value in expected.items():
        with open(os.path.join(directory, name), encoding="utf-8") as stream:
            actual = stream.read().strip()

        if actual != value:
            raise ValueError(f"Cached rasdaemon {name} mismatch: {actual} != {value}")

    if not os.path.isfile(os.path.join(directory, "rasdaemon-install.tar")):
        raise ValueError("Cached rasdaemon install archive is missing")

    with open(os.path.join(directory, "config.h"), encoding="utf-8") as stream:
        return stream.read()
