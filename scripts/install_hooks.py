"""Install the local git hooks (pre-commit vendor-data guard).

Run once per clone: python scripts/install_hooks.py

The repo is public while the TipRanks and Norgate licences are personal-use,
so vendor VALUES must never enter version control. data/ is gitignored, but
gitignore can be overridden with -f; this hook makes the block absolute.
Hooks are per-clone and never versioned, hence this installer.
"""
from pathlib import Path
import stat

HOOK = """#!/bin/sh
# Public repo + personal-use data licences: block vendor-data paths outright.
staged=$(git diff --cached --name-only)
bad=$(printf '%s\\n' "$staged" | grep -Ei '^data/|\\.csv$|\\.xlsx$|tipranks_monitor_.*\\.html$')
if [ -n "$bad" ]; then
  echo "COMMIT BLOCKED - vendor-data path staged (personal-use licence firewall):" >&2
  printf '%s\\n' "$bad" >&2
  exit 1
fi

# docs/monitor/index.html is the hosted monitor SHELL -- code only, no data.
# pipeline.py guards it at build time; this is the last check before a public
# push. A shell carrying an inlined panel would be megabytes, not kilobytes.
if printf '%s\\n' "$staged" | grep -q '^docs/monitor/index\\.html$'; then
  size=$(git cat-file -s "$(git rev-parse :docs/monitor/index.html)")
  if [ "$size" -gt 153600 ]; then
    echo "COMMIT BLOCKED - docs/monitor/index.html is $size bytes (cap 153600)." >&2
    echo "The hosted shell must carry no data. Rebuild with scripts/pipeline.py." >&2
    exit 1
  fi
fi
exit 0
"""


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    hooks = root / ".git" / "hooks"
    if not hooks.is_dir():
        raise SystemExit("no .git/hooks directory found -- run from a clone of the repo")
    hook = hooks / "pre-commit"
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed {hook}")


if __name__ == "__main__":
    main()
