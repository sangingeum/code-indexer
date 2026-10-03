"""Sensitive-content exclusion (privacy work item).

Two layers, both applied at index time:
1. **Filename skips** — built-in default globs for credential/secret-bearing
   files (.env*, *.pem, *.key, id_rsa*, *.p12, *.kdbx, credentials*,
   secrets*.{json,yaml,yml,toml}, *.tfstate). A file whose path matches is
   never indexed at all.
2. **Content scan** — high-confidence secret patterns (AWS access keys,
   PEM private-key blocks, GitHub tokens, JWT triples, Slack tokens). A file
   whose content matches is skipped whole; this is deliberately conservative
   (whole-file, not per-chunk) because splitting a secret-bearing file would
   still leak surrounding context into the index.

Both layers are bypassed only by the explicit per-project override
``allow_sensitive`` (CLI --allow-sensitive on add-project, stored in the
manifest and honored on later passes). Skipped items are counted and
reported by index-status — never silently.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

# -- layer 1: filename globs (matched against the project-relative path and
#    the bare filename, fnmatch semantics) -----------------------------------

SENSITIVE_FILE_GLOBS: tuple[str, ...] = (
    ".env*", "*.pem", "*.key", "id_rsa*", "*.p12", "*.kdbx",
    "credentials*", "secrets*.json", "secrets*.yaml", "secrets*.yml",
    "secrets*.toml", "*.tfstate",
)

# -- layer 2: high-confidence content patterns ------------------------------

SENSITIVE_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),                        # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),      # PEM private key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),              # GitHub token
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\."
               r"[A-Za-z0-9_-]{10,}"),                      # JWT-like triple
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),            # Slack token
)


@dataclass
class SensitiveReport:
    """Counts for index-status: what the exclusion layers skipped."""

    files_by_name: int = 0
    files_by_content: int = 0
    skipped_names: list[str] = field(default_factory=list)
    skipped_content: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.files_by_name + self.files_by_content

    def summary(self) -> str | None:
        if not self.total:
            return None
        parts = []
        if self.files_by_name:
            parts.append(f"{self.files_by_name} by filename")
        if self.files_by_content:
            parts.append(f"{self.files_by_content} by content")
        return (f"sensitive files skipped: {', '.join(parts)}"
                f" — use --allow-sensitive to index them")


def filename_is_sensitive(rel_path: str) -> bool:
    """True when the path (or its basename) matches a built-in secret glob."""
    name = rel_path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel_path, glob) or fnmatch.fnmatch(name, glob)
               for glob in SENSITIVE_FILE_GLOBS)


def content_is_sensitive(text: str) -> bool:
    """True when the text carries a high-confidence secret pattern."""
    return any(pattern.search(text) for pattern in SENSITIVE_CONTENT_PATTERNS)


def file_is_sensitive(rel_path: str, text: str) -> str | None:
    """Exclusion verdict for one file: 'filename', 'content', or None."""
    if filename_is_sensitive(rel_path):
        return "filename"
    if content_is_sensitive(text):
        return "content"
    return None