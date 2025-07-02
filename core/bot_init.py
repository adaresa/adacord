import discord
from discord.ext import commands
from config import logger

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
    # Set presence
    activity = discord.CustomActivity(name="kassu's discord bot")
    await bot.change_presence(activity=activity, status=discord.Status.online)
    logger.info("✅ Bot is ready!")

@bot.event
async def on_application_command_error(ctx, error):
    """Enhanced error handling for slash commands"""
    from discord.ext import commands as ext_commands
    if isinstance(error, ext_commands.CommandOnCooldown):
        await ctx.respond(f"⏱️ Command is on cooldown. Try again in {error.retry_after:.2f}s", ephemeral=True)
    elif isinstance(error, ext_commands.MissingPermissions):
        await ctx.respond("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        logger.error(f"Unhandled command error: {error}", exc_info=error)
        await ctx.respond("❌ An unexpected error occurred.", ephemeral=True)
