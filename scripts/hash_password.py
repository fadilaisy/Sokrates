"""Make a password hash for AUTH_USERS, so the plain password never sits in the env file.

    python scripts/hash_password.py            # asks for the password (not echoed)

Paste the output as the password part: budi:maintenance_head:<hash>:Budi Santoso
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.auth import hash_password  # noqa: E402

# Never take the password as an argument: it would land in shell history and `ps`.
pw = getpass.getpass("Password: ")
if pw != getpass.getpass("Again: "):
    sys.exit("Passwords don't match.")
print(hash_password(pw))
