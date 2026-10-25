"""Bundle .gitlab-ci.yml + its local includes into one document for the GitLab CI lint API.

Local includes are resolved by GitLab from the server's default branch, so linting the root file alone would test the
old included files. This inlines them (as text, so `!reference` tags survive) and keeps remote/component includes.

    python scripts/ci_lint_bundle.py > .gg-out/ci-bundle.yml
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def bundle(root: Path = Path(".")) -> str:
    text = (root / ".gitlab-ci.yml").read_text(encoding="utf-8")
    local_files = re.findall(r"^\s*- local:\s*(\S+)", text, flags=re.M)
    # Drop the local include entries (and their rules blocks) from the root file.
    out_lines, skipping = [], False
    for line in text.splitlines():
        if re.match(r"^\s*- local:", line):
            skipping = True
            continue
        other_include = re.match(r"^\s*- (component|project|remote|template):", line)
        if skipping and re.match(r"^\s{4,}\S", line) and not other_include:
            continue
        skipping = False
        out_lines.append(line)
    parts = ["\n".join(out_lines)]
    for f in local_files:
        parts.append(f"# ---- inlined from {f}\n" + (root / f).read_text(encoding="utf-8"))
    return "\n\n".join(parts) + "\n"


if __name__ == "__main__":
    sys.stdout.write(bundle())
