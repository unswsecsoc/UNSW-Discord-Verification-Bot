import os
import sys
import re
import time
import sqlite3
import random
import string
import requests
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
MAILGUN_API_KEY = os.environ["MAILGUN_API_KEY"]
MAILGUN_DOMAIN = os.environ["MAILGUN_DOMAIN"]
MAILGUN_FROM = os.environ["MAILGUN_FROM"]
GUILD_ID = int(os.environ["GUILD_ID"])
VERIFIED_ROLE_NAME = os.environ["VERIFIED_ROLE_NAME"]
ADMIN_LOG_CHANNEL_ID = int(os.environ["ADMIN_LOG_CHANNEL_ID"])
ALLOWED_DOMAINS = [d.strip().lower() for d in os.environ["ALLOWED_EMAIL_DOMAINS"].split(",")]

OTP_EXPIRY_SECONDS = 600
OTP_RESEND_COOLDOWN = 120
OTP_LENGTH = 10

# what events we want our bot to be aware of
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Database
conn = sqlite3.connect("verification.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    discord_id INTEGER PRIMARY KEY,
    email TEXT,
    verified INTEGER DEFAULT 0,
    verified_at INTEGER
)
""")
conn.commit()

# Active OTP dictionary
pending_verifications = {}  
# user_id: {code, expires, last_sent, email}

def generate_otp():
    return ''.join(random.choices(string.digits, k=OTP_LENGTH))


def valid_email_domain(email):
    match = re.match(r"[^@]+@([^@]+\.[^@]+)", email)
    if not match:
        return False
    domain = match.group(1).lower()
    return domain in ALLOWED_DOMAINS


def send_email_otp(to_email, code):
    return requests.post(
        f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": MAILGUN_FROM,
            "to": [to_email],
            "subject": "Verify your email address",
            "text": f"Your verification code is: {code}\nExpires in {OTP_EXPIRY_SECONDS/60} minutes."
        },
        timeout=10
    )


async def log_admin(message):
    channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        print("Supplied ADMIN_LOG_CHANNEL_ID is not the ID of a discord.TextChannel", file=sys.stderr)
    else:
        await channel.send(message)

# create a popup in discord upon !verify invocation
class EmailModal(discord.ui.Modal, title="Email Verification"):
    email = discord.ui.TextInput(label="Enter your UNSW email address", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        email = self.email.value.strip().lower()

        # Check DB if already verified
        c.execute("SELECT verified FROM users WHERE discord_id=?", (user_id,))
        row = c.fetchone()
        if row and row[0] == 1:
            await interaction.response.send_message("✅ You are already verified.", ephemeral=True)
            return

        if not valid_email_domain(email):
            await interaction.response.send_message("❌ Email domain not allowed.", ephemeral=True)
            await log_admin(f"🚫 {interaction.user} tried invalid domain: {email}")
            return

        now = time.time()
        record = pending_verifications.get(user_id)

        if record and now - record["last_sent"] < OTP_RESEND_COOLDOWN:
            remaining = int(OTP_RESEND_COOLDOWN - (now - record["last_sent"]))
            await interaction.response.send_message(
                f"⏳ Wait {remaining}s before requesting another OTP.",
                ephemeral=True
            )
            return

        code = generate_otp()
        pending_verifications[user_id] = {
            "code": code,
            "expires": now + OTP_EXPIRY_SECONDS,
            "last_sent": now,
            "email": email
        }

        resp = send_email_otp(email, code)

        if resp.status_code == 200:
            await interaction.response.send_message("📧 OTP sent! Click below to enter it.", view=OTPView(), ephemeral=True)
            await log_admin(f"📨 OTP sent to {email} for {interaction.user}")
        else:
            await interaction.response.send_message("❌ Failed to send email.", ephemeral=True)
            await log_admin(f"❌ Mailgun failed for {interaction.user}")

class OTPModal(discord.ui.Modal, title="Enter pin"):
    otp = discord.ui.TextInput(label=f"Enter the {OTP_LENGTH}-digit code", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        record = pending_verifications.get(user_id)

        if not record:
            await interaction.response.send_message("No active verification. Use !verify again.", ephemeral=True)
            return

        if time.time() > record["expires"]:
            del pending_verifications[user_id]
            await interaction.response.send_message("⏰ Code expired.", ephemeral=True)
            await log_admin(f"⌛ OTP expired for {interaction.user}")
            return

        if self.otp.value != record["code"]:
            await interaction.response.send_message("❌ Incorrect code.", ephemeral=True)
            await log_admin(f"❌ Wrong OTP from {interaction.user}")
            return

        # Success — store in DB
        c.execute("""
            INSERT INTO users (discord_id, email, verified, verified_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                email=excluded.email,
                verified=1,
                verified_at=excluded.verified_at
        """, (user_id, record["email"], int(time.time())))
        conn.commit()
        
        guild = interaction.guild
        member = interaction.guild.get_member(interaction.user.id) # type: ignore
        bot_member = guild.get_member(bot.user.id) # type: ignore

        if not guild or not member or not bot_member:
            await interaction.response.send_message("❌ Verification failed (server state error).", ephemeral=True)
            return

        role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)

        if not role:
            await interaction.response.send_message("❌ Verified role not found. Contact an admin.", ephemeral=True)
            return

        # permission check
        if not guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ Bot lacks **Manage Roles** permission.", ephemeral=True)
            return

        # role hierarchy check
        if role >= bot_member.top_role:
            await interaction.response.send_message(
                "❌ Bot cannot assign this role because it is higher than or equal to the bot's top role.",
                ephemeral=True
            )
            return

        try:
            await member.add_roles(role, reason="User completed email verification")
        except discord.Forbidden:
            await interaction.response.send_message("❌ Permission error while assigning role.", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("❌ Discord API error. Try again later.", ephemeral=True)
            return


        role = discord.utils.get(interaction.guild.roles, name=VERIFIED_ROLE_NAME) # type: ignore
        if role:
            await interaction.user.add_roles(role) # type: ignore

        del pending_verifications[user_id]

        await interaction.response.send_message("✅ Verification successful!", ephemeral=True)
        await log_admin(f"✅ {interaction.user} verified with {record['email']}")

class OTPView(discord.ui.View):
    @discord.ui.button(label="Enter OTP", style=discord.ButtonStyle.primary)
    async def enter_otp(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OTPModal())

# ---------------- COMMAND ----------------

@tree.command(name="verify", description="Verify your email")
@app_commands.guilds(discord.Object(id=GUILD_ID))
@app_commands.guild_only()
async def verify(interaction: discord.Interaction):
    await interaction.response.send_modal(EmailModal())

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
