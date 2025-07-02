from .music import register_music_commands
from .queue import register_queue_commands

def setup_all_commands(bot):
    register_music_commands(bot)
    register_queue_commands(bot)

    # Register all music and queue commands here
    # Example: music.play, music.pause, queue.queue_cmd, etc. 