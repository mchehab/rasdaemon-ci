#!/bin/bash -eux
# SPDX-License-Identifier: GPL-2.0-only

# Shared exact-name artifact lookup/download. An API failure is not a cache miss.
set -o pipefail

operation=$1
shift

case "$operation" in
lookup)
	name=$1
	if ! artifacts=$(gh api --paginate --slurp \
		"/repos/$GITHUB_REPOSITORY/actions/artifacts?name=$name&per_page=100"); then
		echo "Artifact lookup failed for $name; stopping rather than treating an API error as a missing build." >&2
		exit 1
	fi

	id=$(printf '%s' "$artifacts" | jq -r --arg name "$name" \
		'[.[].artifacts[] | select(.name == $name and .expired == false)]
		 | max_by(.id) | .id // empty')

	if test -n "$id"; then
		echo "Artifact available: $name (ID $id)." >&2
	elif printf '%s' "$artifacts" | jq -e --arg name "$name" \
		'any(.[].artifacts[]; .name == $name and .expired == true)' >/dev/null; then
		echo "Artifact unavailable: $name has expired; no usable copy remains." >&2
	else
		echo "Artifact unavailable: no artifact named $name was found." >&2
	fi

	# Only the ID goes to stdout: callers capture it for dependency selection.
	printf '%s\n' "$id"
	;;
download)
	id=$1
	destination=$2
	[[ "$id" =~ ^[0-9]+$ ]]
	archive=$(mktemp)
	trap 'rm -f "$archive"' EXIT
	gh api "/repos/$GITHUB_REPOSITORY/actions/artifacts/$id/zip" > "$archive"
	mkdir -p "$destination"
	unzip -q "$archive" -d "$destination"
	;;
*) echo "Unknown artifact operation: $operation" >&2; exit 2 ;;
esac
