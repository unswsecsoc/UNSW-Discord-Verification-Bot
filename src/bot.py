import logging
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
import logfire
from discord import app_commands
from discord.ext import commands

import config
import logs
from export import export_db_to_csv, import_csv_to_db
from otp import generate_otp, match_email, redact_email, send_email_otp, valid_email_domain
from utils import (
    get_commands_hash,
    get_guild_db,
    get_guild_dir,
    get_verified_role,
    log_admin,
    set_verified_role,
)

# setup Logfire
logs.init()

os.makedirs(config.DB_DIR, exist_ok=True)

# what events we want our bot to be aware of
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Active OTP dictionary
# (guild_id, user_id): {code, expires, last_sent, email}
pending_verifications = {}


def is_verified(member: discord.Member) -> bool:
    """Checks if a user is verified in the DB."""
    conn = get_guild_db(member.guild)
    c = conn.cursor()
    c.execute("SELECT verified, email FROM users WHERE discord_id=?", (member.id,))
    row = c.fetchone()
    return row is not None and row[0] == 1


async def grant_verified_role(member: discord.Member) -> str | None:
    """Grants the verified role to a member. Returns an error message, or None on success."""
    guild = member.guild

    role = get_verified_role(guild)

    if not role:
        return "❌ Verified role not found. Contact an admin."

    # permission check
    if not guild.me.guild_permissions.manage_roles:
        return "❌ Bot lacks **Manage Roles** permission. Contact an admin."

    # role hierarchy check
    if role >= guild.me.top_role:
        await log_admin(
            "❌ Bot cannot assign role: it is higher than or equal to the bot's top role.",
            guild,
        )
        return (
            "❌ Bot cannot assign the verified role because it is higher than"
            " the bot's top role. Contact an admin."
        )

    try:
        await member.add_roles(role, reason="User completed email verification")
    except discord.Forbidden:
        return "❌ Permission error while assigning role. Contact an admin."
    except discord.HTTPException:
        return "❌ Discord API error. Try again later."

    return None


async def restore_verified_role(member: discord.Member) -> str:
    """Re-grants the verified role to a member. Returns a success/error message."""
    guild = member.guild
    role = get_verified_role(guild)

    if not role:
        await log_admin(
            "❌ Bot is unable to check member roles",
            guild,
        )
        return "⚠️ You are verified but I couldn't check roles."

    # If they already have the role
    if role in member.roles:
        return "✅ You are already verified."

    # Role missing - try to restore it
    await log_admin(f"♻️ Restoring verified role for {member}", guild)
    err = await grant_verified_role(member)
    if err is not None:
        return "🔁 You were already verified but I couldn't restore your role:\n" + err
    return "🔁 You were already verified - I've restored your role."


# modal for the user to enter their email
class EmailModal(discord.ui.Modal, title="Email Verification"):
    email = discord.ui.TextInput(label="Enter your UNSW email address", required=True)

    @logfire.instrument(extract_args=["interaction"])
    async def on_submit(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        email = self.email.value.strip().lower()

        logging.info(f"{interaction.user} is attempting to verify")

        if is_verified(interaction.user):
            msg = await restore_verified_role(interaction.user)
            await interaction.response.send_message(msg, ephemeral=True)
            return

        if not match_email(email):
            await interaction.response.send_message("❌ Invalid email format.", ephemeral=True)
            return

        if not valid_email_domain(email):
            await interaction.response.send_message(
                "❌ Email domain not allowed. Allowed domains:"
                + "".join(f"\n- `@{domain}`" for domain in config.ALLOWED_DOMAINS),
                ephemeral=True,
            )
            await log_admin(
                f"🚫 {interaction.user} tried a non-allowed domain: {redact_email(email)}",
                interaction.guild,
            )
            return

        user_id = interaction.user.id
        key = (interaction.guild.id, user_id)
        record = pending_verifications.get(key)

        now = time.time()
        if record and now - record["last_sent"] < config.OTP_RESEND_COOLDOWN:
            remaining = int(config.OTP_RESEND_COOLDOWN - (now - record["last_sent"]))
            await interaction.response.send_message(
                f"⏳ Wait {remaining}s before requesting another OTP.", ephemeral=True
            )
            return

        code = generate_otp()
        key = (interaction.guild.id, user_id)
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
                f"📨 OTP sent to {redact_email(email)} for {interaction.user}", interaction.guild
            )
        else:
            # Could be an actual issue but could also just be that an invalid email was entered.
            # If its an actual issue, then we might have run out of API usage this month.
            logging.warning(f"OTP failed to send to {interaction.user} in {interaction.guild}")
            await interaction.response.send_message("❌ Failed to send email.", ephemeral=True)
            await log_admin(f"❌ Mailgun failed for {interaction.user}", interaction.guild)


