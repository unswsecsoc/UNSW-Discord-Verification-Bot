import hashlib
import json
import logging
import os
import sqlite3
from functools import lru_cache, wraps
from typing import TYPE_CHECKING, Any

import discord
import logfire
from discord.ext import commands

import config

if TYPE_CHECKING:
    from collections.abc import Callable

    from discord.app_commands import CommandTree


def get_guild_dir(guild: discord.Guild):
    return os.path.join(config.DB_DIR, str(guild.id))


def get_guild_db_path(guild: discord.Guild):
    return os.path.join(get_guild_dir(guild), "database.db")


# store the human-readable guild name in its data directory
def save_guild_info(guild: discord.Guild) -> None:
    guild_dir = get_guild_dir(guild)
    os.makedirs(guild_dir, exist_ok=True)
    info_file = os.path.join(guild_dir, "guild_name.txt")
    with open(info_file, "w") as f:
        f.write(guild.name)


@lru_cache(maxsize=16)
def get_guild_db(guild: discord.Guild):
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT
    """)
    conn.commit()
    logging.info(f"loaded or created database for guild {guild.id}")

    save_guild_info(guild)

    return conn


def get_verified_role(guild: discord.Guild) -> discord.Role | None:
    conn = get_guild_db(guild)
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = 'verified_role_id'")
    row = c.fetchone()

    if row is None:
        return None

    role_id = int(row[0])
    return guild.get_role(role_id)


def set_verified_role(guild: discord.Guild, role: discord.Role) -> None:
    conn = get_guild_db(guild)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO config (key, value)
        VALUES ('verified_role_id', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """,
        (str(role.id),),
    )
    conn.commit()


async def log_admin(message, guild, **kwargs):
    logfire.debug(f'log_admin: logging "{message}" to guild {guild.id}')

    channel = discord.utils.get(guild.text_channels, name="verification-logs")

    if channel is None:
        logging.info(f"No #verification-logs channel in guild {guild.name}")
        return

    if not channel.permissions_for(guild.me).send_messages:
        logging.info(f"Missing permission to send messages in #{channel.name}")
        return

    await channel.send(message, **kwargs)


def get_commands_hash(tree: CommandTree) -> str:
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


def modal_cooldown(times: int, seconds: float, key: Callable[[discord.Interaction], Any]):
    def decorator(func):
        _modal_cooldown = commands.CooldownMapping.from_cooldown(times, seconds, key)

        @wraps(func)
        async def wrapper(self, interaction: discord.Interaction):
            bucket = _modal_cooldown.get_bucket(interaction)
            assert bucket is not None
            retry_after = bucket.update_rate_limit()

            if retry_after is not None:
                logging.info(f"Rate limiting {interaction.user} for {retry_after} seconds")
                await interaction.response.send_message(
                    f"⏳ Too many requests. Try again in {int(retry_after)}s.",
                    ephemeral=True,
                )
                return

            return await func(self, interaction)

        return wrapper

    return decorator
