from pyrogram import Client

API_ID = 36954581
API_HASH = "fa0d629367552da18ec8db6430f2a620"

app = Client("gift_checker_session", api_id=API_ID, api_hash=API_HASH)

with app:
    print("Авторизація успішна!")