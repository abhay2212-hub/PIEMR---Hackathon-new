"""Generate a password hash for settings.AUTH_USERS.

Usage: python make_user.py <userid>
"""
import getpass
import hashlib
import json
import secrets
import sys


def main():
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("Usage: python make_user.py <userid>", file=sys.stderr)
        return 2

    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return 1

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
    encoded = f"pbkdf2$200000${salt.hex()}${digest.hex()}"
    print(f"AUTH_USERS = {{{json.dumps(sys.argv[1])}: {json.dumps(encoded)}}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
