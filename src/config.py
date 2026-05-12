import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY")
MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN")
MAILGUN_FROM = os.environ.get("MAILGUN_FROM")
ALLOWED_DOMAINS = [d.strip().lower() for d in os.environ["ALLOWED_EMAIL_DOMAINS"].split(",")]

OTP_EXPIRY_SECONDS = 600
OTP_RESEND_COOLDOWN = 120
OTP_LENGTH = 10

project_root = Path(__file__).resolve().parent.parent

LOG_DIR = project_root / "logs"
DB_DIR = project_root / "guild_dbs"
TEMPLATES_DIR = project_root / "src" / "templates"

ENVIRONMENT = os.environ.get("ENVIRONMENT", "local")  # local/dev/prod

# Rate Limiting
# /export
RATE_LIMIT_EXPORT_TIMES = int(os.environ.get("RATE_LIMIT_EXPORT_TIMES", "10"))
RATE_LIMIT_EXPORT_SECONDS = int(os.environ.get("RATE_LIMIT_EXPORT_SECONDS", "300"))

# /import
RATE_LIMIT_IMPORT_TIMES = int(os.environ.get("RATE_LIMIT_IMPORT_TIMES", "10"))
RATE_LIMIT_IMPORT_SECONDS = int(os.environ.get("RATE_LIMIT_IMPORT_SECONDS", "300"))
IMPORT_MAX_SIZE_MB = int(os.environ.get("IMPORT_MAX_SIZE_MB", "5"))

# OTP attempts
RATE_LIMIT_OTP_TIMES = int(os.environ.get("RATE_LIMIT_OTP_TIMES", "10"))
RATE_LIMIT_OTP_SECONDS = int(os.environ.get("RATE_LIMIT_OTP_SECONDS", "300"))

# Email sending
# per-member (user + guild)
RATE_LIMIT_EMAIL_MEMBER_TIMES = int(os.environ.get("RATE_LIMIT_EMAIL_MEMBER_TIMES", "5"))
RATE_LIMIT_EMAIL_MEMBER_SECONDS = int(os.environ.get("RATE_LIMIT_EMAIL_MEMBER_SECONDS", "1800"))
# per-user
RATE_LIMIT_EMAIL_USER_TIMES = int(os.environ.get("RATE_LIMIT_EMAIL_USER_TIMES", "20"))
RATE_LIMIT_EMAIL_USER_SECONDS = int(os.environ.get("RATE_LIMIT_EMAIL_USER_SECONDS", "86400"))
