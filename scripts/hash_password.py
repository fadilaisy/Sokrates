"""Make a password hash for AUTH_USERS, so the plain password never sits in the env file.

    python scripts/hash_password.py            # asks for the password
    python scripts/hash_password.py s3cret     # or pass it

Paste the output as the password part: budi:maintenance_head:<hash>:Budi Santoso
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.auth import hash_password  # noqa: E402

pw = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Password: ")
print(hash_password(pw))
