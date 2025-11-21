import asyncio
import contextlib
import logging
import uvicorn
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, PreCheckoutQuery, ContentType
from sqlalchemy.future import select

from config import BOT_TOKEN, CREATOR_ID, WEBAPP_URL
from db import init_db, async_session
from models import User
from web_server import app as fastapi_app
from giveaway import register_giveaway_handlers
from creator import register_creator_handlers


logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)


async def check_subscription_to_channel(bot: Bot, user_id: int, channel_username: str) -> bool:
    """Проверяет подписку пользователя на канал"""
    try:
        member = await bot.get_chat_member(channel_username, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.warning(f"Ошибка проверки подписки на {channel_username}: {e}")
        return False

@dp.callback_query_handler(lambda c: c.data == 'check_subscription')
async def check_subscription_callback_handler(callback_query: types.CallbackQuery):
    """Обработчик кнопки 'Проверить' подписку - работает идентично /start"""
    logging.info(f"🔔 Callback получен: {callback_query.data} от пользователя {callback_query.from_user.id}")
    
    await callback_query.answer()  # Убираем индикатор загрузки
    
    telegram_id = callback_query.from_user.id
    username = callback_query.from_user.username or callback_query.from_user.full_name
    
    # Используем ту же логику, что и в cmd_start
    # Сначала проверяем подписку на обязательный канал (кроме создателя)
    channel_username = "@monkeys_giveaways"
    is_subscribed = True  # По умолчанию для создателя
    
    if telegram_id != CREATOR_ID:
        import time as _time
        subscription_check_started = _time.perf_counter()
        logging.info(f"🔍 Проверка подписки для пользователя {telegram_id} ({username}) при нажатии 'Проверить'")
        is_subscribed = await check_subscription_to_channel(bot, telegram_id, channel_username)
        logging.info(
            "⏱️ Subscription check for %s via callback took %.2f s",
            telegram_id,
            _time.perf_counter() - subscription_check_started,
        )
        logging.info(f"📊 Результат проверки подписки при 'Проверить' для {telegram_id}: {is_subscribed}")
    
    if not is_subscribed:
        # Если не подписан - обновляем сообщение с кнопкой подписки
        channel_url = "https://t.me/monkeys_giveaways"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url)]
            ]
        )
        
        try:
            await callback_query.message.edit_text(
                f"⚠️ Для пользования ботом необходимо подписаться на канал {channel_username}\n\n"
                f"Пожалуйста, подпишитесь на канал и отправьте команду /start еще раз.",
                reply_markup=kb
            )
        except Exception as e:
            logging.error(f"Ошибка при редактировании сообщения: {e}")
            await callback_query.message.answer(
                f"⚠️ Для пользования ботом необходимо подписаться на канал {channel_username}\n\n"
                f"Пожалуйста, подпишитесь на канал и отправьте команду /start еще раз.",
                reply_markup=kb
            )
        return

    # Если подписан - удаляем старое сообщение и показываем стандартное приветствие
    try:
        await callback_query.message.delete()
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение: {e}")
    
    # Создаем/обновляем пользователя и показываем стандартное сообщение
    import time as _time
    db_started = _time.perf_counter()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()

        if not user:
            role = "creator" if telegram_id == CREATOR_ID else "user"
            user = User(telegram_id=telegram_id, role=role, username=username)
            session.add(user)
            await session.commit()
            logging.info(f"👤 Новый пользователь добавлен: {username} (ID: {telegram_id}, роль: {role})")
        else:
            # Обновляем username если он изменился
            if user.username != username:
                user.username = username
                await session.commit()
            logging.info(f"👤 Пользователь уже существует: {username} (ID: {telegram_id}, роль: {user.role})")
    logging.info("⏱️ DB block for %s via callback took %.2f s", telegram_id, _time.perf_counter() - db_started)

    import time
    import os
    try:
        index_path = os.path.join(os.path.dirname(__file__), "index.html")
        if os.path.exists(index_path):
            cache_buster = int(os.path.getmtime(index_path))
        else:
            cache_buster = int(time.time())
    except:
        cache_buster = int(time.time())
    logging.info("WEBAPP_URL env value: %s", WEBAPP_URL)
    web_app_url = f"{WEBAPP_URL}?tg_id={telegram_id}&_v={cache_buster}"
    logging.info("Constructed WebApp URL for %s: %s", telegram_id, web_app_url)
    web_app = WebAppInfo(url=web_app_url)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть App", web_app=web_app)]
        ]
    )
    try:
        logging.info("Inline keyboard payload for %s: %s", telegram_id, kb.to_python())
    except Exception as log_err:
        logging.warning("Failed to serialize inline keyboard for %s: %s", telegram_id, log_err)

    await callback_query.message.answer(
        f"👋 Привет, {username}!\nТы успешно авторизован как *{user.role}*.",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    telegram_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    # Сначала проверяем подписку на обязательный канал (кроме создателя)
    channel_username = "@monkeys_giveaways"
    is_subscribed = True  # По умолчанию для создателя
    
    if telegram_id != CREATOR_ID:
        import time as _time
        subscription_check_started = _time.perf_counter()
        logging.info(f"🔍 Проверка подписки для пользователя {telegram_id} ({username}) при /start")
        is_subscribed = await check_subscription_to_channel(bot, telegram_id, channel_username)
        logging.info(
            "⏱️ Subscription check for %s took %.2f s",
            telegram_id,
            _time.perf_counter() - subscription_check_started,
        )
        logging.info(f"📊 Результат проверки подписки при /start для {telegram_id}: {is_subscribed}")
    
    if not is_subscribed:
        # Если не подписан - показываем сообщение с кнопкой подписки
        channel_url = "https://t.me/monkeys_giveaways"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=channel_url)]
            ]
        )
        
        await message.answer(
            f"⚠️ Для пользования ботом необходимо подписаться на канал {channel_username}\n\n"
            f"Пожалуйста, подпишитесь на канал и отправьте команду /start еще раз.",
            reply_markup=kb
        )
        return

    # Если подписан - создаем/обновляем пользователя и показываем стандартное сообщение
    import time as _time
    db_started = _time.perf_counter()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalars().first()

        if not user:
            role = "creator" if telegram_id == CREATOR_ID else "user"
            user = User(telegram_id=telegram_id, role=role, username=username)
            session.add(user)
            await session.commit()
            logging.info(f"👤 Новый пользователь добавлен: {username} (ID: {telegram_id}, роль: {role})")
        else:
            # Обновляем username если он изменился
            if user.username != username:
                user.username = username
                await session.commit()
            logging.info(f"👤 Пользователь уже существует: {username} (ID: {telegram_id}, роль: {user.role})")
    logging.info("⏱️ DB block for %s took %.2f s", telegram_id, _time.perf_counter() - db_started)

    import time
    import os
    try:
        index_path = os.path.join(os.path.dirname(__file__), "index.html")
        if os.path.exists(index_path):
            cache_buster = int(os.path.getmtime(index_path))
        else:
            cache_buster = int(time.time())
    except:
        cache_buster = int(time.time())
    logging.info("WEBAPP_URL env value: %s", WEBAPP_URL)
    web_app_url = f"{WEBAPP_URL}?tg_id={telegram_id}&_v={cache_buster}"
    logging.info("Constructed WebApp URL for %s: %s", telegram_id, web_app_url)
    web_app = WebAppInfo(url=web_app_url)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть App", web_app=web_app)]
        ]
    )
    try:
        logging.info("Inline keyboard payload for %s: %s", telegram_id, kb.to_python())
    except Exception as log_err:
        logging.warning("Failed to serialize inline keyboard for %s: %s", telegram_id, log_err)

    # Экранируем специальные символы Markdown
    safe_username = username.replace('*', '\\*').replace('_', '\\_').replace('[', '\\[').replace(']', '\\]')
    safe_role = user.role.replace('*', '\\*').replace('_', '\\_').replace('[', '\\[').replace(']', '\\]')
    
    await message.answer(
        f"👋 Привет, {safe_username}!\nТы успешно авторизован как *{safe_role}*.",
        parse_mode="Markdown",
        reply_markup=kb
    )


