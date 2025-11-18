from aiogram import Dispatcher, types
from db import get_session
from models import User
from helpers import is_creator


async def add_admin(message: types.Message):
    if not is_creator(message.from_user.id):
        await message.answer("⛔ Только создатель может добавлять админов.")
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Используй: /add_admin <user_id>")
        return

    new_admin_id = int(args[1])

    async for session in get_session():
        existing = await session.scalar(
            session.query(User).filter(User.telegram_id == new_admin_id)
        )
        if existing:
            existing.role = "admin"
        else:
            session.add(User(telegram_id=new_admin_id, role="admin"))
        await session.commit()

    await message.answer(f"✅ Пользователь {new_admin_id} назначен администратором.")


def register_creator_handlers(dp: Dispatcher):
    """
    Регистрирует все хендлеры для aiogram 2.x
    """
    # Хендлер для команды /add_admin
    dp.register_message_handler(add_admin, commands=['add_admin'])
