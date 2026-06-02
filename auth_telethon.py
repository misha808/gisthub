import asyncio
from telethon import TelegramClient

API_ID = 34537170
API_HASH = "41711038f3c2952df1b3610b1b47443b"


async def main():
    phone = input("Введи номер телефону (з +): ")
    client = TelegramClient('auth.session', API_ID, API_HASH)
    await client.connect()

    await client.send_code_request(phone)
    code = input("Введи код з Telegram: ")

    try:
        await client.sign_in(phone, code)
    except Exception:
        password = input("Введи пароль 2FA: ")
        await client.sign_in(password=password)

    me = await client.get_me()
    print(f"Авторизовано як: {me.first_name}")
    await client.disconnect()


asyncio.run(main())