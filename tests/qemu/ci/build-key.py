#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# pylint: disable=C0103
"""Hash the files that determine a compiled artifact or guest image."""

import argparse
import hashlib
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUTS = {
    "kernel": ["ci/build-kernel.sh", "guest/test-driver"],
    "qemu": ["ci/build-qemu.sh", "oci/Containerfile.build"],
    "rasdaemon": ["ci/build-rasdaemon.sh", "oci/Containerfile.build"],
    "image": ["ci/build-bundle.sh", "ci/build-guest.sh", "ci/firmware.py",
              "guest/bootstrap.sh", "guest/ras-qemu-agent.service",
              "guest/select-kernel.sh", "guest/install-kernel.sh", "oci/Containerfile",
              "oci/rasdaemon-ci.py", "ras_qemu.py", "payload.py", "scenarios.json",
              "fuzz.py", "features.py", "hmp_inject.py", "hisi.py", "results.css",
              "results.js", "guest/agent.py", "guest/consumers.py", "guest/erst.py"],
}


def build_key(component: str) -> str:
    """Include filenames and contents, including additions to input directories."""
    files = []

    for relative in INPUTS[component]:
        path = os.path.join(ROOT, relative)

        if os.path.isdir(path):
            for directory, _, names in os.walk(path):
                files.extend(os.path.join(directory, name) for name in names)
        else:
            files.append(path)

    digest = hashlib.sha256()

    for path in sorted(files):
        digest.update(os.path.relpath(path, ROOT).encode() + b"\0")

        with open(path, "rb") as stream:
            digest.update(stream.read())

        digest.update(b"\0")

    return digest.hexdigest()[:16]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=INPUTS)
    print(build_key(parser.parse_args().component))