@dp.pre_checkout_query_handler(lambda query: True)
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """
    Обработчик предварительного запроса на оплату
    Критично для работы оплаты через Telegram Stars - должен отвечать быстро!
    """
    try:
        user_id = pre_checkout_query.from_user.id
        username = pre_checkout_query.from_user.username or pre_checkout_query.from_user.full_name or f"ID_{user_id}"
        payload = pre_checkout_query.invoice_payload
        amount = pre_checkout_query.total_amount
        currency = pre_checkout_query.currency
        
        logging.info(f"💳 Pre-checkout query получен: Пользователь {username} (ID: {user_id}) готов оплатить {amount} {currency}, payload: {payload}")
        
        # Немедленно подтверждаем готовность принять платеж
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
        logging.info(f"✅ Pre-checkout query подтвержден: Пользователь {username} (ID: {user_id}) может оплатить счет")
    except Exception as e:
        logging.error(f"❌ Ошибка при обработке pre-checkout query: {e}", exc_info=True)
        # В случае ошибки отклоняем запрос
        try:
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Ошибка обработки платежа")
        except:
            pass


@dp.message_handler(content_types=ContentType.SUCCESSFUL_PAYMENT)
async def process_successful_payment(message: types.Message):
    """
    Обработчик успешной оплаты через Telegram Stars
    """
    try:
        payment = message.successful_payment
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.full_name
        
        logging.info(f"✅ Успешная оплата получена от пользователя {username} (ID: {user_id})")
        logging.info(f"💰 Сумма: {payment.total_amount} {payment.currency}")
        logging.info(f"📦 Payload: {payment.invoice_payload}")
        
        # Парсим payload для получения информации о покупке
        try:
            import json
            # Payload может быть в формате JSON или строкой с JSON
            payload_str = payment.invoice_payload
            # Если payload содержит timestamp в конце, убираем его
            if '_' in payload_str:
                # Пытаемся найти последний JSON объект
                try:
                    payload_data = json.loads(payload_str)
                except:
                    # Если не получается, пробуем найти JSON перед timestamp
                    if '_' in payload_str:
                        parts = payload_str.rsplit('_', 1)
                        try:
                            payload_data = json.loads(parts[0])
                        except:
                            payload_data = {}
            else:
                payload_data = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
            
            category = payload_data.get("category")
            item_id = payload_data.get("item_id")
            payment_method = payload_data.get("payment_method", "stars")
            
            # Определяем название товара
            item_name = "Неизвестный товар"
            if category == "themes":
                if item_id == "kitty":
                    item_name = "Тема Kitty"
                elif item_id == "mario":
                    item_name = "Тема Mario"
            
            logging.info(f"🛒 Покупка: Пользователь {username} (ID: {user_id}) оплатил {payment.total_amount} {payment.currency} и получил {item_name} (категория: {category}, товар: {item_id}, метод оплаты: {payment_method})")
            
            # Сохраняем покупку в базу данных
            try:
                from db import async_session
                from models import User
                from sqlalchemy.future import select
                import json
                
                async with async_session() as session:
                    result = await session.execute(select(User).where(User.telegram_id == user_id))
                    user = result.scalars().first()
                    
                    if user:
                        # Получаем текущие покупки
                        purchased_items = None
                        if hasattr(user, 'purchased_items') and user.purchased_items:
                            try:
                                if isinstance(user.purchased_items, str):
                                    purchased_items = json.loads(user.purchased_items)
                                else:
                                    purchased_items = user.purchased_items
                            except:
                                purchased_items = {"themes": [], "avatarStars": [], "nftGifts": []}
                        else:
                            purchased_items = {"themes": [], "avatarStars": [], "nftGifts": []}
                        
                        # Добавляем покупку
                        if category not in purchased_items:
                            purchased_items[category] = []
                        
                        if item_id not in purchased_items[category]:
                            purchased_items[category].append(item_id)
                        
                        # Сохраняем в базу данных
                        user.purchased_items = json.dumps(purchased_items)
                        await session.commit()
                        
                        logging.info(f"✅ Покупка сохранена в БД: Пользователь {username} (ID: {user_id}) получил {item_name} (категория: {category}, товар: {item_id})")
            except Exception as e:
                logging.error(f"❌ Ошибка сохранения покупки в БД: {e}", exc_info=True)
            
        except Exception as e:
            logging.error(f"❌ Ошибка парсинга payload: {e}")
        
        # Отправляем подтверждение пользователю
        await message.answer(
            f"✅ **Оплата прошла успешно!**\n\n"
            f"Получено {payment.total_amount} ⭐\n"
            f"Ваш заказ обработан.",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logging.error(f"❌ Ошибка при обработке успешной оплаты: {e}", exc_info=True)


async def start_web_server():
    """Запускаем FastAPI сервер в том же event loop"""
    import os
    ssl_keyfile = os.getenv("SSL_KEYFILE", "ssl/key.pem")
    ssl_certfile = os.getenv("SSL_CERTFILE", "ssl/cert.pem")
    
    use_ssl = os.path.exists(ssl_keyfile) and os.path.exists(ssl_certfile)
    
    if use_ssl:
        print("🔒 WebApp доступен на https://0.0.0.0:8000")
        config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        )
    else:
        print("⚠️  SSL сертификаты не найдены. WebApp доступен на http://0.0.0.0:8000")
        print("💡 Для HTTPS создайте сертификаты: python generate_ssl.py")
        config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )
    
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot():
    await init_db()
    print("✅ База данных инициализирована")
    print("🤖 Запуск Telegram-бота...")
    
    # Callback handler для проверки подписки уже зарегистрирован через декоратор
    logging.info("✅ Callback handler для проверки подписки зарегистрирован через декоратор")
    
    # Регистрируем хендлеры из других модулей
    register_giveaway_handlers(dp)
    register_creator_handlers(dp)
    
    # Проверяем все активные конкурсы и собираем исторические комментарии
    from giveaway import check_all_giveaways_historical_comments
    check_bot = Bot(token=BOT_TOKEN)
    try:
        await check_all_giveaways_historical_comments(check_bot)
    finally:
        try:
            session = await check_bot.get_session()
            if session:
                await session.close()
        except:
            pass
    
    await dp.start_polling()


async def main():
    web_task = asyncio.create_task(start_web_server(), name="fastapi-server")
    try:
        await run_bot()
    finally:
        if not web_task.done():
            web_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await web_task


if __name__ == "__main__":
    asyncio.run(main())
