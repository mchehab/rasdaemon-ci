#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inject x86 MCE and PCIe AER errors through QMP's HMP wrapper.

These are the host-originated error sources used by the QEMU test harness that
are separate from the GHES CPER helpers.  QEMU must expose a QMP listener, for
example ``-qmp tcp:127.0.0.1:4445,server=on,wait=off``.
"""

import argparse
import json
import os
import random
import secrets
import socket
from typing import Any


AER_ERRORS = (
    "DLP", "SDN", "POISON_TLP", "FCP", "COMP_TIME", "COMP_ABORT",
    "UNX_COMP", "RX_OVER", "MALF_TLP", "ECRC", "UNSUP", "ACSV",
    "INTN", "MCBTLP", "ATOP_EBLOCKED", "TLP_PRF_BLOCKED", "RCVR",
    "BAD_TLP", "BAD_DLLP", "REP_ROLL", "REP_TIMER", "ADV_NONFATAL",
    "INTERNAL", "HL_OVERFLOW",
)
SAFE_AER_ERRORS = (
    "RCVR", "BAD_TLP", "BAD_DLLP", "REP_ROLL", "REP_TIMER",
    "ADV_NONFATAL", "INTERNAL", "HL_OVERFLOW",
)
# A valid, enabled corrected MCE with valid address and misc fields. The
# random MCACOD lives in bits 15:0; all severity bits remain clear. QEMU only
# raises CPU_INTERRUPT_MCE for an uncorrected (UC) error.
MCE_SAFE_STATUS_BASE = 0x9C00000000000000
MCE_SAFE_STATUS_CODE_MASK = 0xFFFF
MCE_LOW_RAM_START = 0x100000
MCE_LOW_RAM_END = 0xC0000000


class QmpClient:
    """Minimal QMP client for one local injection request."""

    def __init__(self, host: str, port: int, timeout: float):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket: socket.socket | None = None
        self.stream = None
        self.events: list[dict[str, Any]] = []

    def __enter__(self):
        self.socket = socket.create_connection((self.host, self.port),
                                               self.timeout)
        self.stream = self.socket.makefile("rwb", buffering=0)
        greeting = self.receive()
        if "QMP" not in greeting:
            raise RuntimeError("QMP endpoint returned no greeting")
        self.execute("qmp_capabilities")

        return self

    def __exit__(self, _kind, _value, _traceback):
        if self.stream:
            self.stream.close()
        if self.socket:
            self.socket.close()

    def receive(self) -> dict[str, Any]:
        """Read one non-event QMP response."""
        assert self.stream is not None
        while True:
            line = self.stream.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            message = json.loads(line)
            if "event" in message:
                self.events.append(message)
            else:
                return message

    def execute(self, command: str, arguments: dict[str, Any] | None = None) -> Any:
        """Execute one QMP command and return its result."""
        assert self.stream is not None
        request: dict[str, Any] = {"execute": command}
        if arguments is not None:
            request["arguments"] = arguments
        self.stream.write((json.dumps(request) + "\r\n").encode())
        response = self.receive()
        if "error" in response:
            raise RuntimeError(f"QMP {command} failed: {response['error']}")

        return response.get("return")

    def hmp(self, command: str) -> str:
        """Run one HMP command using QMP's human-monitor-command wrapper."""
        result = self.execute("human-monitor-command", {"command-line": command})

        return result if isinstance(result, str) else str(result)


def number(value: str) -> int:
    """Parse decimal or conventional ``0x``-prefixed integer input."""
    return int(value, 0)


