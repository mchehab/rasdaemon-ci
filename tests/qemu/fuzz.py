#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Seed the unmodified QEMU fuzzy generator and retain CPER before delivery."""

import argparse
import base64
import json
import os
import random
import sys


BOUNDARIES = (1, 7, 8, 31, 32, 63, 64, 71, 72, 79, 80, 95, 96,
              127, 128, 191, 192, 207, 208, 255, 256, 511, 512)


def case_parameters(index: int, families: list[str], mode: str) -> list[str]:
    """Exercise every CPER family, including both sides of common structure sizes."""
    arguments = ["fuzzy", "-c", "1", "-t", "5", "-T", families[index % len(families)]]

    if mode == "boundaries":
        size = BOUNDARIES[(index // len(families)) % len(BOUNDARIES)]
        arguments += ["--min-size", str(size), "--max-size", str(size)]

        if (index // len(families)) % 2 == 0:
            arguments.append("--zero")
    else:
        # The helper's maximum is exclusive. It treats a zero minimum as
        # unspecified, so the smallest supported CLI payload here is one byte.
        arguments += ["--min-size", "1", "--max-size", "513"]

        if mode == "zero":
            arguments.append("--zero")

    return arguments


def main() -> None:
    """Use QEMU's generator with a recording transport adapter, without source edits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("random", "zero", "boundaries"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--corpus", required=True)
    args = parser.parse_args()

    if not 1 <= args.count <= 10000:
        parser.error("--count must be between 1 and 10000")

    sys.path.insert(0, "/opt/qemu/libexec/rasdaemon")
    # Runtime imports use the helper version installed with this QEMU build.
    import fuzzy_error  # pylint: disable=C0415,E0401
    import qmp_helper  # pylint: disable=C0415,E0401

    class RecordingMonitor(qmp_helper.qmp):
        """Persist the pending CPER even when decoding or QMP delivery fails."""

        context: dict = {}

        def send_cper(self, notif_type: object, payload: bytes, **kwargs: object) -> str:
            gede = self.get_gede(notif_type, len(payload))
            gesb = self.get_gebs(len(payload))
            record = bytes(gesb) + bytes(gede) + bytes(payload)
            entry = dict(self.context, cper=base64.b64encode(record).decode("ascii"))

            with open(args.corpus, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

            # debug=True prints the standard GUID/GESB/GEDE/Payload format
            # accepted by ghes_inject raw, as well as the helper's decoder output.
            try:
                result = super().send_cper(notif_type, payload, **kwargs)

                if result != "OK":
                    raise RuntimeError(f"CPER delivery failed for case {self.context}")

                return result
            finally:
                self._close()

    fuzzy_error.qmp = RecordingMonitor
    generator_parser = argparse.ArgumentParser()
    generator = fuzzy_error.FuzzyError(generator_parser.add_subparsers())
    families = list(generator.types)
    random.seed(args.seed)
    print(f"FUZZ mode={args.mode} seed={args.seed} count={args.count}", flush=True)

    for index in range(args.count):
        arguments = case_parameters(index, families, args.mode)
        case = generator_parser.parse_args(arguments)
        case.host, case.port, case.debug = "127.0.0.1", args.port, True
        RecordingMonitor.context = {"index": index, "seed": args.seed,
                                    "mode": args.mode, "arguments": arguments}
        print(f"FUZZ case={index} arguments={arguments}", flush=True)
        # The upstream batch method returns None after success. Validate each
        # send above instead of interpreting that as a CLI failure or success.
        generator.send_cper(case)

    print(f"FUZZ completed={args.count}", flush=True)


if __name__ == "__main__":
    main()
