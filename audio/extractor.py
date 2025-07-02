import yt_dlp
import asyncio
from typing import Dict, Any

class AudioExtractor:
    def __init__(self):
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio/best',
            'noplaylist': True,
            'default_search': 'ytsearch1',
            'extractaudio': True,
            'audioformat': 'mp3',
            'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        }
    async def extract_info(self, query: str, download: bool = False) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: ydl.extract_info(query, download=download)),
                    timeout=15
                )
                return info
        except asyncio.TimeoutError:
            raise Exception("Request timed out")
        except Exception as e:
            raise Exception(f"Extraction failed: {str(e)[:100]}")
    async def get_stream_url(self, query: str) -> str:
        info = await self.extract_info(query, download=False)
        if 'entries' in info and info['entries']:
            entry = info['entries'][0]
        else:
            entry = info
        if 'url' in entry:
            return entry['url']
        formats = entry.get('formats', [])
        audio_formats = [f for f in formats if f.get('acodec') != 'none']
        if audio_formats:
            best_audio = max(audio_formats, key=lambda x: x.get('abr', 0) or 0)
            return best_audio['url']
        raise Exception("No audio stream found")

extractor = AudioExtractor()
