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
VERIFIED_ROLE_NAME = os.environ["VERIFIED_ROLE_NAME"]
ALLOWED_DOMAINS = [d.strip().lower() for d in os.environ["ALLOWED_EMAIL_DOMAINS"].split(",")]

OTP_EXPIRY_SECONDS = 600
OTP_RESEND_COOLDOWN = 120
OTP_LENGTH = 10

DB_FOLDER = "guild_dbs"
os.makedirs(DB_FOLDER, exist_ok=True)

# one connection per guild
db_connections = {}

# what events we want our bot to be aware of
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Database
def safe_guild_filename(guild: discord.Guild):
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", guild.name)
    return os.path.join(DB_FOLDER, f"{name}_{guild.id}.db")


def get_guild_db(guild: discord.Guild):
    if guild.id in db_connections:
        return db_connections[guild.id]

    path = safe_guild_filename(guild)
    conn = sqlite3.connect(path)
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
    db_connections[guild.id] = conn
    return conn

# Active OTP dictionary
pending_verifications = {}  
# (guild_id, user_id): {code, expires, last_sent, email}

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


async def log_admin(message, guild):
    channel = discord.utils.get(guild.text_channels, name="verification-logs")

    if channel is None:
        print(f"No #verification-logs channel in guild {guild.name}", file=sys.stderr)
        return

    if not channel.permissions_for(guild.me).send_messages:
        print(f"Missing permission to send messages in #{channel.name}", file=sys.stderr)
        return

    await channel.send(message)


# create a popup in discord upon /verify invocation
class EmailModal(discord.ui.Modal, title="Email Verification"):
    email = discord.ui.TextInput(label="Enter your UNSW email address", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        email = self.email.value.strip().lower()

        # Check DB if already verified
        conn = get_guild_db(interaction.guild) # type: ignore Bot can only run in a guild
        c = conn.cursor()
        c.execute("SELECT verified, email FROM users WHERE discord_id=?", (user_id,))
        row = c.fetchone()

        if row and row[0] == 1:
            guild = interaction.guild
            member = guild.get_member(user_id) # type: ignore Bot can only run in a guild
            bot_member = guild.get_member(bot.user.id)  # type: ignore
            role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME) # type: ignore

            if not member or not role or not bot_member:
                await interaction.response.send_message("⚠️ You are verified but I couldn't check roles. Contact an admin.", ephemeral=True)
                return

            # If they already have the role
            if role in member.roles:
                await interaction.response.send_message("✅ You are already verified.", ephemeral=True)
                return

            # Role missing — try to restore it
            if not guild.me.guild_permissions.manage_roles: # type: ignore
                await interaction.response.send_message("⚠️ You're verified but I don't have permission to restore your role.", ephemeral=True)
                return

            if role >= bot_member.top_role:
                await interaction.response.send_message("⚠️ You're verified but my role is too low to re-assign yours.", ephemeral=True)
                return

            try:
                await member.add_roles(role, reason="Restoring verified role")
                await interaction.response.send_message("🔁 You were already verified — I've restored your role.", ephemeral=True)
                await log_admin(f"♻️ Restored verified role for {interaction.user}", guild)
            except discord.Forbidden:
                await interaction.response.send_message("⚠️ Verified in database but role restore failed due to permissions.", ephemeral=True)
            except discord.HTTPException:
                await interaction.response.send_message("⚠️ Discord error while restoring role. Try again later.", ephemeral=True)

            return

        if not valid_email_domain(email):
            await interaction.response.send_message("❌ Email domain not allowed.", ephemeral=True)
            await log_admin(f"🚫 {interaction.user} tried invalid domain: {email}", interaction.guild)
            return

        now = time.time()
        key = (interaction.guild.id, user_id) # type: ignore
        record = pending_verifications.get(key)

        if record and now - record["last_sent"] < OTP_RESEND_COOLDOWN:
            remaining = int(OTP_RESEND_COOLDOWN - (now - record["last_sent"]))
            await interaction.response.send_message(
                f"⏳ Wait {remaining}s before requesting another OTP.",
                ephemeral=True
            )
            return

        code = generate_otp()
        key = (interaction.guild.id, user_id) # type: ignore
        pending_verifications[key] = {
            "code": code,
            "expires": now + OTP_EXPIRY_SECONDS,
            "last_sent": now,
            "email": email
        }

        resp = send_email_otp(email, code)

        if resp.status_code == 200:
            await interaction.response.send_message("📧 OTP sent! Click below to enter it.", view=OTPView(), ephemeral=True)
            await log_admin(f"📨 OTP sent to {email} for {interaction.user}", interaction.guild)
        else:
            await interaction.response.send_message("❌ Failed to send email.", ephemeral=True)
            await log_admin(f"❌ Mailgun failed for {interaction.user}", interaction.guild)

class OTPModal(discord.ui.Modal, title="Enter pin"):
    otp = discord.ui.TextInput(label=f"Enter the {OTP_LENGTH}-digit code", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        key = (interaction.guild.id, user_id) # type: ignore
        record = pending_verifications.get(key)

        if not record:
            await interaction.response.send_message("No active verification. Use /verify again.", ephemeral=True)
            return

        if time.time() > record["expires"]:
            del pending_verifications[key]

            await interaction.response.send_message("⏰ Code expired.", ephemeral=True)
            await log_admin(f"⌛ OTP expired for {interaction.user}", interaction.guild)
            return

        if self.otp.value != record["code"]:
            await interaction.response.send_message("❌ Incorrect code.", ephemeral=True)
            await log_admin(f"❌ Wrong OTP from {interaction.user}", interaction.guild)
            return

        # Success — store in DB
        conn = get_guild_db(interaction.guild) # type: ignore Bot can only run in a guild
        c = conn.cursor()
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

        del pending_verifications[key]

        await interaction.response.send_message("✅ Verification successful!", ephemeral=True)
        await log_admin(f"✅ {interaction.user} verified with {record['email']}", interaction.guild)

class OTPView(discord.ui.View):
    @discord.ui.button(label="Enter OTP", style=discord.ButtonStyle.primary)
    async def enter_otp(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OTPModal())

# ---------------- COMMAND ----------------

@tree.command(name="verify", description="Verify your email")
@app_commands.guild_only()
async def verify(interaction: discord.Interaction):
    await interaction.response.send_modal(EmailModal())

@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)
