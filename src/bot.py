import os
import time
import json
import hashlib
import logging
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from export import export_db_to_csv, import_csv_to_db
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from otp import generate_otp, send_email_otp, valid_email_domain
from utils import log_admin, get_guild_db_path, get_guild_dir, save_guild_info

os.makedirs(config.LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(config.LOG_DIR, "app.log"),
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)

os.makedirs(config.DB_DIR, exist_ok=True)

# one connection per guild
db_connections = {}

# what events we want our bot to be aware of
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


def get_guild_db(guild: discord.Guild):
    if guild.id in db_connections:
        return db_connections[guild.id]

    path = get_guild_db_path(guild)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            discord_id INTEGER PRIMARY KEY,
            email TEXT,
            verified INTEGER NOT NULL CHECK (verified IN (0, 1)) DEFAULT 0,
            verified_at INTEGER CHECK (verified_at > 0),
            CHECK ((verified_at IS NULL) OR verified) -- verified_at implies verified
        ) STRICT
    """)
    conn.commit()
    db_connections[guild.id] = conn
    logging.info(f"loaded or created database for guild {guild.id}")

    save_guild_info(guild)

    return conn


# Active OTP dictionary
pending_verifications = {}
# (guild_id, user_id): {code, expires, last_sent, email}

def get_commands_hash() -> str:
    # Changes when a commands name or description, or its parameters' name or description changes
    # Also changes if a parameter's mandatoriness changes
    commands = sorted(
        (
            {
                "name": c.name,
                "description": c.description,  # type: ignore
                "parameters": sorted(
                    (
                        {
                            "name": p.name,
                            "description": p.description,
                            "required": p.required,
                        }
                        for p in c.parameters  # type: ignore
                    ),
                    key=lambda p: p["name"],
                ),
            }
            for c in tree.get_commands()
        ),
        key=lambda c: c["name"],
    )
    return hashlib.md5(json.dumps(commands, sort_keys=True).encode()).hexdigest()


# modal for the user to enter their email
class EmailModal(discord.ui.Modal, title="Email Verification"):
    email = discord.ui.TextInput(label="Enter your UNSW email address", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        email = self.email.value.strip().lower()

        logging.info(f"{interaction.user} is attempting to verify")

        # Check DB if already verified
        conn = get_guild_db(interaction.guild)  # type: ignore Bot can only run in a guild
        c = conn.cursor()
        c.execute("SELECT verified, email FROM users WHERE discord_id=?", (user_id,))
        row = c.fetchone()

        if row and row[0] == 1:
            guild = interaction.guild
            member = guild.get_member(user_id)  # type: ignore Bot can only run in a guild
            bot_member = guild.get_member(bot.user.id)  # type: ignore
            role = discord.utils.get(guild.roles, name=config.VERIFIED_ROLE_NAME)  # type: ignore

            if not member or not role or not bot_member:
                await interaction.response.send_message(
                    "⚠️ You are verified but I couldn't check roles. Contact an admin.",
                    ephemeral=True,
                )
                return

            # If they already have the role
            if role in member.roles:
                await interaction.response.send_message(
                    "✅ You are already verified.", ephemeral=True
                )
                return

            # Role missing — try to restore it
            if not guild.me.guild_permissions.manage_roles:  # type: ignore
                await interaction.response.send_message(
                    "⚠️ You're verified but I don't have permission to restore your role.",
                    ephemeral=True,
                )
                return

            if role >= bot_member.top_role:
                await interaction.response.send_message(
                    "⚠️ You're verified but my role is too low to re-assign yours.",
                    ephemeral=True,
                )
                return

            try:
                await member.add_roles(role, reason="Restoring verified role")
                await interaction.response.send_message(
                    "🔁 You were already verified — I've restored your role.",
                    ephemeral=True,
                )
                await log_admin(
                    f"♻️ Restored verified role for {interaction.user}", guild
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "⚠️ Verified in database but role restore failed due to permissions.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                await interaction.response.send_message(
                    "⚠️ Discord error while restoring role. Try again later.",
                    ephemeral=True,
                )

            return

        if not valid_email_domain(email):
            await interaction.response.send_message(
                "❌ Email domain not allowed.", ephemeral=True
            )
            await log_admin(
                f"🚫 {interaction.user} tried invalid domain: {email}",
                interaction.guild,
            )
            return

        now = time.time()
        key = (interaction.guild.id, user_id)  # type: ignore
        record = pending_verifications.get(key)

        if record and now - record["last_sent"] < config.OTP_RESEND_COOLDOWN:
            remaining = int(config.OTP_RESEND_COOLDOWN - (now - record["last_sent"]))
            await interaction.response.send_message(
                f"⏳ Wait {remaining}s before requesting another OTP.", ephemeral=True
            )
            return

        code = generate_otp()
        key = (interaction.guild.id, user_id)  # type: ignore
        pending_verifications[key] = {
            "code": code,
            "expires": now + config.OTP_EXPIRY_SECONDS,
            "last_sent": now,
            "email": email,
        }

        resp = send_email_otp(email, code)

        if resp.status_code == 200:
            logging.info("OTP successfully sent")
            await interaction.response.send_message(
                "📧 OTP sent! Click below to enter it.", view=OTPView(), ephemeral=True
            )
            await log_admin(
                f"📨 OTP sent to {email} for {interaction.user}", interaction.guild
            )
        else:
            # Could be an actual issue but could also just be that an invalid email was entered.
            # If its an actual issue, then we might have run out of API usage this month.
            logging.warning(
                f"OTP failed to send to {interaction.user} in {interaction.guild}"
            )
            await interaction.response.send_message(
                "❌ Failed to send email.", ephemeral=True
            )
            await log_admin(
                f"❌ Mailgun failed for {interaction.user}", interaction.guild
            )


class OTPModal(discord.ui.Modal, title="Enter pin"):
    otp = discord.ui.TextInput(
        label=f"Enter the {config.OTP_LENGTH}-digit code", required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        key = (interaction.guild.id, user_id)  # type: ignore
        record = pending_verifications.get(key)

        if not record:
            await interaction.response.send_message(
                "No active verification. Please click the `Verify Email` button again.", ephemeral=True
            )
            return

        if time.time() > record["expires"]:
            del pending_verifications[key]

            await interaction.response.send_message("⏰ Code expired.", ephemeral=True)
            await log_admin(f"⌛ OTP expired for {interaction.user}", interaction.guild)
            return

        if self.otp.value.lower() != record["code"].lower():
            await interaction.response.send_message(
                "❌ Incorrect code.", ephemeral=True
            )
            await log_admin(f"❌ Wrong OTP from {interaction.user}", interaction.guild)
            return

        # Success — store in DB
        conn = get_guild_db(interaction.guild)  # type: ignore Bot can only run in a guild
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO users (discord_id, email, verified, verified_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                email=excluded.email,
                verified=1,
                verified_at=excluded.verified_at
        """,
            (user_id, record["email"], int(time.time())),
        )
        conn.commit()

        guild = interaction.guild
        member = interaction.guild.get_member(interaction.user.id)  # type: ignore
        bot_member = guild.get_member(bot.user.id)  # type: ignore

        if not guild or not member or not bot_member:
            await interaction.response.send_message(
                "❌ Verification failed (server state error).", ephemeral=True
            )
            return

        role = discord.utils.get(guild.roles, name=config.VERIFIED_ROLE_NAME)

        if not role:
            await interaction.response.send_message(
                "❌ Verified role not found. Contact an admin.", ephemeral=True
            )
            return

        # permission check
        if not guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ Bot lacks **Manage Roles** permission.", ephemeral=True
            )
            return

        # role hierarchy check
        if role >= bot_member.top_role:
            await interaction.response.send_message(
                "❌ Bot cannot assign this role because it is higher than or equal to the bot's top role.",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason="User completed email verification")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Permission error while assigning role.", ephemeral=True
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Discord API error. Try again later.", ephemeral=True
            )
            return

        role = discord.utils.get(interaction.guild.roles, name=config.VERIFIED_ROLE_NAME)  # type: ignore
        if role:
            await interaction.user.add_roles(role)  # type: ignore

        del pending_verifications[key]

        await interaction.response.send_message(
            "✅ Verification successful!", ephemeral=True
        )
        logging.info(f"verified user {interaction.user}")
        await log_admin(
            f"✅ {interaction.user} verified with {record['email']}", interaction.guild
        )


