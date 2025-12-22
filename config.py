import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CREATOR_ID = int(os.getenv("CREATOR_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///giveaway.db")

# Telethon конфигурация (для получения исторических комментариев)
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")  # Опционально, если нужна авторизация по телефону

# Платежная конфигурация
TON_WALLET = os.getenv("TON_WALLET", "UQBVTCudYNrI11UXM04V6Lq9KWc2HurxVr2BtkMeZfmyBvuC")  # Кошелек креатора по умолчанию  # Адрес TON кошелька креатора
CRYPTOBOT_API_TOKEN = os.getenv("CRYPTOBOT_API_TOKEN", "489033:AAwZuKfD6QyZ77ZVqJO34qYMGxOHTvFRbo0")  # Токен CryptoBot API
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"

# WebApp конфигурация (URL для Telegram WebApp)
# Используйте IP адрес вашего сервера с HTTPS (например: https://90.156.211.211)
# Или домен, если есть (например: https://yourdomain.com)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://90.156.211.211")

# See.tg API конфигурация для получения информации о NFT
SEE_TG_API_KEY = os.getenv("SEE_TG_API_KEY", "e9920398-1667-4f87-8b10-87f8ffda7d01:873a587224a276e557a0ee8871088a6748134807d7dd08e817ab89b21cd36e98")
