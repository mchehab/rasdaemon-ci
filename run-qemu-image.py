#!/usr/bin/env python3
"""Download the latest CI QEMU image artifact and run it locally."""

import argparse
import datetime
import os
import shutil
import subprocess
from typing import Any


def log(message: str) -> None:
    """Print a timestamped message."""
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{now}] {message}", flush=True)


def command(args: list[str], **kwargs: Any) -> int:
    """Run a command while prefixing its output with timestamps."""
    with subprocess.Popen(args, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          bufsize=1, **kwargs) as process:
        assert process.stdout is not None

        for line in process.stdout:
            log(line.rstrip("\n"))

        status = process.wait()

    if status:
        raise subprocess.CalledProcessError(status, args)

    return status


def main() -> None:
    """Download and run the selected CI image."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default="mchehab/rasdaemon-ci")
    parser.add_argument("--arch", default="x86_64")
    parser.add_argument("--source-dir", default="../rasdaemon")
    parser.add_argument("--result-dir", default="qemu-results")
    parser.add_argument("--profile", choices=("baseline", "injection"),
                        default="injection")
    parser.add_argument("--accelerator", choices=("auto", "kvm", "tcg"),
                        default="auto")
    parser.add_argument("--timeout", type=int, default=3600)

    args = parser.parse_args()

    if not shutil.which("docker"):
        raise SystemExit("docker is required")

    source = os.path.realpath(args.source_dir)
    if not os.path.isfile(os.path.join(source, "meson.build")):
        raise SystemExit(f"rasdaemon source checkout not found: {source}")

    owner = args.repository.split("/", 1)[0].lower()
    image = f"ghcr.io/{owner}/rasdaemon-ci:{args.arch}"
    log(f"Pulling image {image}")
    command(["docker", "pull", image])
    os.makedirs(args.result_dir, exist_ok=True)
    docker_args = ["docker", "run", "--rm"]
    host_arch = os.uname().machine
    compatible_architectures = {
        "x86_64": {"x86_64", "amd64"},
        "aarch64": {"aarch64", "arm64"},
    }
    kvm_arch_ok = host_arch in compatible_architectures.get(args.arch, {})
    kvm_device_ok = (os.path.exists("/dev/kvm") and
                     os.access("/dev/kvm", os.R_OK | os.W_OK))

    if kvm_arch_ok and kvm_device_ok:
        log("Passing /dev/kvm to the test container")
        docker_args.extend(("--device", "/dev/kvm"))
    elif not kvm_arch_ok:
        log(f"Host architecture is {host_arch}; {args.arch} cannot use KVM")
    else:
        log("/dev/kvm is unavailable; the test container will use TCG")
    docker_args.extend([
        "-v", f"{source}:/workspace:ro",
        "-v", f"{os.path.realpath(args.result_dir)}:/results",
        image, "run", "--arch", args.arch,
        "--accelerator", args.accelerator,
        "--profile", args.profile,
        "--timeout", str(args.timeout),
    ])

    log("Starting test container")
    command(docker_args)

    result_dir = os.path.realpath(args.result_dir)
    log(f"Test results are available in {result_dir}")

    for name in sorted(os.listdir(result_dir)):
        result = os.path.join(result_dir, name)
        if os.path.isfile(result):
            log(f"  {result}")


if __name__ == "__main__":
    main()
