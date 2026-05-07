import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY")
MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN")
MAILGUN_FROM = os.environ.get("MAILGUN_FROM")
VERIFIED_ROLE_NAME = os.environ["VERIFIED_ROLE_NAME"]
ALLOWED_DOMAINS = [d.strip().lower() for d in os.environ["ALLOWED_EMAIL_DOMAINS"].split(",")]

OTP_EXPIRY_SECONDS = 600
OTP_RESEND_COOLDOWN = 120
OTP_LENGTH = 10

project_root = Path(__file__).resolve().parent.parent

LOG_DIR = project_root / "logs"
DB_DIR = project_root / "guild_dbs"
TEMPLATES_DIR = project_root / "src" / "templates"
