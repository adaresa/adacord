import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import logging
from commands import setup_all_commands

# Attempt to load opus early for voice stability
try:
    if not discord.opus.is_loaded():
        discord.opus.load_opus('libopus.so.0')
except Exception as e:
    logging.getLogger(__name__).warning(f"Could not pre-load opus library: {e}")

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

LOG_LEVEL = os.getenv('LOG_LEVEL', 'WARNING').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8', mode='a')
    ]
)
logging.getLogger('discord').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

if not TOKEN:
    logger.error("ERROR: DISCORD_TOKEN not found in .env file!")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="/", 
    intents=intents,
    help_command=None  # Disable default help for cleaner slash commands
)

@bot.event
async def on_ready():
    logger.info(f'🤖 Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info(f'📊 Connected to {len(bot.guilds)} guild(s)')
    if discord.opus.is_loaded():
        logger.info("🎧 Opus loaded successfully for voice.")
    else:
        logger.warning("⚠️ Opus not loaded; voice playback may fail.")

    # Set presence
    activity = discord.CustomActivity(name="kassu's discord bot")
    await bot.change_presence(activity=activity, status=discord.Status.online)
    
    logger.info("✅ Bot is ready!")

@bot.event
async def on_application_command_error(ctx, error):
    """Enhanced error handling for slash commands"""
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.respond(f"⏱️ Command is on cooldown. Try again in {error.retry_after:.2f}s", ephemeral=True)
    elif isinstance(error, commands.MissingPermissions):
        await ctx.respond("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        logger.error(f"Unhandled command error: {error}", exc_info=error)
        await ctx.respond("❌ An unexpected error occurred.", ephemeral=True)

# Register all commands
setup_all_commands(bot)

if __name__ == "__main__":
    try:
        logger.info("🚀 Starting bot...")
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.error("❌ Invalid bot token!")
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}", exc_info=e)
