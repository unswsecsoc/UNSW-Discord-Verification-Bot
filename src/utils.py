import logging
import os

import discord
import logfire

import config


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