class OTPView(discord.ui.View):
    @discord.ui.button(label="Enter OTP", style=discord.ButtonStyle.primary)
    async def enter_otp(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(OTPModal())


class VerifyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify Email", style=discord.ButtonStyle.success, custom_id="verify-button")
    async def verify_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(EmailModal())


def close_guild_db(guild: discord.Guild):
    logging.info(f"closing guild db connection for {guild}")
    gid = guild.id
    conn = db_connections.pop(gid, None)
    if conn:
        conn.close()


# ---------------- COMMANDS ----------------
@bot.tree.command(name="export", description="Download the verification database file")
@app_commands.default_permissions(administrator=True)  # need to be admin
@app_commands.checks.has_permissions(administrator=True)
async def export_db(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    exported_csv = export_db_to_csv(get_guild_db(interaction.guild))  # type: ignore

    filename = f"verification_backup_{interaction.guild.id}_{datetime.now(ZoneInfo("Australia/Sydney")).strftime("%Y-%m-%d_%H-%M-%S")}.csv"  # type: ignore

    logging.info(
        f"user {interaction.user} is exporting database for guild: {interaction.guild}"
    )
    await log_admin(
        f"📤 {interaction.user} exported the verification database.", interaction.guild
    )
    await interaction.followup.send(
        content="📦 Here is the current verification database:",
        file=discord.File(exported_csv, filename=filename),
    )


@bot.tree.command(
    name="import",
    description="Replace the verification database with an uploaded backup",
)
@app_commands.default_permissions(administrator=True)  # need to be admin
@app_commands.checks.has_permissions(administrator=True)
async def import_db(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)

    conn = get_guild_db(interaction.guild)  # type: ignore

    try:
        backup_dir = os.path.join(get_guild_dir(interaction.guild), "backups")
        os.makedirs(backup_dir, exist_ok=True)

        timestamp = int(time.time())
        backup_filename = f"{timestamp}.db.backup"
        backup_path = os.path.join(backup_dir, backup_filename)

        with sqlite3.connect(backup_path) as backup_dest_conn:
            conn.backup(backup_dest_conn)
    except Exception as e:
        logging.error(f"Failed to back up db before importing: {e}")
        await interaction.followup.send(
            "❌ Import failed - failed to create a backup before importing"
        )

    try:
        file_bytes = await file.read()
        success, message = import_csv_to_db(
            conn, file_bytes.decode(errors="backslashreplace")
        )
        await interaction.followup.send(message)
    except Exception as e:
        logging.error(f"database import failed with error: {e}")
        await interaction.followup.send("❌ Import failed")
    else:
        if success:
            await log_admin(
                f"📥 {interaction.user} imported a new verification database.",
                interaction.guild,
            )
            logging.info(
                f"{interaction.user} replaced database for guild: {interaction.guild}"
            )
        else:
            await log_admin(
                f"❌ Database import requested by {interaction.user} failed.",
                interaction.guild,
            )
            logging.warning(
                f"Database import for guild {interaction.guild} failed: {message}"
            )


@bot.tree.command(
    name="send-verify-button",
    description="Send a verification button to this channel",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(send_messages=True)
async def send_verify_button(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send(
            "Click here to verify your email.", view=VerifyButtonView()
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "Failed to send verification button. "
            "Does the bot have permissions to send messages in this channel?",
            ephemeral=True
        )
    else:
        await interaction.followup.send("Verification button sent.", ephemeral=True)


# Runs once on initial startup
@bot.event
async def setup_hook():
    current_hash = get_commands_hash()
    stored_hash = None
    # Using hashes avoids unecessary syncing
    if os.path.exists("cmd_hash.txt"):
        with open("cmd_hash.txt") as f:
            stored_hash = f.read().strip()

    if current_hash != stored_hash:
        await tree.sync()
        with open("cmd_hash.txt", "w") as f:
            f.write(current_hash)
        logging.info("Commands synced (hash changed)")
    else:
        logging.info("Commands unchanged, skipping sync")


# Runs each time bot reconnects to a server
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    if not config.MAILGUN_API_KEY:
        logging.warning(
            "No Mailgun API key provided. OTPs will be logged to the console."
        )
        print(
            "WARNING: No Mailgun API key provided. OTPs will be logged to the console."
        )

    # Register the button view so it keeps working after a restart
    bot.add_view(VerifyButtonView())


bot.run(config.DISCORD_TOKEN)
