QEMU hardware-event coverage
============================

The injection profile tests the real guest kernel, rasdaemon trace consumers,
SQLite recording and ras-mc-ctl reports. The scenarios are defined in
``scenarios.json``. A required scenario that is missing, skipped or fails does
not satisfy the coverage contract.

Implemented scenarios
---------------------

===================== ========================================= ===========================
Scenario              Guest event                               Producer
===================== ========================================= ===========================
mce-hardware-first    mce:mce_record                            QEMU HMP corrected memory MCE
aer-native            ras:aer_event                             QEMU HMP BAD_DLLP
block-io-native       block:block_rq_error or block_rq_complete  QEMU blkdebug write EIO
ghes-memory           ras:mc_event                              Corrected memory CPER via GHES/EDAC
ghes-arm              ras:arm_event                             Corrected ARM processor CPER
ghes-pci-bus           ras:non_standard_event                    PCI/PCI-X bus CPER
ghes-unknown          ras:non_standard_event                    Unknown-GUID CPER
ghes-aer              ras:aer_event                             PCIe CPER with guest-discovered BDF
cxl-media             cxl:cxl_general_media                     Type 3 event mailbox
cxl-dram              cxl:cxl_dram                              Type 3 event mailbox
cxl-module            cxl:cxl_memory_module                     Type 3 event mailbox
cxl-poison            cxl:cxl_poison                            QMP poison plus guest list query
cxl-overflow          cxl:cxl_overflow                          Overflow while guest CPUs are stopped
cxl-aer-ce            cxl:cxl_aer_correctable_error              CXL correctable memory ECC
cxl-aer-ue            cxl:cxl_aer_uncorrectable_error            CXL uncorrectable memory parity
memory-failure        ras:memory_failure_event                  Guest hwpoison on a helper's page
memory-sigbus         signal:signal_generate                    Helper accesses its poisoned page
===================== ========================================= ===========================

These 17 scenarios target 15 distinct consumer paths (counting the two block
tracepoint alternatives as one path). ARM CPER decoding in an x86 VM tests the
firmware-record consumer, not ARM CPU hardware. Guest hwpoison tests kernel
memory-failure handling; it is distinct from a QEMU hardware-reported error.

Native CXL uses a dedicated 256 MiB volatile Type 3 device. Its memory is not
onlined as guest System RAM. The uncorrectable CXL case runs after the other
CXL cases because recovery can reset or detach the device. All virtio devices
have explicit PCI bus assignments: adding pxb-cxl changes implicit bus selection.

The CXL poison list is queried through
``/sys/bus/cxl/devices/mem*/trigger_poison_list``, as implemented by the
`kernel CXL memory driver <https://github.com/torvalds/linux/blob/master/drivers/cxl/mem.c>`_.
An empty poison-list trace does not count: the recorded DPA and length must match
the injected 64-byte poison range.

GHES uses the configured QEMU ``rasdaemon_tests`` branch and its
``ghes_inject.py`` helper, including raw replay for deterministic memory,
PCIe and unknown-GUID records. The producer interfaces were inspected at
QEMU commit ``577467776b06d66e7fbddf17d853bde0479a12a8``.
The image must install every helper import, including pci_bus_error.py,
fuzzy_error.py, raw_error.py and ghes_decode.py.

Evidence and failures
---------------------

Each added scenario starts a daemon with a fresh SQLite database. The guest
waits for the required tracepoint to be enabled before printing its readiness
marker. Only then does the host inject. The guest waits for matching committed
rows, requires clean daemon shutdown and runs the corresponding JSON report.

The JSON result retains rows (BLOBs as hex), daemon output, tracepoint identity,
QMP transcripts or exact helper commands, raw deterministic CPER and helper
debug output. Host injection evidence survives exceptions. Console, QEMU log,
JSON, results.log and JUnit remain the normal artifacts. The added scenarios
retain decoded database contents in JSON; they do not export the SQLite files.

Readiness and row waits are bounded. A host injection failure is retained while
the guest can continue to other scenarios. Missing drivers and disabled
tracepoints are failures, rather than capability skips, for this required image.

Kernel configuration
--------------------

