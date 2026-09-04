Standalone rasdaemon CI repository
==================================

This is a temporary standalone home for rasdaemon QEMU functional
testing. It may be merged back into the main rasdaemon repository in the
future.

The QEMU harness consumes a rasdaemon checkout supplied by the caller.
Every run must provide the image manifest explicitly with
``--manifest``; manifests are generated as part of the CI image build
and are not checked in.

CI uses the rasdaemon source checked out for the workflow run.
