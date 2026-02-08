import os
import re
import sys
import time
import shutil
import random
import string
import logging
import sqlite3
import discord
import tempfile
import requests
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from dotenv import load_dotenv

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    filename='logs/app.log',
    filemode='a',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)

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

def safe_guild_name(guild: discord.Guild) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", guild.name)

# TODO: Don't prepend DB_FOLDER here, do that later or rename function
def safe_guild_filename(guild: discord.Guild):
    return os.path.join(DB_FOLDER, f"{safe_guild_name(guild)}_{guild.id}.db")

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
    logging.info(f"created database for guild {guild.id}")
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
        
        logging.info(f"{interaction.user} is attempting to verify")

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
            logging.info("OTP successfull sent")
            await interaction.response.send_message("📧 OTP sent! Click below to enter it.", view=OTPView(), ephemeral=True)
            await log_admin(f"📨 OTP sent to {email} for {interaction.user}", interaction.guild)
        else:
            # Could be an actual issue but could also just be that an invalid email was entered.
            # If its an actual issue, then we might have run out of API usage this month.
            logging.info(f"OTP failed to send to {interaction.user} in {interaction.guild}")
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
        logging.info(f"verified user {interaction.user}")
        await log_admin(f"✅ {interaction.user} verified with {record['email']}", interaction.guild)

class OTPView(discord.ui.View):
    @discord.ui.button(label="Enter OTP", style=discord.ButtonStyle.primary)
    async def enter_otp(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OTPModal())

# TODO: Remove this function since it is redundant.
def get_guild_db_path(guild: discord.Guild) -> str:
    return f"{safe_guild_filename(guild)}"

def close_guild_db(guild: discord.Guild):
    logging.info(f"closing guild db connection for {guild}")
    gid = guild.id
    conn = db_connections.pop(gid, None)
    if conn:
        conn.close()

# ---------------- COMMANDS ----------------
# main command
@tree.command(name="verify", description="Verify your email")
@app_commands.guild_only()
async def verify(interaction: discord.Interaction):
    await interaction.response.send_modal(EmailModal())


@bot.tree.command(name="export", description="Download the verification database file")
@app_commands.default_permissions(administrator=True) # need to be admin
@app_commands.checks.has_permissions(administrator=True)
async def export_db(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    db_path = get_guild_db_path(interaction.guild)  # type: ignore
    
    if not os.path.exists(db_path):
        logging.warning(f"export failed due to lack of database file in {interaction.guild}")
        await interaction.followup.send("❌ Database file not found.")
        return

    filename = f"verification_backup_{interaction.guild.id}.db" # type: ignore

    logging.info(f"user {interaction.user} is exporting database for guild: {interaction.guild}")
    await interaction.followup.send(
        content="📦 Here is the current verification database:",
        file=discord.File(db_path, filename=filename)
    )
    
@bot.tree.command(name="import", description="Replace the verification database with an uploaded backup")
@app_commands.default_permissions(administrator=True) # need to be admin
@app_commands.checks.has_permissions(administrator=True)
async def import_db(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)

    if not file.filename.endswith(".db"):
        await interaction.followup.send("❌ Please upload a valid .db SQLite3 file.")
        return

    db_path = get_guild_db_path(interaction.guild) # type: ignore
    
    # this is where backups will be stored later
    guild_folder = safe_guild_name(interaction.guild) # type: ignore
    backup_dir = os.path.join("guild_dbs", "backups", guild_folder)
    os.makedirs(backup_dir, exist_ok=True)

    temp_path = os.path.join(backup_dir, f"upload_{int(time.time())}.db")

    # save uploaded db temporarily
    try:
        data = await file.read()
        with open(temp_path, "wb") as f:
            f.write(data)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to save uploaded file: {e}")
        return

    # validate uploaded db
    try:
        test_conn = sqlite3.connect(temp_path)
        c = test_conn.cursor()

        # basic integrity check
        c.execute("PRAGMA integrity_check;")
        result = c.fetchone()
        if result[0] != "ok":
            raise Exception("SQLite integrity check failed")

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users';")
        if not c.fetchone():
            raise Exception("Missing required 'users' table")

        # get table columns
        c.execute("PRAGMA table_info(users);")
        columns = {row[1] for row in c.fetchall()}

        if "discord_id" not in columns or "email" not in columns:
            raise Exception("Database must contain 'discord_id' and 'email' columns")
        
        test_conn.close()

    except Exception as e:
        os.remove(temp_path)
        await interaction.followup.send(f"❌ Invalid database file: {e}")
        return

    # since its valid lets add any missing fields to it so other functions continue working
    try:
        conn = sqlite3.connect(temp_path)
        c = conn.cursor()

        c.execute("PRAGMA table_info(users);")
        columns = {row[1] for row in c.fetchall()}

        # add missing columns if needed
        if "verified" not in columns:
            c.execute("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 1;")
        if "verified_at" not in columns and "time" not in columns:
            c.execute("ALTER TABLE users ADD COLUMN verified_at TEXT;")

        # if verified exists but is NULL, force verified = 1
        c.execute("UPDATE users SET verified = 1 WHERE verified IS NULL;")

        conn.commit()
        conn.close()

    except Exception as e:
        os.remove(temp_path)
        await interaction.followup.send(f"❌ Failed while upgrading database schema: {e}")
        return
    
    # finally replace the db
    try:
        close_guild_db(interaction.guild)  # type: ignore
        
        timestamp = int(time.time())
        guild_name = safe_guild_name(interaction.guild) # type: ignore
        backup_filename = f"{guild_name}_{interaction.guild.id}_{timestamp}.backup" # type: ignore
        backup_path = os.path.join(backup_dir, backup_filename)

        if os.path.exists(db_path): # move to backups folder
            shutil.move(db_path, backup_path)
            logging.info(f"created database backup at {backup_path}")
        
        # switch imported db into place
        shutil.move(temp_path, db_path)
        # open connection to new db
        get_guild_db(interaction.guild) # type: ignore

        await interaction.followup.send("✅ Database imported successfully.")
        await log_admin(f"📥 {interaction.user} safely replaced the verification database.", interaction.guild)
        logging.info(f"{interaction.user} replaced database for guild: {interaction.guild}")

    except Exception as e:
        logging.error(f"database import failed with error: {e}")
        await interaction.followup.send(f"❌ Import failed during replacement: {e}")

        # Attempt rollback
        try:
            if os.path.exists(backup_path): # pyright: ignore[reportPossiblyUnboundVariable]
                shutil.move(backup_path, db_path) # pyright: ignore[reportPossiblyUnboundVariable]
                get_guild_db(interaction.guild) # type: ignore
                logging.info("Database rollback successful")
        except Exception as rollback_error:
            logging.critical(f"ROLLBACK FAILED: {rollback_error}")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(DISCORD_TOKEN)