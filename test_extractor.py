import sys
import asyncio

from audio import extractor


async def main(query: str) -> None:
    try:
        url, headers = await extractor.get_stream_url(query)
        print("OK")
        print(url[:120])
        print("headers:", sorted(list(headers.keys())))
    except Exception as e:
        print("ERROR:", str(e))


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "petit biscuit sunset lover slowed"
    asyncio.run(main(q))