def create_parser() -> argparse.ArgumentParser:
    """Create the command line interface."""
    formatter = argparse.ArgumentDefaultsHelpFormatter
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=formatter)
    parser.add_argument("--host", default="127.0.0.1", help="QMP host")
    parser.add_argument("--port", type=int, default=4445, help="QMP TCP port")
    parser.add_argument("--timeout", type=float, default=30,
                        help="QMP connection timeout in seconds")
    commands = parser.add_subparsers(dest="kind", required=True)

    aer = commands.add_parser("aer", help="inject one PCIe AER error",
                              formatter_class=formatter)
    aer.add_argument("--device", default="ras-aer",
                     help="AER-capable qdev ID")
    aer.add_argument("--error", default="BAD_DLLP", choices=AER_ERRORS)
    aer.add_argument("--advisory-non-fatal", action="store_true",
                     help="mark an uncorrectable error advisory non-fatal")

    mce = commands.add_parser("mce", help="inject one x86 machine check",
                              formatter_class=formatter)
    mce.add_argument("--cpu", type=number, default=0, help="guest CPU index")
    mce.add_argument("--bank", type=number, default=0, help="MCE bank number")
    mce.add_argument("--status", type=number, required=True)
    mce.add_argument("--mcg-status", type=number, default=0,
                     help="machine-check global status")
    mce.add_argument("--address", type=number, required=True)
    mce.add_argument("--misc", type=number, default=0, help="MCE misc value")
    mce.add_argument("--broadcast", action="store_true",
                     help="broadcast the MCE to all guest CPUs")

    fuzz_parser = commands.add_parser("fuzz", help="fuzz safe AER and MCE fields",
                                      formatter_class=formatter)
    fuzz_parser.add_argument("--scope", choices=("both", "aer", "mce"),
                             default="both", help="error sources to generate")
    fuzz_parser.add_argument("--device", default="ras-aer",
                             help="AER-capable qdev ID")
    fuzz_parser.add_argument("--error", default="random",
                             choices=("random",) + AER_ERRORS,
                             help="AER status, or a random status per payload")
    fuzz_parser.add_argument("--fatal", action="store_true",
                             help="include uncorrectable AER statuses when randomizing")
    fuzz_parser.add_argument("--mce-address", type=number,
                             help="MCE physical address; defaults to QMP-discovered RAM")
    fuzz_parser.add_argument("--mce-cpu", type=number, default=0,
                             help="guest CPU receiving generated MCEs")
    fuzz_parser.add_argument("--mce-bank", type=number, default=0,
                             help="MCE bank receiving generated MCEs")
    fuzz_parser.add_argument("--count", type=int, default=1000,
                             help="number of generated cases per selected source")
    fuzz_parser.add_argument("--mode", choices=("random", "zero"), default="random",
                             help="TLP dword fill mode")
    fuzz_parser.add_argument("--seed", type=number, default=argparse.SUPPRESS,
                             help="random generator seed")
    fuzz_parser.add_argument("--result-dir", default="hmp-fuzz-results",
                             help="directory for corpus, outcomes, and summary")

    return parser


def aer_command(device: str, error: str, advisory_non_fatal: bool,
                dwords: list[int] | None = None) -> str:
    """Build one PCIe AER injection command with an optional TLP payload."""
    prefix = "-a " if advisory_non_fatal else ""
    payload = "" if not dwords else " " + " ".join(
        f"{value:#x}" for value in dwords)

    return f"pcie_aer_inject_error {prefix}{device} {error}{payload}"


# The HMP ABI has seven positional fields, which is intentionally mirrored.
# pylint: disable=too-many-arguments
def mce_command(cpu: int, bank: int, status: int, mcg_status: int,
                address: int, misc: int, broadcast: bool) -> str:
    """Build one x86 machine-check injection command."""
    prefix = "-b " if broadcast else ""

    return (f"mce {prefix}{cpu} {bank} {status:#x} {mcg_status:#x} "
            f"{address:#x} {misc:#x}")


# pylint: enable=too-many-arguments


def hmp_command(args: argparse.Namespace) -> str:
    """Return the restricted HMP command corresponding to user input."""
    if args.kind == "aer":
        return aer_command(args.device, args.error, args.advisory_non_fatal)

    return mce_command(args.cpu, args.bank, args.status, args.mcg_status,
                       args.address, args.misc, args.broadcast)


