# QEMU hardware-event coverage

This matrix is the implementation contract for the QEMU regression lab.  A
test is complete only when a real guest kernel emits the named trace event,
the real rasdaemon/libtraceevent consumer handles it, and the expected SQLite
row and `ras-mc-ctl` report are verified.  Writing directly to tracefs or to
the database is not hardware coverage.

## Handoff state (2026-09-03)

The worktree was clean at commit `bd2ffd3` when this handoff was written.
Milestone commits, oldest first, are:

| Commit | Result |
| --- | --- |
| `b24f46f` | Initial QEMU regression lab |
| `35ea779` | Verify MCE SQLite data with `ras-mc-ctl` |
| `5d45713` | Strip ANSI/control sequences from retained console text |
| `7de1e29` | Define the expanded hardware-event coverage plan |
| `f12f3ae` | Implement native PCIe AER injection |
| `478f356` | Avoid Meson failures from host/guest clock skew |
| `de288c0` | Record successful native AER verification |
| `bd2ffd3` | Implement disposable `blkdebug` block-I/O injection |

Verified on Fedora 44 bare metal with QEMU 10.2.2 and KVM:

- corrected x86 memory MCE: QEMU HMP `mce`, kernel `mce:mce_record`, one
  `mce_record` row, and decoded `ras-mc-ctl` JSON;
- corrected PCIe AER `BAD_DLLP`: dedicated Virtio RNG endpoint, kernel
  `ras:aer_event`, one `aer_event` row, and decoded `ras-mc-ctl` JSON;
- all 17 host-runner unit tests passed after the block-I/O implementation.

The first block-I/O live run reached the test but exposed both block tracepoint
formats: Fedora's kernel provided `block_rq_error` while rasdaemon selected and
enabled `block_rq_complete`.  The test incorrectly assumed the first existing
format was active.  The current fix selects whichever supported tracepoint the
running rasdaemon instance actually enabled.  Rebuild the x86_64 guest image
before retesting because `tests/qemu/guest/agent.py` changed.  The next
immediate action is:

1. rebuild the Fedora 43/44 x86_64 image;
2. run `make qemu-test` on the Fedora 44/QEMU 10.2.2 KVM host;
3. inspect `qemu-output/x86_64/result.json` and `console.log`;
4. require MCE, AER, `block-io-native`, and `block-database-report` to pass;
5. diagnose and fix the block scenario if it fails, then record live
   verification here and commit that milestone.

After block I/O passes, proceed to guest memory failure, then native CXL.
GHES/CPER remains a later, separate milestone.

## Constraints and permissions

- Work only in `/home/mchehab/rasdaemon-ci`; never modify
  `/home/mchehab/rasdaemon`.
- Do not inject errors into host hardware or weaken host security policy.
- KVM and workspace-local QEMU runs, images, temporary data, network research,
  and milestone commits in this repository are authorized.
- Do not install packages.  If something is missing, tell the user exactly
  what to install and wait for them to do it.
- Fedora 41 is EOL.  Use Fedora 43 or Fedora 44 images only.
- Guest-agent changes require rebuilding the image because the systemd agent
  is baked into the qcow2 image.
- Tests must run real rasdaemon and consume real guest-kernel trace events via
  libtraceevent.  Unit-only, direct tracefs writes, and direct database writes
  do not satisfy a hardware milestone.
- The Python `qmp` package required by `scripts/ghes_inject.py` is installed.
- For GHES/CPER, exclusively use
  `https://gitlab.com/mchehab_kernel/qemu.git`, branch
  `pcie-bus-error-inject-v4`, and its `scripts/ghes_inject.py`.  A distribution
  QEMU implementation is not a substitute.
- Keep retained `console.log` text free of ANSI escape/control sequences and
  retain QEMU log, QMP transcript, exact injection arguments, JSON/JUnit,
  SQLite counts, and `ras-mc-ctl` output.

## Native QEMU and guest-kernel mechanisms