class OTPModal(discord.ui.Modal, title="Enter pin"):
    otp = discord.ui.TextInput(label=f"Enter the {config.OTP_LENGTH}-digit code", required=True)

    @logfire.instrument(extract_args=["interaction"])
    async def on_submit(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        user_id = interaction.user.id
        key = (interaction.guild.id, user_id)
        record = pending_verifications.get(key)

        if not record:
            await interaction.response.send_message(
                "No active verification. Please click the `Verify Email` button again.",
                ephemeral=True,
            )
            return

        if time.time() > record["expires"]:
            del pending_verifications[key]

            await interaction.response.send_message("⏰ Code expired.", ephemeral=True)
            await log_admin(f"⌛ OTP expired for {interaction.user}", interaction.guild)
            return

        if self.otp.value.lower() != record["code"].lower():
            await interaction.response.send_message("❌ Incorrect code.", ephemeral=True)
            await log_admin(f"❌ Wrong OTP from {interaction.user}", interaction.guild)
            return

        # Success - store in DB
        conn = get_guild_db(interaction.guild)
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

        err = await grant_verified_role(interaction.user)
        if err is not None:
            await interaction.response.send_message(err, ephemeral=True)
            return

        del pending_verifications[key]

        await interaction.response.send_message("✅ Verification successful!", ephemeral=True)
        logging.info(f"verified user {interaction.user}")
        await log_admin(
            f"✅ {interaction.user} verified with {redact_email(record['email'])}",
            interaction.guild,
        )


class OTPView(discord.ui.View):
    @discord.ui.button(label="Enter OTP", style=discord.ButtonStyle.primary)
    async def enter_otp(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OTPModal())


class VerifyButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Email", style=discord.ButtonStyle.success, custom_id="verify-button"
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        assert isinstance(interaction.user, discord.Member)

        if is_verified(interaction.user):
            msg = await restore_verified_role(interaction.user)
            await interaction.response.send_message(msg, ephemeral=True)
            return

        await interaction.response.send_modal(EmailModal())


# ---------------- COMMANDS ----------------
@bot.tree.command(name="export", description="Download the verification database file")
@app_commands.default_permissions(administrator=True)  # need to be admin
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
@logfire.instrument()
async def export_db(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    exported_csv = export_db_to_csv(get_guild_db(interaction.guild))

    filename = (
        f"verification_backup_{interaction.guild.id}"  # type: ignore
        f"_{datetime.now(ZoneInfo('Australia/Sydney')).strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    )

    logging.info(f"user {interaction.user} is exporting database for guild: {interaction.guild}")
    await log_admin(f"📤 {interaction.user} exported the verification database.", interaction.guild)
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
@app_commands.guild_only()
@logfire.instrument(extract_args=["interaction"])
async def import_db(interaction: discord.Interaction, file: discord.Attachment):
    assert interaction.guild is not None

    await interaction.response.defer(ephemeral=True)

    conn = get_guild_db(interaction.guild)

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
        return

    try:
        file_bytes = await file.read()
        success, message = import_csv_to_db(conn, file_bytes.decode(errors="backslashreplace"))
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
            logging.info(f"{interaction.user} replaced database for guild: {interaction.guild}")
        else:
            await log_admin(
                f"❌ Database import requested by {interaction.user} failed.",
                interaction.guild,
            )
            logging.warning(f"Database import for guild {interaction.guild} failed: {message}")


@bot.tree.command(
    name="send-verify-button",
    description="Send a verification button to this channel",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(send_messages=True)
@app_commands.guild_only()
@logfire.instrument()
async def send_verify_button(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await interaction.channel.send("Click here to verify your email.", view=VerifyButtonView())  # type: ignore
    except discord.Forbidden:
        await interaction.followup.send(
            "Failed to send verification button. "
            "Does the bot have permissions to send messages in this channel?",
            ephemeral=True,
        )
    else:
        await interaction.followup.send("Verification button sent.", ephemeral=True)


@bot.tree.command(
    name="set-verified-role",
    description="Set the role to assign to verified members",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def set_verified_role_cmd(interaction: discord.Interaction, role: discord.Role):
    assert interaction.guild is not None

    await interaction.response.defer(ephemeral=True)

    if role >= interaction.guild.me.top_role:
        await interaction.followup.send(
            "❌ That role is not lower in the role hierarchy than the bot's top role.",
            ephemeral=True,
        )
        return

    try:
        set_verified_role(interaction.guild, role)
        await interaction.followup.send(
            f"✅ Verified role set to {role.mention}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions(roles=False),
        )
        await log_admin(
            f"🔧 {interaction.user} set verified role to {role.mention}",
            interaction.guild,
            allowed_mentions=discord.AllowedMentions(roles=False),
        )
    except Exception as e:
        logging.error(f"Failed to set verified role: {e}")
        await interaction.followup.send(
            "❌ Failed to set verified role.",
            ephemeral=True,
        )


@bot.tree.command(
    name="check-setup",
    description="Check the configuration of the bot",
)
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
@logfire.instrument()
async def check_setup(interaction: discord.Interaction):
    assert interaction.guild is not None

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    results = []

    # Verified role is set
    verified_role = get_verified_role(guild)
    if verified_role:
        results.append(f"✅ Verified role is set ({verified_role.mention})")
    else:
        results.append("❌ Verified role is not set")

    # Verified role is lower than bot's top role
    if verified_role:
        if verified_role < guild.me.top_role:
            results.append("✅ Verified role is lower than bot's top role")
        else:
            results.append("❌ Verified role is higher than bot's top role")

    # Bot has Manage Roles permission
    if guild.me.guild_permissions.manage_roles:
        results.append("✅ Bot has `Manage Roles` permission")
    else:
        results.append("❌ Bot lacks `Manage Roles` permission")

    # Verification logs channel exists
    logs_channel = discord.utils.get(guild.text_channels, name="verification-logs")
    if logs_channel:
        results.append("✅ `#verification-logs` channel exists")
    else:
        results.append("❌ `#verification-logs` channel not found")

    # Bot can send messages in logs channel
    if logs_channel:
        if logs_channel.permissions_for(guild.me).send_messages:
            results.append("✅ Bot can send messages in `#verification-logs`")
        else:
            results.append("❌ Bot cannot send messages in `#verification-logs`")

    await interaction.followup.send(
        "\n".join(results), ephemeral=True, allowed_mentions=discord.AllowedMentions(roles=False)
    )


# Runs once on initial startup
@bot.event
async def setup_hook():
    current_hash = get_commands_hash(bot.tree)
    stored_hash = None
    # Using hashes avoids unecessary syncing
    if os.path.exists("cmd_hash.txt"):
        with open("cmd_hash.txt") as f:
            stored_hash = f.read().strip()

    if current_hash != stored_hash:
        await bot.tree.sync()
        with open("cmd_hash.txt", "w") as f:
            f.write(current_hash)
        logging.info("Commands synced (hash changed)")
    else:
        logging.info("Commands unchanged, skipping sync")


# Runs each time bot reconnects to a server
@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user}")

    if not config.MAILGUN_API_KEY:
        logging.warning("No Mailgun API key provided. OTPs will be logged to the console.")

    # Register the button view so it keeps working after a restart
    bot.add_view(VerifyButtonView())


bot.run(config.DISCORD_TOKEN, log_handler=None)
