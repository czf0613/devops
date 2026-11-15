"""Create the ignored local configuration without overwriting existing values."""

import os
import re
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
template = (
    (root / ".env.example")
    .read_text()
    .replace("replace-with-local-password", secrets.token_urlsafe(32))
    .replace("replace-with-redis-password", secrets.token_urlsafe(32))
)
existing = target.read_text() if target.exists() else ""
keys = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", existing, re.MULTILINE))
missing = [
    line
    for line in template.splitlines()
    if re.match(r"^[A-Z][A-Z0-9_]*=", line) and line.split("=", 1)[0] not in keys
]
if not target.exists():
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(template)
elif missing:
    with target.open("a") as output:
        output.write("\n# Added development settings\n" + "\n".join(missing) + "\n")
target.chmod(0o600)
print(
    f"Local environment ready; added {len(missing)} settings, existing values preserved."
)
