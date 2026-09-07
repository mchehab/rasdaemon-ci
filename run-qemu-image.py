#!/usr/bin/env python3
"""Download the latest CI QEMU image artifact and run it locally."""

import argparse
import datetime
import os
import shutil
import subprocess
import sys
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
    parser.add_argument("-v", "--verbose-console", action="store_true",
                        help="show full guest serial output instead of lifecycle events only")
    parser.add_argument("--image-harness", action="store_true",
                        help="use the tests embedded in the image instead of this checkout")

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
    command(["docker", "image", "inspect", "--format",
             "Image ID={{.Id}}; repository digests={{json .RepoDigests}}", image])
    os.makedirs(args.result_dir, exist_ok=True)
    docker_args = ["docker", "run", "--rm"]
    local_harness = not args.image_harness
    if local_harness:
        harness = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tests", "qemu")
        entrypoint = os.path.join(harness, "oci", "rasdaemon-ci.py")
        if not os.path.isfile(entrypoint):
            raise SystemExit("Local harness missing; run from a complete checkout or use --image-harness")
        log("Using this checkout's test harness with the published guest and QEMU")
        docker_args.extend([
            "-v", f"{harness}:/opt/rasdaemon-ci/harness:ro",
            "--entrypoint", "python3",
        ])
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
        image,
    ])
    if local_harness:
        docker_args.append("/opt/rasdaemon-ci/harness/oci/rasdaemon-ci.py")
    docker_args.extend([
        "run", "--arch", args.arch,
        "--accelerator", args.accelerator,
        "--profile", args.profile,
        "--timeout", str(args.timeout),
    ])

    if args.verbose_console and local_harness:
        docker_args.append("--verbose-console")
    elif args.verbose_console:
        help_result = subprocess.run(
            ["docker", "run", "--rm", image, "run", "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            check=True,
        )
        if "--verbose-console" in help_result.stdout:
            docker_args.append("--verbose-console")
        else:
            log("This image predates --verbose-console; running with its default "
                "logging. Full serial output will be retained in console.log. "
                "Rebuild the image to enable live verbose console output.")
    log("Starting test container")
    try:
        command(docker_args)
        status = 0
    except subprocess.CalledProcessError as error:
        status = error.returncode
        log(f"Test container finished with exit status {status}; inspect the retained results")

    result_dir = os.path.realpath(args.result_dir)
    log(f"Test results are available in {result_dir}")

    for name in sorted(os.listdir(result_dir)):
        result = os.path.join(result_dir, name)
        if os.path.isfile(result):
            log(f"  {result}")
    if status:
        sys.exit(status)


if __name__ == "__main__":
    main()
