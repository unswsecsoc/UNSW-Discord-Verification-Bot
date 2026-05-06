import os
import re
import discord
import config


def safe_guild_name(guild: discord.Guild) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", guild.name)


# TODO: Don't prepend DB_FOLDER here, do that later or rename function
def safe_guild_filename(guild: discord.Guild):
    return os.path.join(config.DB_FOLDER, f"{safe_guild_name(guild)}_{guild.id}.db")


async def log_admin(message, guild):
    channel = discord.utils.get(guild.text_channels, name="verification-logs")

    if channel is None:
        print(f"No #verification-logs channel in guild {guild.name}", file=sys.stderr)
        return

    if not channel.permissions_for(guild.me).send_messages:
        print(
            f"Missing permission to send messages in #{channel.name}", file=sys.stderr
        )
        return

    await channel.send(message)
