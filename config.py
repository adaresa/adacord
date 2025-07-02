import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Discord token
TOKEN = os.getenv('DISCORD_TOKEN')

# Logging configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'WARNING').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8', mode='a')
    ]
)
logger = logging.getLogger(__name__)
