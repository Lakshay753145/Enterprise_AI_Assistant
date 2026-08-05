"""Generate a SECRET_KEY for .env.

    python scripts/generate_secret.py
"""

import secrets

if __name__ == "__main__":
    print("\nAdd this to your .env file:\n")
    print(f"SECRET_KEY={secrets.token_urlsafe(64)}\n")
