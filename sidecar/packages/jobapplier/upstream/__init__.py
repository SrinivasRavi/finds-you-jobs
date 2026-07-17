# jobapplier.upstream — AGPL-3.0 subtree (see LICENSE).
# SPDX-License-Identifier: AGPL-3.0-only
"""Skyvern-derived observation core (AGPL-3.0-only subtree).

A trimmed carry of Skyvern's screenshot + interactive-element-tree observation
layer, forked at commit ``28db09cb59d2f3c15b1a8e1a8405f1a9eaa36ca3``. Skyvern is
AGPL-3.0 — the same license as finds-you-jobs-owned code — so no license
boundary is required; even so, every file here keeps an
``SPDX-License-Identifier: AGPL-3.0-only`` header, a per-file provenance line to
its exact upstream source path, and the applied trims. The verbatim upstream
license text is ``LICENSE`` in this directory. See ``../provenance.md`` for the
authoritative take/trim record.

This subtree is observation-only (roadmap commit 12 Slice A): it injects
``domUtils.js``, walks the page and its child frames, assembles/trims the
interactive-element tree, renders it as compact HTML, and takes a viewport
screenshot. The agent loop, actions, and any fill/submit capability are
finds-you-jobs-owned and land in later commits.
"""
