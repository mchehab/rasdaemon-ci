rasdaemon-ci
============

Independent continuous-integration infrastructure for `rasdaemon
<https://github.com/mchehab/rasdaemon>`_. It builds current Linux and QEMU
sources, exercises rasdaemon in isolated virtual machines, and publishes
results intended for human review.

Latest reports
--------------

* `Daily functional-test report
  <https://mchehab.github.io/rasdaemon-ci/daily/>`_

Each report summarizes implemented feature results and reports x86_64 and
ARM64 VM infrastructure failures separately. It links to the GitHub Actions
run and provides detailed per-component evidence and downloadable
machine-readable files.

Functional feature implementation
---------------------------------

This is the implementation inventory, not a PASS/FAIL report. New checks in
this branch still need validation on this machine; in particular, the paired
x86_64/aarch64 daily run and its runtime balance have not been verified.
``Implemented`` means an injection or consumer check and its assertions exist.
``Partial`` means some paths exist but identified event families or checks
remain missing. ``Not implemented`` means no functional check exists yet.
``Not possible`` is scoped to the current two-architecture hardware setup,
not a claim that a feature can never be tested.

The daily report derives its feature names from the tested rasdaemon source.
Unsupported platform features and unimplemented checks are N/A, not FAIL, and
do not contribute to regression badges. Detailed reports list them separately
under ``Features not tested``, including missing portions of partial features.
Missing results, skipped, failed, and duplicate checks whose implementation
exists still do not count as PASS. An absent result is not automatically
classified as an implementation gap.
Compilation flags and other internal checks remain separate from feature
coverage. Each feature has one owning architecture; consumer assertions reuse
events where practical, without repeating the feature verdict on the other
architecture. The runtime ownership/check mapping is in
``tests/qemu/features.py``. Keep this table updated when adding checks.

.. list-table:: Current implementation status
   :header-rows: 1
   :widths: 22 12 18 48

   * - Feature
     - Owner
     - Status
     - Implemented check / remaining work
   * - abrt-report
     - aarch64
     - Implemented
     - Receive and inspect the ABRT socket report for an injected memory failure.
   * - aer
     - x86_64
     - Implemented
     - Native PCIe and firmware-first GHES delivery, matching recorded AER data.
   * - amp-ns-decode
     - aarch64
     - Partial
     - Ampere payload 0 fixture and decoded fields; other payload formats remain.
   * - ampere-oem-sel
     - aarch64
     - Implemented
     - Separate consumer assertion on the emulated BMC's type 0xc0 SEL record.
   * - arm
     - aarch64
     - Implemented
     - GHES ARM processor CPER and matching processor identification.
   * - arm-vendor-data (internal)
     - aarch64
     - Implemented
     - ARM CPER vendor bytes retained in the database and decoded in output.
   * - bmc-generic
     - x86_64
     - Implemented
     - Opt-in generic type 0x02 SEL consumer; verify BDF and severity bytes.
   * - cpu-fault-isolation
     - aarch64
     - Implemented
     - Verify CPU 1 is offlined after the ARM event; restore its initial state.
   * - cxl
     - x86_64
     - Partial
     - Media, DRAM, module, poison, overflow, AER and memory-sparing checks
       exist. Sparing uses a real mailbox resource query. A generic event
       producer remains to be implemented and is listed separately as N/A.
   * - db (internal)
     - aarch64
     - Implemented
     - Installed database-report CLI must return the injected record's fields.
   * - devlink
     - aarch64
     - Implemented
     - netdevsim health report and a software device's real TX watchdog timeout.
   * - diskerror
     - aarch64
     - Implemented
     - QEMU blkdebug I/O failure and matching block error record.
   * - erst
     - x86_64
     - Implemented
     - Load the x86 MCE ERST submodule, write/read a real ERST CPER, consume
       both pstore layouts with legacy-first selection, and verify deletion
       survives remount. Power-cycle persistence is not checked.
   * - extlog
     - x86_64
     - Implemented
     - Guest software producer emits the kernel EXTLOG tracepoint; verify
       decoded fields. Physical firmware EXTLOG delivery is not checked.
   * - hisi-ns-decode
     - aarch64
     - Implemented
     - All four registered formats: common, HIP08 OEM type 1, OEM type 2,
       and PCIe local. Batched fixtures cover every named module/submodule,
       known and unknown severity, each validity bit, BDF and register dumps.
       Execution and malformed-input coverage remain pending.
   * - ipmi-bmc (internal)
     - x86_64
     - Implemented
     - Shared IPMI transport exercised by the generic BMC consumer assertion.
   * - jaguar-ns-decode
     - aarch64
     - Partial
     - Jaguar payload 0 fixture; additional payload layouts remain.
   * - mc-event (always available)
     - aarch64
     - Implemented
     - GHES memory CPER and recorded memory-controller event.
   * - mce
     - x86_64
     - Implemented
     - Hardware-first machine-check injection and decoded event assertions.
   * - memory-ce-pfa
     - aarch64
     - Implemented
     - Corrected-error threshold and successful isolation of a disposable page.
   * - memory-failure
     - aarch64
     - Implemented
     - Poison a disposable helper's page and match the reported PFN.
   * - memory-row-ce-pfa
     - aarch64
     - Implemented
     - Row threshold and isolation of the disposable page identified by CPER.
   * - mysql
     - x86_64
     - Implemented
     - Real MariaDB service, recording backend and installed CLI readback.
   * - non-standard (internal)
     - aarch64
     - Implemented
     - PCI bus and unknown-GUID CPER injections require exact GUID and payload
       bytes. PCI bus delivery also exercises explicit zero-valued fields.
   * - nvidia-ns-decode
     - aarch64
     - Partial
     - NVIDIA non-standard fixture and decoded fields; broader format coverage
       remains to be audited.
   * - openbmc-unified-sel
     - aarch64
     - Implemented
     - Separate assertion on the Facebook/Meta type 0xfb SEL consumer record.
   * - page-isolation (internal)
     - aarch64
     - Implemented
     - Shared isolation mechanism checked by page and row PFA consumers.
   * - pcie-edpc
     - x86_64
     - Not implemented
     - Needs a DPC-capable emulated port and assertions on its control register;
       the current QEMU test topology does not provide that capability (N/A).
   * - poison-page-stat (internal)
     - aarch64
     - Implemented
     - Memory-failure injection must produce the configured threshold warning.
   * - postgresql
     - x86_64
     - Implemented
     - Real PostgreSQL service, recording backend and installed CLI readback.
   * - reri
     - Unavailable
     - Not possible
     - Not possible with the current x86_64/aarch64 stack. Experimental RISC-V
       support exists elsewhere, but adding it is explicitly out of scope.
       The aarch64 report retains this unsupported feature as N/A.
   * - signal
     - aarch64
     - Implemented
     - A disposable process accesses its poisoned page to provoke SIGBUS.
   * - sqlite3
     - aarch64
     - Implemented
     - Separate assertion that the injected memory-failure row was committed.
   * - trigger (internal)
     - aarch64
     - Implemented
     - Execute a real trigger and check its PFN and action-result environment.
   * - yitian-ns-decode
     - aarch64
     - Partial
     - Yitian DDR fixture and register dump; other event types remain.

