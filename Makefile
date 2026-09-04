PYTHON ?= python3

python-test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest -v \
		tests.test_ras_qemu tests.test_qemu_source_refs

sync-rasdaemon:
	$(PYTHON) tests/qemu/source_refs.py sync-rasdaemon \
		--lock rasdaemon.lock.json --update

check-rasdaemon-sync:
	$(PYTHON) tests/qemu/source_refs.py sync-rasdaemon \
		--lock rasdaemon.lock.json

qemu-bundle:
	tests/qemu/ci/build-bundle.sh --channel release \
		--output "$(CURDIR)/build/rasdaemon-ci" \
		--tag rasdaemon-ci:local
