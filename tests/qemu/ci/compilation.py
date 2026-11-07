#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compilation settings shared by cache keys and actual build commands."""

import argparse
import hashlib
import json
import os


CONFIGURATION_DIR = os.path.join(os.path.dirname(__file__), "compilation")


def settings(component: str, arch: str, directory: str = CONFIGURATION_DIR) -> dict:
    """Resolve common options plus architecture-specific additions."""
    if component not in ("kernel", "qemu", "rasdaemon"):
        raise ValueError(f"Unsupported component: {component}")

    with open(os.path.join(directory, component + ".json"), encoding="utf-8") as stream:
        configuration = json.load(stream)

    result = dict(configuration["common"])

    for name, value in configuration["architectures"][arch].items():
        result[name] = result.get(name, []) + value if isinstance(value, list) else value

    return result


def build_key(configuration: dict) -> str:
    """Ignore JSON formatting while retaining every effective compilation option."""
    serialized = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def main() -> None:
    """Print effective settings or their cache key."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("kernel", "qemu", "rasdaemon"))
    parser.add_argument("arch", choices=("x86_64", "aarch64"))
    parser.add_argument("--key", action="store_true")
    args = parser.parse_args()
    configuration = settings(args.component, args.arch)
    print(build_key(configuration) if args.key else json.dumps(configuration))


if __name__ == "__main__":
    main()