Except for the expanded HiSilicon valid-format corpus, the vendor fixtures
above are representative checks, not exhaustive decoder
coverage. A feature PASS currently requires the checks listed in the ownership
mapping; it must not be read as proof that every subtype or malformed input was
tested. Expand those required checks along with the corresponding scenarios.

N/A checks not implemented
--------------------------

These are deliberate implementation gaps, shown as N/A in the detailed daily
report's ``Features not tested`` table. They are not skipped regression tests:
there is no runnable producer or emulated device for them yet, so they are
excluded from the PASS/FAIL badge totals.

.. list-table:: Current N/A implementation gaps
   :header-rows: 1
   :widths: 24 14 22 40

   * - Feature / check
     - Owner
     - Status
     - Reason
   * - cxl / cxl-generic
     - x86_64
     - Not implemented (N/A)
     - The harness has no producer for a generic CXL event. The other CXL
       event families are implemented and continue to be regression tested.
   * - pcie-edpc
     - x86_64
     - Not implemented (N/A)
     - QEMU's current test topology has no DPC-capable PCIe port. The retained
       build-flag check is N/A for this reason; it does not fail daily badges.
   * - reri / reri-event
     - Unavailable
     - Not implemented (N/A, out of scope)
     - The x86_64/aarch64 test setup has no RERI event producer. RISC-V
       emulation support is intentionally not added by this work.

A manually requested ``--dry-run`` also records the guest execution as N/A,
because no VM is started by design. Daily runs do start the guest, so that
manual-only marker is not part of their feature coverage.

ERST scope
~~~~~~~~~~

On current x86 Linux, the MCE core serializes a machine check as a CPER record
and writes it to firmware ERST storage. At a subsequent pstore scan (normally
on boot or when pstore is mounted), the ERST pstore backend exposes that record
as ``/sys/fs/pstore/mce-erst-<record-id>``. The kernel creates records at the
pstore mount root; it does not create ``/sys/fs/pstore/erst`` itself.

The ERST reader is an x86 MCE submodule. It first checks for an accessible
historical ``/sys/fs/pstore/erst`` mount, then falls back to the current flat
``/sys/fs/pstore`` mount. This order matters: the legacy mount can coexist
with an accessible parent directory, which otherwise would hide its records.
The two locations do not need to exist together. The functional test uses
``erst_dbg`` to write a CPER MCE record, then mounts pstore in each supported
layout to exercise real persistence, module initialization, discovery, decode,
and deletion. It also proves the precedence rule by leaving a record at the
flat root while the legacy directory is present: the record is decoded only
when the flat layout is selected.
It intentionally does not inject a fatal MCE and reboot the VM; that additional
MCE-core-to-ERST lifecycle test remains future work.

RERI limitation
~~~~~~~~~~~~~~~

A RISC-V CPU emulation alone does not supply a RERI error source. An
`experimental RAS stack
<https://lists.infradead.org/pipermail/linux-riscv/2026-January/083224.html>`_
documents RERI emulation using QEMU together with OpenSBI, EDK2 and Linux
changes. That stack delivers errors through GHES/CPER; rasdaemon's ``reri``
decoder expects the separate ``ras:reri_event`` tracepoint. Their connection
would have to be verified or implemented before claiming RERI decoder coverage.
The inspected local QEMU and Linux trees contain no RERI producer. A third VM,
experimental firmware stack, or software RERI producer is not being implemented
in this work, at the user's request.

TaiShan reference platform
--------------------------

Read-only discovery on the development host identifies a Huawei TaiShan 2280
V2, board BC82AMDD, with two 64-core Kunpeng-920 processors and four NUMA nodes.
Its firmware exposes HEST, EINJ, ERST and BERT, among other ACPI tables.
No serial numbers, UUIDs, or raw host ACPI tables are used as test fixtures.

The aarch64 VM remains QEMU's ``virt`` platform, with native host CPU support
when KVM is usable and an emulated CPU under TCG. It is not an emulation of
Kunpeng's physical peripherals or memory map. HiSilicon non-standard decoding
is selected by CPER section GUID, not SMBIOS model, so the complete valid-format
corpus does not require copying TaiShan firmware or changing QEMU source.
