from config import TOKEN, logger
from core.bot_init import bot
from commands.setup import setup_all_commands

# Register all commands
setup_all_commands(bot)

if __name__ == "__main__":
    try:
        logger.info("🚀 Starting bot...")
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}", exc_info=e)
