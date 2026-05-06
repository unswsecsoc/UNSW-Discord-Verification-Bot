import re
import secrets

import requests

import config

with open("email_template.html") as f:
    EMAIL_HTML_TEMPLATE = f.read()


def generate_otp():
    return "".join(secrets.token_hex(nbytes=config.OTP_LENGTH // 2).upper())


def valid_email_domain(email):
    match = re.match(r"[^@]+@([^@]+\.[^@]+)", email)
    if not match:
        return False
    domain = match.group(1).lower()
    return domain in config.ALLOWED_DOMAINS


def send_email_otp(to_email, code):
    if not config.MAILGUN_API_KEY:
        print(f"OTP for {to_email}: {code}")

        class MockResponse:
            status_code = 200

        return MockResponse()

    return requests.post(
        f"https://api.mailgun.net/v3/{config.MAILGUN_DOMAIN}/messages",
        auth=("api", config.MAILGUN_API_KEY),
        data={
            "from": config.MAILGUN_FROM,
            "to": [to_email],
            "subject": "Verify your email address",
            "html": EMAIL_HTML_TEMPLATE
                    .replace("{{code}}", code)
                    .replace("{{expiry_mins}}", str(config.OTP_EXPIRY_SECONDS//60)),
            "text": f"Your verification code is: {code}\nExpires in {config.OTP_EXPIRY_SECONDS//60} minutes.",
        },
        timeout=10,
    )
