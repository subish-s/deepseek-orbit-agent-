import secrets
import hashlib
import time

def generate_bearer_token():
    random_part = secrets.token_urlsafe(64)
    timestamp = str(time.time())

    token = hashlib.sha512(
        f"{random_part}:{timestamp}".encode()
    ).hexdigest()

    return f"Bearer {token}"

print(generate_bearer_token())