The additional requirements are GHES-to-EDAC (``EDAC_GHES``), GHES PCIe AER
(``ACPI_APEI_PCIEAER``), and the CXL bus, PCI, ACPI, memory and region drivers.
CXL ACPI also requires NUMA/ACPI_NUMA. These are built in, and the build checks
that olddefconfig retained the required values. ``HWPOISON_INJECT=m`` remains
the disposable-page producer.

The guest boot command line enables ``ghes.edac_force_enable=1`` because QEMU
is outside the GHES EDAC physical-platform allowlist. The payload builds
rasdaemon with ``-Denable-arch=all`` so ARM and non-standard CPER consumers
can be exercised on the x86 guest.

Enabling physical chipset EDAC drivers does not create those controllers in
QEMU. The additional EDAC vendor drivers, scrub/repair/ECS controls, NVIDIA
GHES, CXL feature controls and persistence backends from a broad development
configuration therefore do not by themselves increase this test's event coverage.
Software PCIe AER injection is unnecessary for the existing native QEMU AER
producer. EINJ-specific additions should accompany actual EINJ scenarios.
RAS_CEC can consume corrected errors before normal consumers see them, so it
should be tested separately rather than enabled indiscriminately.

Linux and QEMU workflows still perform artifact checks on each push, allowing
the image workflow to wait for them. Compilation is forced only if their build
script or workflow changed across that push, or a matching upstream artifact
is missing. Daily source checks continue to reuse existing artifacts.

Runtime choices
---------------

The ordinary profile keeps deterministic coverage of every implemented event
source. Fixed post-injection sleeps for MCE, AER and block I/O have been replaced
by bounded polling, which returns as soon as a row is committed.

Recommended: keep this deterministic profile on image changes, and add a
separate weekly stress run with 1000 fuzzy GHES records. Treat that run as
robustness coverage: invalid records can legitimately produce no rasdaemon row.
Require the guest and daemon to remain responsive, retain each payload and
diagnostics, and follow stress with a deterministic record to prove continued
recording. The current helper has no seed option; a harness wrapper can seed
Python's random generator and invoke the unmodified script. Isolate stress in
its own disposable VM so a panic cannot discard deterministic test results.

Alternatives:

- Run a small fixed-seed fuzz batch daily and 1000 records weekly. This gives
  earlier feedback at some daily runtime cost.
- Divide fixed-seed CPER families into rotating daily batches. This caps each
  run's duration, but complete fuzz coverage then takes several days.
- Use only a curated deterministic replay corpus on routine runs and reserve
  random fuzzing for manual or weekly execution. Promote useful new failures
  into the corpus after triage.

No weekly schedule or unseeded fuzz pass is enabled by this change; these are
execution-policy alternatives for selection after deterministic live validation.

Remaining event sources
-----------------------

- CXL generic and memory-sparing: the selected QMP interface has no matching
  event-record producer. A general-media record is not a substitute.
- extlog_mem_event: requires the firmware extended-log protocol; no working
  producer has been established in the configured VM.
- devlink_health_report and net_dev_xmit_timeout: the configured devices have
  no deterministic producer. A netdevsim guest integration test is a separate
  option, but is not QEMU hardware-failure coverage.
- RISC-V reri_event: needs a RISC-V firmware/kernel/image and a working RERI
  producer; the current CI image and matrix are x86 only.
- ERST persistence and vendor-specific decoder tables need dedicated reboot
  scenarios or validated vendor CPER fixtures. Generic non-standard event
  coverage does not imply every vendor decoder has been tested.

Live validation
---------------

The expanded topology has been launched locally and reached the artifact
kernel using the existing x86 image under emulation on an ARM host. The run
timed out during boot after 480 host seconds (about 33 guest seconds), before
the agent started. Results are in ``tmp/vm-coverage/``. This is not a successful
end-to-end run of the new cases. The existing image lacks
the newly requested kernel configuration and boot parameter; rebuild it before
using the expanded coverage contract as a release gate.

The focused Python tests check CPER headers, safe memory validation bits,
CXL overflow recovery, explicit PCI assignments and rejection of false-positive
poison, memory-failure and SIGBUS records. Unit tests do not establish hardware
coverage. Retain the first successful rebuilt-image run as live evidence.
