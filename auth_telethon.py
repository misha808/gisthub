import asyncio
from telethon import TelegramClient

API_ID = 35460344
API_HASH = "acb3861c302dbef7c5edc5e7316944bd"
PHONE = "+380993694769"  # твій номер
PASSWORD = "brawl1456"  # двофакторний пароль

async def main():
    client = TelegramClient('hubgift_session', API_ID, API_HASH)
    await client.start(phone=PHONE, password=PASSWORD)
    me = await client.get_me()
    print(f"Авторизовано як: {me.first_name}")
    await client.disconnect()

asyncio.run(main())