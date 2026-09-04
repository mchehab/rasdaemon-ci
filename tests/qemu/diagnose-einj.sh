#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

result_dir=${1:-build/qemu-results/x86_64}
result_json=$result_dir/result.json
console_log=$result_dir/console.log
qemu_log=$result_dir/qemu.log
qemu_binary=${QEMU_SYSTEM_X86_64:-qemu-system-x86_64}

section()
{
	printf '\n===== %s =====\n' "$1"
}

section "QEMU version"
if command -v "$qemu_binary" >/dev/null 2>&1; then
	"$qemu_binary" --version | head -n 1
else
	printf '%s not found\n' "$qemu_binary"
fi

section "Q35 machine RAS options"
if command -v "$qemu_binary" >/dev/null 2>&1; then
	"$qemu_binary" -machine q35,help 2>&1 |
		grep -iE 'einj|ghes|apei|(^|[^[:alnum:]_])ras([^[:alnum:]_]|$)|acpi' || true
fi

section "QEMU RAS devices"
if command -v "$qemu_binary" >/dev/null 2>&1; then
	"$qemu_binary" -device help 2>&1 |
		grep -iE 'einj|ghes|apei|(^|[^[:alnum:]_])ras([^[:alnum:]_]|$)|erst' || true
fi

section "Structured result"
if test -f "$result_json"; then
	python3 - "$result_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)

print("architecture:", result.get("architecture"))
print("accelerator:", result.get("accelerator"))
print("profile:", result.get("profile"))
for test in result.get("tests", []):
    print("%s %-28s %s" % (
        test.get("status", "unknown"),
        test.get("name", "unnamed"),
        test.get("reason", ""),
    ))
    if test.get("status") != "passed" and test.get("evidence"):
        print(json.dumps(test["evidence"], indent=2, sort_keys=True))
PY
else
	printf '%s not found\n' "$result_json"
fi

section "Guest ACPI/APEI/EINJ messages"
if test -f "$console_log"; then
	grep -iE 'einj|apei|ghes|erst|hardware error|acpi.*error' \
		"$console_log" || true
else
	printf '%s not found\n' "$console_log"
fi

section "Guest agent tail"
if test -f "$console_log"; then
	tail -n 120 "$console_log"
fi

section "QEMU log"
if test -f "$qemu_log"; then
	tail -n 120 "$qemu_log"
else
	printf '%s not found\n' "$qemu_log"
fi
