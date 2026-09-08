rasdaemon-ci
============

Independent continuous-integration infrastructure for `rasdaemon
<https://github.com/mchehab/rasdaemon>`_. It builds current Linux and QEMU
sources, exercises rasdaemon in isolated virtual machines, and publishes
results intended for human review.

Latest reports
--------------

.. |feature-pass| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-feature-pass.svg
   :alt: Features passed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |feature-fail| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-feature-fail.svg
   :alt: Features failed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |kernel-pass| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-kernel-pass.svg
   :alt: Kernel checks passed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |kernel-fail| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-kernel-fail.svg
   :alt: Kernel checks failed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |rasdaemon-pass| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-rasdaemon-pass.svg
   :alt: rasdaemon checks passed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |rasdaemon-fail| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-rasdaemon-fail.svg
   :alt: rasdaemon checks failed
   :target: https://mchehab.github.io/rasdaemon-ci/daily/
.. |x86-vm| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-x86-vm-fail.svg
   :alt: x86_64 VM status
   :target: https://mchehab.github.io/rasdaemon-ci/daily/x86_64/
.. |arm64-vm| image:: https://mchehab.github.io/rasdaemon-ci/daily/badge-arm64-vm-fail.svg
   :alt: ARM64 VM status
   :target: https://mchehab.github.io/rasdaemon-ci/daily/aarch64/

.. list-table:: Daily functional results
   :header-rows: 1

   * - Scope
     - PASS
     - FAIL
   * - Features
     - |feature-pass|
     - |feature-fail|
   * - Kernel checks
     - |kernel-pass|
     - |kernel-fail|
   * - rasdaemon checks
     - |rasdaemon-pass|
     - |rasdaemon-fail|

.. list-table:: VM health
   :header-rows: 1

   * - Architecture
     - Status
   * - x86_64
     - |x86-vm|
   * - ARM64
     - |arm64-vm|

* `Daily functional-test report
  <https://mchehab.github.io/rasdaemon-ci/daily/>`_

Each report summarizes implemented feature results and reports x86_64 and
ARM64 VM infrastructure failures separately. It links to the GitHub Actions
run and provides detailed per-component evidence and downloadable
machine-readable files.

Functional feature implementation
---------------------------------

This is the implementation inventory, not a PASS/FAIL report. Status describes
the declared functional test plan and available producer/device simulation;
it does not depend on whether the latest GitHub run has completed.
``Implemented`` means an injection or consumer check and its assertions exist.
``Partial`` means representative paths exist but identified event families,
producer types, or lifecycle checks remain missing. ``Not implemented`` means
no functional check exists yet.
``Not possible`` is scoped to the current two-architecture hardware setup,
not a claim that a feature can never be tested.

The daily report derives its feature names from the tested rasdaemon source.
Unsupported platform features and unimplemented checks are N/A, not FAIL, and
do not contribute to regression badges. Detailed reports list them separately
under ``Features not tested``, including missing portions of partial features.
The Pages publisher also adds this maintained gap list when reading older daily
artifacts, so the published status does not depend on which harness revision
created the artifact.
Missing results, skipped, failed, and duplicate checks whose implementation
exists still do not count as PASS. An absent result is not automatically
classified as an implementation gap.
Explicit N/A results count as neither PASS nor FAIL. If a VM stops partway
through a suite, completed feature verdicts still contribute to the totals;
checks not run because of that interruption remain N/A.
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
     - Partial
     - Load the x86 MCE ERST submodule, write/read a real ERST CPER, consume
       both pstore layouts with legacy-first selection, and verify deletion
       survives remount. Fatal-MCE reboot and post-boot discovery are not
       simulated.
   * - extlog
     - x86_64
     - Partial
     - Guest software producer emits the kernel EXTLOG tracepoint; verify
       decoded fields. Physical firmware EXTLOG delivery is not simulated.
   * - hisi-ns-decode
     - aarch64
     - Partial
     - All four registered formats: common, HIP08 OEM type 1, OEM type 2,
       and PCIe local. Batched fixtures cover every named module/submodule,
       known and unknown severity, each validity bit, BDF and register dumps.
       Execution and malformed-input coverage are not yet simulated.
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
   * - poison-page-stat (internal)
     - aarch64
     - Implemented
     - Memory-failure injection must produce the configured threshold warning.
   * - postgresql
     - x86_64
     - Implemented
     - Real PostgreSQL service, recording backend and installed CLI readback.
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

Known untested portions and unavailable tests
---------------------------------------------

These are deliberate coverage gaps, shown in the detailed daily report's
``Features not tested`` table. Representative checks for a ``Partial`` feature
still contribute PASS/FAIL results; only the listed missing portions are N/A.
Unavailable tests have no runnable producer or emulated device and are excluded
from the PASS/FAIL badge totals.

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
   * - amp-ns-decode / additional coverage
     - aarch64
     - Partial (N/A)
     - Only Ampere payload 0 has a producer; other AMP payload formats are not
       currently simulated.
   * - erst / fatal-MCE reboot
     - x86_64
     - Partial (N/A)
     - The current fixture does not inject a fatal MCE and reboot the VM.
   * - extlog / firmware delivery
     - x86_64
     - Partial (N/A)
     - The current producer is software-only; firmware EXTLOG delivery is not
       available in the QEMU plan.
   * - hisi-ns-decode / malformed and execution coverage
     - aarch64
     - Partial (N/A)
     - Valid decoder fixtures exist, but malformed inputs and execution through
       every registered format are not yet simulated.
   * - jaguar-ns-decode / additional coverage
     - aarch64
     - Partial (N/A)
     - Only Jaguar payload 0 has a producer.
   * - nvidia-ns-decode / additional coverage
     - aarch64
     - Partial (N/A)
     - Only one NVIDIA non-standard format has a producer.
   * - yitian-ns-decode / additional coverage
     - aarch64
     - Partial (N/A)
     - Only the Yitian DDR register-dump format has a producer.
   * - debug-sql
     - aarch64
     - Not implemented (N/A)
     - No functional SQL debug-logging test is assigned.
   * - pcie-edpc
     - x86_64
     - Not implemented (N/A)
     - QEMU's current test topology has no DPC-capable PCIe port. The retained
       build-flag check is N/A for this reason; it does not fail daily badges.
   * - reri / reri-event
     - riscv64
     - Not implemented (N/A)
     - QEMU currently has no RERI event producer or injection mechanism. This
       remains a planned RISC-V test once QEMU can provide one.
   * - debug-sql (internal)
     - aarch64
     - Not implemented
     - This is a debug feature that turns on SQL logs. It doesn't make sense
       having a tester for it.

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

A RISC-V CPU emulation alone does not supply a RERI error source. QEMU
currently has no RERI event producer or injection mechanism. An
`experimental RAS stack
<https://lists.infradead.org/pipermail/linux-riscv/2026-January/083224.html>`_
documents RERI emulation using QEMU together with OpenSBI, EDK2 and Linux
changes. That stack delivers errors through GHES/CPER; rasdaemon's ``reri``
decoder expects the separate ``ras:reri_event`` tracepoint. Their connection
would have to be verified or implemented before claiming RERI decoder coverage.
The inspected local QEMU and Linux trees contain no RERI producer.
Once QEMU exposes a producer, a RISC-V firmware, kernel and guest scenario
can be added.

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
