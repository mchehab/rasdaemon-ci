#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

"""Resolve and pin the external sources used by the QEMU test lab."""

import argparse
import json
import re
import subprocess
import sys
import tempfile


DEFAULT_KERNEL_REPOSITORY = "https://github.com/torvalds/linux.git"
DEFAULT_QEMU_REPOSITORY = "https://gitlab.com/mchehab_kernel/qemu.git"
FINAL_TAG = re.compile(r"^v(\d+)\.(\d+)$")
RC_TAG = re.compile(r"^v(\d+)\.(\d+)-rc(\d+)$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class SourceError(RuntimeError):
    """An external source could not be resolved safely."""


def run(command: list[str], cwd: str | None = None) -> str:
    completed = subprocess.run(command, cwd=cwd, check=False, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    if completed.returncode:
        raise SourceError("command failed: %s\n%s" %
                          (" ".join(command), completed.stdout.strip()))

    return completed.stdout


def ls_remote(repository: str, *patterns: str,
              include_peeled: bool = False) -> dict[str, str]:
    command = ["git", "ls-remote"]
    if not include_peeled:
        command.append("--refs")

    output = run(command + [repository, *patterns])
    refs = {}

    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2 or not FULL_SHA.fullmatch(fields[0]):
            continue

        refs[fields[1]] = fields[0]

    return refs


def select_kernel_tag(refs: dict[str, str], channel: str) -> str:
    candidates = []

    for ref in refs:
        name = ref.removeprefix("refs/tags/")
        final = FINAL_TAG.fullmatch(name)
        rc = RC_TAG.fullmatch(name)
        if final and channel in ("release", "latest"):
            major, minor = (int(value) for value in final.groups())
            candidates.append(((major, minor, 1, 0), name))
        elif rc and channel in ("rc", "latest"):
            major, minor, number = (int(value) for value in rc.groups())
            candidates.append(((major, minor, 0, number), name))

    if not candidates:
        raise SourceError("no %s kernel tags found" % channel)
    return max(candidates)[1]


def resolve_ref(repository: str, ref: str) -> tuple[str, str]:
    candidates = [ref]
    if not ref.startswith("refs/"):
        candidates.extend(("refs/heads/" + ref, "refs/tags/" + ref))

    refs = ls_remote(repository, *candidates,
                     include_peeled=any(value.startswith("refs/tags/")
                                        for value in candidates))

    for candidate in candidates:
        if candidate.startswith("refs/tags/") and candidate + "^{}" in refs:
            return refs[candidate + "^{}"], candidate
        if candidate in refs:
            return refs[candidate], candidate
    if FULL_SHA.fullmatch(ref):
        return ref, ref
    raise SourceError("%s does not advertise ref %s" % (repository, ref))


def exact_tags(repository: str, commit: str) -> list[str]:
    """Find lightweight and peeled annotated tags pointing at a commit."""
    if not FULL_SHA.fullmatch(commit):
        raise SourceError("invalid source commit")
    refs = ls_remote(repository, "refs/tags/*", include_peeled=True)
    return sorted(ref.removeprefix("refs/tags/") for ref, value in refs.items()
                  if ref.startswith("refs/tags/") and not ref.endswith("^{}")
                  and refs.get(ref + "^{}", value) == commit)


def describe_commit(repository: str, commit: str, tags_repository: str | None = None) -> dict:
    """Describe a tested commit using tags from an optional authoritative repository."""
    if not FULL_SHA.fullmatch(commit):
        raise SourceError("invalid source commit")
    with tempfile.TemporaryDirectory(prefix="ras-ci-describe-") as directory:
        run(["git", "init", "-q", directory])
        run(["git", "-C", directory, "-c", "protocol.version=2", "fetch", "-q",
             "--filter=tree:0", "--no-recurse-submodules", "--no-tags", repository, commit])
        run(["git", "-C", directory, "-c", "protocol.version=2", "fetch", "-q",
             "--filter=tree:0", "--no-recurse-submodules", tags_repository or repository,
             "+refs/tags/*:refs/tags/*"])
        result = subprocess.run(["git", "-C", directory, "describe", commit],
                                check=False, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        description = result.stdout.strip() if not result.returncode else None
        return {"commit": commit, "describe": description,
                "error": result.stderr.strip() if result.returncode else ""}


def resolve_kernel(repository: str, channel: str,
                   ref: str | None = None) -> dict[str, str]:
    if channel == "custom":
        if not ref:
            raise SourceError("custom kernel selection requires --ref")
        commit, resolved_ref = resolve_ref(repository, ref)

        return {"repository": repository, "channel": channel,
                "ref": resolved_ref, "commit": commit}
    refs = ls_remote(repository, "refs/tags/v*")
    tag = select_kernel_tag(refs, channel)
    commit, resolved_ref = resolve_ref(repository, "refs/tags/" + tag)

    return {"repository": repository, "channel": channel,
            "ref": resolved_ref, "tag": tag, "commit": commit}


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    git_ref = commands.add_parser("resolve-git")
    git_ref.add_argument("--repository", required=True)
    git_ref.add_argument("--ref", required=True)
    kernel = commands.add_parser("resolve-kernel")
    kernel.add_argument("--repository", default=DEFAULT_KERNEL_REPOSITORY)
    kernel.add_argument("--channel", choices=("latest", "release", "rc", "custom"),
                        default="latest")
    kernel.add_argument("--ref")
    tags = commands.add_parser("exact-tags")
    tags.add_argument("--repository", required=True)
    tags.add_argument("--commit", required=True)
    metadata_tags = commands.add_parser("tags-for-metadata")
    metadata_tags.add_argument("--metadata", required=True)
    describe = commands.add_parser("describe")
    describe.add_argument("--repository", required=True)
    describe.add_argument("--commit", required=True)
    describe.add_argument("--tags-repository")
    describe_metadata = commands.add_parser("describe-metadata")
    describe_metadata.add_argument("--metadata", required=True)
    describe_metadata.add_argument("--tags-repository")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    try:
        if args.command == "resolve-git":
            commit, ref = resolve_ref(args.repository, args.ref)
            result = {"repository": args.repository, "ref": ref,
                      "commit": commit}
        elif args.command == "resolve-kernel":
            result = resolve_kernel(args.repository, args.channel, args.ref)
        elif args.command == "exact-tags":
            result = {"commit": args.commit, "tags": exact_tags(args.repository, args.commit)}
        elif args.command == "tags-for-metadata":
            with open(args.metadata, encoding="utf-8") as stream:
                metadata = json.load(stream)
            commit = metadata["commit"]
            result = {"commit": commit, "tags": exact_tags(metadata["repository"], commit)}
        elif args.command == "describe":
            result = describe_commit(args.repository, args.commit, args.tags_repository)
        elif args.command == "describe-metadata":
            with open(args.metadata, encoding="utf-8") as stream:
                metadata = json.load(stream)
            result = describe_commit(metadata["repository"], metadata["commit"],
                                     args.tags_repository)
        else:
            raise SourceError("unknown command")
        json.dump(result, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OSError, json.JSONDecodeError, SourceError, KeyError) as error:
        print("source-refs: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
