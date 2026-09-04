#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

"""Container entry point for the rasdaemon QEMU functional test image."""

import argparse
import os
import subprocess
import sys


MANIFEST = "/opt/rasdaemon-ci/images.json"


def parser() -> argparse.ArgumentParser:
    """Create the container entry-point argument parser."""
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    for name in ("probe", "run"):
        command = commands.add_parser(name)
        command.add_argument("--arch", default="x86_64")
        if name == "run":
            command.add_argument("--profile", choices=("baseline", "injection", "fuzz"),
                                 default="injection")
            command.add_argument("--fuzz-mode", choices=("random", "zero", "boundaries"),
                                 default="random")
            command.add_argument("--fuzz-seed", type=int, default=1)
            command.add_argument("--accelerator", choices=("auto", "kvm", "tcg"),
                                 default="auto")
            command.add_argument("--timeout", type=int, default=900)
    return value


def main(argv: list[str] | None = None) -> int:
    """Validate mounts and execute the QEMU harness."""
    args = parser().parse_args(argv)
    source = os.environ.get("RASDAEMON_SOURCE", "/workspace")
    results = os.environ.get("RASDAEMON_RESULTS", "/results")
    runner = "/opt/rasdaemon-ci/harness/ras_qemu.py"
    if not os.path.isfile(runner):
        print("rasdaemon-ci: embedded test harness is missing",
              file=sys.stderr)
        return 2
    if not os.path.isfile(os.path.join(source, "meson.build")):
        print(f"rasdaemon-ci: mount a rasdaemon checkout at {source}",
              file=sys.stderr)
        return 2
    command = [sys.executable, str(runner), "--manifest", MANIFEST,
               "--cache-dir", "/opt/rasdaemon-ci", args.command,
               "--arch", args.arch]
    if args.command == "run":
        os.makedirs(results, exist_ok=True)
        command.extend(("--profile", args.profile, "--accelerator",
                        args.accelerator, "--timeout", str(args.timeout),
                        "--result-dir", str(results), "--source-dir",
                        str(source)))
        command.extend(("--fuzz-mode", args.fuzz_mode, "--fuzz-seed", str(args.fuzz_seed)))
    return subprocess.call(command, cwd=source)


if __name__ == "__main__":
    sys.exit(main())