| Milestone | Guest trace event | SQLite table | Producer | Architectures | State |
| --- | --- | --- | --- | --- | --- |
| Machine check | `mce:mce_record` | `mce_record` | QEMU HMP `mce` | x86_64 | Implemented: corrected, recoverable memory MCE |
| PCIe AER | `ras:aer_event` | `aer_event` | QEMU HMP `pcie_aer_inject_error` on an AER-capable endpoint | x86_64, aarch64 | x86_64 correctable path verified with QEMU 10.2.2/KVM |
| Block I/O | `block:block_rq_error`, or failed `block_rq_complete` | `disk_errors` | disposable `blkdebug` block device | x86_64, aarch64 | Implemented; live verification pending |
| Memory failure | `ras:memory_failure_event` | `memory_failure_event` | guest `MADV_HWPOISON`/hwpoison debugfs on a disposable page | x86_64, aarch64 | Planned; capability-gated by guest kernel |
| CXL poison | `cxl:cxl_poison` | `cxl_poison_event` | QEMU `cxl-inject-poison` with an emulated CXL Type 3 device | x86_64 | Planned |
| CXL AER UE/CE | `cxl:cxl_aer_uncorrectable_error`, `cxl:cxl_aer_correctable_error` | `cxl_aer_ue_event`, `cxl_aer_ce_event` | QEMU CXL error QMP commands | x86_64 | Planned |
| CXL media events | `cxl:cxl_general_media`, `cxl:cxl_dram`, `cxl:cxl_memory_module` | matching `cxl_*_event` tables | QEMU CXL event QMP commands | x86_64 | Planned |
| ERST persistence | pstore ERST record consumed at startup | `mce_record` with ERST provenance | QEMU `acpi-erst` device plus guest pstore | x86_64 | Research milestone; capability-gated |

CXL generic, overflow, and memory-sparing handlers are retained in the
coverage inventory.  They will be enabled only when the selected QEMU/kernel
combination has a real producer; another CXL event must not be substituted for
them.

## Firmware-first GHES/CPER mechanisms

All GHES/CPER tests use commit-compatible builds of
`https://gitlab.com/mchehab_kernel/qemu.git`, branch
`pcie-bus-error-inject-v4`, and its `scripts/ghes_inject.py`.  The runner must
record the QEMU version and source revision.  A similarly named command from a
distribution QEMU does not satisfy this requirement.

| CPER input | Expected guest path | rasdaemon evidence | Architectures |
| --- | --- | --- | --- |
| Platform memory v1/v2 | GHES to EDAC | `ras:mc_event`, `mc_event` row | x86_64, aarch64 |
| PCIe | GHES AER | `ras:aer_event`, `aer_event` row | x86_64, aarch64 |
| Processor generic/x86/ARM | GHES processor tracepoint | matching x86 or ARM record where the kernel exposes one | architecture-specific |
| PCI/PCI-X bus and device | GHES CPER | supported kernel tracepoint and database row, otherwise an explicit capability skip | x86_64, aarch64 |
| CXL protocol and event records | GHES/CXL | matching `cxl_*` event table | x86_64, aarch64 where supported |
| Firmware, DMA, CCIX and raw/fuzzy records | GHES non-standard | `ras:non_standard_event` and applicable decoder table | primarily aarch64 |

The branch's fuzzy generator is useful for decoder breadth, but randomized
records are supplemental.  Every required scenario also has a deterministic
seed and stores the exact command/CPER payload in the result artifacts.

## Capability-gated or currently unavailable producers

| Handler | Limitation and policy |
| --- | --- |
| `ras:extlog_mem_event` / `extlog_event` | Requires x86 firmware extended error logging.  No native QEMU producer is currently identified; cover through a genuine GHES/firmware path only if the guest emits this tracepoint. |
| `devlink:devlink_health_report` / `devlink_event` | QEMU device errors do not normally invoke a devlink health reporter.  A guest `netdevsim` producer may be added as a kernel-device integration test and must be labeled as such. |
| `ras:arm_event` / `arm_event` | Requires ARM RAS records exposed by the guest kernel.  Use deterministic ARM processor GHES injection from the mandated branch. |
| `ras:non_standard_event` and vendor tables | Use the mandated branch's raw/fuzzy CPER support with deterministic payloads; report unsupported vendor GUIDs as skips. |
| `ras:reri_event` / `reri_event` | RISC-V RERI needs a suitable QEMU, firmware, kernel, and image.  The existing RISC-V descriptor continues to skip honestly until that stack exists. |
| `ras:signal_event` / `signal_event` | A process signal is not a hardware-error injection mechanism and is outside this hardware matrix. |

## Execution order

1. Preserve the implemented safe x86 MCE case and add more recoverable MCE
   variants only when they cannot panic the guest.
2. Generalize scenario coordination and common evidence checks.
3. Add native PCIe AER and block-I/O scenarios.
4. Add guest memory-failure and native CXL scenarios.
5. Integrate deterministic GHES/CPER scenarios using the mandated QEMU branch.
6. Add ERST and architecture-specific aarch64 coverage, then document honest
   skips for remaining kernel/QEMU gaps.

Each scenario retains a sanitized serial console, QEMU log, QMP transcript,
exact injection parameters, JSON/JUnit result, database counts, and the final
`ras-mc-ctl` output.