def write_record(stream, record: dict[str, Any]) -> None:
    """Persist one JSONL record before continuing to the next injection."""
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def fuzz_dwords(generator: random.Random, mode: str) -> list[int]:
    """Create a variable-size HMP TLP header/prefix payload of up to 8 dwords."""
    count = generator.randrange(1, 9)
    if mode == "zero":
        return [0] * count

    return [generator.getrandbits(32) for _ in range(count)]


def fuzz_mce(generator: random.Random, mode: str) -> tuple[int, int]:
    """Create a corrected MCE status in QEMU's non-interrupting range."""
    if mode == "zero":
        return MCE_SAFE_STATUS_BASE, 0

    return (MCE_SAFE_STATUS_BASE |
            generator.getrandbits(MCE_SAFE_STATUS_CODE_MASK.bit_length()),
            generator.getrandbits(64))


def mce_preflight(client: QmpClient, cpu_index: int) -> dict[str, Any]:
    """Discover the selected online CPU through QMP before sending MCEs."""
    cpus = client.execute("query-cpus-fast")
    for cpu in cpus:
        if cpu.get("cpu-index") == cpu_index:
            return {"cpu_index": cpu_index, "qom_path": cpu.get("qom-path"),
                    "thread_id": cpu.get("thread-id")}

    available = sorted(cpu.get("cpu-index") for cpu in cpus
                       if "cpu-index" in cpu)
    raise ValueError(f"--mce-cpu {cpu_index} is not online; "
                     f"QMP reports CPUs {available}")


def mce_address_range(client: QmpClient) -> tuple[int, int]:
    """Return a page-aligned low-RAM range discovered through QMP."""
    memory = client.execute("query-memory-size-summary")
    base_memory = memory.get("base-memory", 0)
    end = min(base_memory, MCE_LOW_RAM_END) & ~0xfff
    if end <= MCE_LOW_RAM_START:
        raise ValueError("QMP reports insufficient base memory for safe MCE "
                         "addresses")

    return MCE_LOW_RAM_START, end


def fuzz_mce_address(generator: random.Random, mode: str,
                     address_range: tuple[int, int]) -> int:
    """Select a page-aligned address from QMP-discovered guest low RAM."""
    start, end = address_range
    if mode == "zero":
        return start

    return generator.randrange(start, end, 0x1000)


def mce_event(client: QmpClient, event_start: int) -> dict[str, Any] | None:
    """Return QEMU's memory-failure result emitted for an MCE injection."""
    for event in client.events[event_start:]:
        if event.get("event") == "MEMORY_FAILURE":
            return event
    return None


def fuzz_one(client: QmpClient, corpus, outcomes, package: dict[str, Any]) -> bool:
    """Persist, submit, and record one HMP injection package."""
    write_record(corpus, package)
    event_start = len(client.events)
    try:
        response = client.hmp(package["command"])
        outcome = {"index": package["index"], "kind": package["kind"],
                   "status": "sent", "response": response}
        if package["kind"] == "mce":
            outcome["qmp_memory_failure"] = mce_event(client, event_start)
        sent = True
    except RuntimeError as error:
        outcome = {"index": package["index"], "kind": package["kind"],
                   "status": "failed", "error": str(error)}
        sent = False
    write_record(outcomes, outcome)

    return sent


