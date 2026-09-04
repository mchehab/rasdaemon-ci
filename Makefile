PYTHON ?= python3

python-test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest -v \
		tests.test_ras_qemu tests.test_qemu_source_refs \
		tests.test_publish_results

qemu-bundle:
	tests/qemu/ci/build-bundle.sh --channel release \
		--output "$(CURDIR)/build/rasdaemon-ci" \
		--tag rasdaemon-ci:local
