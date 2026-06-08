import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 34537170  # встав свій
API_HASH = "41711038f3c2952df1b3610b1b47443b"  # встав свій

async def main():
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start()
    print("\n\n✅ SESSION STRING:")
    print(client.session.save())
    print("\nСкопіюй цей рядок в Railway як SESSION_STRING\n")
    await client.disconnect()

asyncio.run(main())
