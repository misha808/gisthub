import asyncio
from telethon import TelegramClient, events

API_ID = 36954581
API_HASH = "fa0d629367552da18ec8db6430f2a620"

async def main():
    client = TelegramClient('auth', API_ID, API_HASH)
    await client.start()
    print("Слухаю повідомлення...")

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        msg = event.message
        print(f"Від: {event.sender_id}")
        print(f"Всі дані: {msg.to_dict()}")
        print("---")
    @client.on(events.Raw())
    async def raw_handler(update):
        print(f"RAW: {update}")
    await asyncio.Event().wait()

asyncio.run(main())