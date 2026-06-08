from telethon.sync import TelegramClient
from telethon.sessions import StringSession
client = TelegramClient(StringSession(), '34537170', '41711038f3c2952df1b3610b1b47443b')
client.start()
print(client.session.save())
client.disconnect()