def fuzz(args: argparse.Namespace) -> int:  # pylint: disable=too-many-locals
    """Inject and retain a reproducible sequence of safe AER and MCE payloads."""
    if not 1 <= args.count <= 10000:
        raise ValueError("--count must be between 1 and 10000")
    os.makedirs(args.result_dir, exist_ok=True)
    corpus_path = os.path.join(args.result_dir, "hmp-fuzz-corpus.jsonl")
    outcomes_path = os.path.join(args.result_dir, "hmp-fuzz-results.jsonl")
    seed = getattr(args, "seed", None)
    if seed is None:
        seed = secrets.randbits(64)
    generator = random.Random(seed)
    counts = {"aer_sent": 0, "aer_failed": 0, "mce_sent": 0, "mce_failed": 0}

    with open(corpus_path, "w", encoding="utf-8") as corpus, \
            open(outcomes_path, "w", encoding="utf-8") as outcomes, \
            QmpClient(args.host, args.port, args.timeout) as client:
        mce_target = None
        mce_range = None
        if args.scope in ("both", "mce"):
            mce_target = mce_preflight(client, args.mce_cpu)
            if args.mce_address is None:
                mce_range = mce_address_range(client)
                mce_target["address_range"] = {
                    "start": f"{mce_range[0]:#x}", "end": f"{mce_range[1]:#x}",
                }
                mce_target["address_source"] = "qmp-low-ram"
            else:
                mce_target["address"] = f"{args.mce_address:#x}"
                mce_target["address_source"] = "command-line"
        for index in range(args.count):
            if args.scope in ("both", "aer"):
                dwords = fuzz_dwords(generator, args.mode)
                choices = AER_ERRORS if args.fatal else SAFE_AER_ERRORS
                error = (generator.choice(choices) if args.error == "random"
                         else args.error)
                aer_package = {
                    "index": index,
                    "kind": "aer",
                    "seed": seed,
                    "mode": args.mode,
                    "device": args.device,
                    "error": error,
                    "dwords": dwords,
                    "command": aer_command(args.device, error, False, dwords),
                }
                if fuzz_one(client, corpus, outcomes, aer_package):
                    counts["aer_sent"] += 1
                else:
                    counts["aer_failed"] += 1

            if args.scope in ("both", "mce"):
                status, misc = fuzz_mce(generator, args.mode)
                address = (args.mce_address if args.mce_address is not None
                           else fuzz_mce_address(generator, args.mode, mce_range))
                mce_package = {
                    "index": index,
                    "kind": "mce",
                    "seed": seed,
                    "mode": args.mode,
                    "cpu": args.mce_cpu,
                    "bank": args.mce_bank,
                    "status": f"{status:#x}",
                    "mcg_status": "0x0",
                    "address": f"{address:#x}",
                    "misc": f"{misc:#x}",
                    "command": mce_command(args.mce_cpu, args.mce_bank, status,
                                           0, address, misc, False),
                }
                if fuzz_one(client, corpus, outcomes, mce_package):
                    counts["mce_sent"] += 1
                else:
                    counts["mce_failed"] += 1

    summary = {
        "count": args.count,
        "sent": counts["aer_sent"] + counts["mce_sent"],
        "failed": counts["aer_failed"] + counts["mce_failed"],
        **counts,
        "seed": seed,
        "mode": args.mode,
        "scope": args.scope,
        "device": args.device,
        "error": args.error,
        "fatal": args.fatal,
        "mce_target": mce_target,
        "mce_safe_status": {
            "base": f"{MCE_SAFE_STATUS_BASE:#x}",
            "random_mask": f"{MCE_SAFE_STATUS_CODE_MASK:#x}",
        },
        "corpus": corpus_path,
        "results": outcomes_path,
    }
    summary_path = os.path.join(args.result_dir, "hmp-fuzz-summary.json")
    with open(summary_path, "w", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")

    print(json.dumps(summary, sort_keys=True))

    return 1 if summary["failed"] else 0


def main() -> int:
    """Inject the requested error and print QEMU's monitor response."""
    args = create_parser().parse_args()
    if args.kind == "fuzz":
        try:
            return fuzz(args)
        except ValueError as error:
            create_parser().error(str(error))
    command = hmp_command(args)
    with QmpClient(args.host, args.port, args.timeout) as client:
        output = client.hmp(command)
    print(output.rstrip())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
