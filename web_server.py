from models import User, Giveaway, Message, Winner, Participant
from sqlalchemy import insert, update, text, func
from datetime import datetime, timezone
from fastapi import Request, HTTPException
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, Response
from fastapi import UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Optional, Union
import hashlib
from sqlalchemy.future import select
from db import async_session, init_db, IS_SQLITE
from models import User
from config import CREATOR_ID, BOT_TOKEN, TON_WALLET, CRYPTOBOT_API_TOKEN, CRYPTOBOT_API_URL
import cryptobot
import pytz
import os
import json
import asyncio
import time
import mimetypes
from aiogram import Bot
from giveaway import select_winners_from_contest, reroll_single_winner, confirm_winners
import re
import logging
import tempfile
import io
try:
    from aiogram.types import FSInputFile
except ImportError:
    FSInputFile = None
try:
    from aiogram.types import BufferedInputFile
except ImportError:
    BufferedInputFile = None

logger = logging.getLogger(__name__)
MSK_TZ = pytz.timezone('Europe/Moscow')

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan-хук для инициализации БД при старте FastAPI"""
    await init_db()
    logger.info("✅ База данных инициализирована при запуске веб-сервера")
    yield

app = FastAPI(lifespan=lifespan)

ROOT_DIR = os.path.dirname(__file__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------- WEB -------------------

def get_file_with_no_cache(file_path: str) -> FileResponse:
    """Возвращает FileResponse с заголовками для предотвращения кэширования"""
    response = FileResponse(file_path)
    # Добавляем заголовки для предотвращения кэширования
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    # Добавляем ETag на основе времени изменения файла
    if os.path.exists(file_path):
        mtime = os.path.getmtime(file_path)
        etag = hashlib.md5(f"{file_path}{mtime}".encode()).hexdigest()
        response.headers["ETag"] = etag
    return response

@app.get("/")
async def root():
    """Главная страница WebApp"""
    index_path = os.path.join(ROOT_DIR, "index.html")
    return get_file_with_no_cache(index_path)

@app.get("/creator.html")
async def get_creator():
    return get_file_with_no_cache(os.path.join(ROOT_DIR, "creator.html"))

@app.get("/admin.html")
async def get_admin():
    return get_file_with_no_cache(os.path.join(ROOT_DIR, "admin.html"))

@app.get("/user.html")
async def get_user():
    return get_file_with_no_cache(os.path.join(ROOT_DIR, "user.html"))

@app.get("/style.css")
async def get_css():
    """CSS напрямую из корня"""
    return get_file_with_no_cache(os.path.join(ROOT_DIR, "style.css"))

@app.get("/script.js")
async def get_js():
    """JS напрямую из корня"""
    return get_file_with_no_cache(os.path.join(ROOT_DIR, "script.js"))


# ------------------- API -------------------

def to_msk_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Преобразует datetime в naive формат МСК (UTC+3) для хранения в БД."""
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(MSK_TZ).replace(tzinfo=None)


def _as_datetime(value: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Преобразует строку или datetime в объект datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        clean = value.strip()
        if not clean:
            return None
        clean = clean.replace('Z', '+00:00') if clean.endswith('Z') else clean
        try:
            return datetime.fromisoformat(clean)
        except Exception:
            return None
    return None


def to_iso(value: Optional[Union[str, datetime]]) -> Optional[str]:
    """Возвращает ISO-строку в МСК (UTC+3)."""
    dt = _as_datetime(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = MSK_TZ.localize(dt)
    else:
        dt = dt.astimezone(MSK_TZ)
    return dt.isoformat()


def to_datetime_local(value: Optional[Union[str, datetime]]) -> Optional[str]:
    """Возвращает строку для input[type=datetime-local] (МСК)."""
    dt = _as_datetime(value)
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = MSK_TZ.localize(dt)
    else:
        dt = dt.astimezone(MSK_TZ)
    return dt.strftime('%Y-%m-%dT%H:%M')

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "FastAPI работает 🚀"}

async def check_subscription_to_channel_web(user_id: int, channel_username: str) -> bool:
    """Проверяет подписку пользователя на канал (для веб-сервера)"""
    bot = None
    try:
        bot = Bot(token=BOT_TOKEN)
        # Добавляем таймаут 5 секунд для проверки подписки
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(channel_username, user_id),
                timeout=5.0
            )
            return member.status in ['member', 'administrator', 'creator']
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут при проверке подписки на {channel_username} для пользователя {user_id}")
            # При таймауте считаем, что пользователь подписан (чтобы не блокировать доступ)
            return True
    except Exception as e:
        logger.warning(f"Ошибка проверки подписки на {channel_username}: {e}")
        # При ошибке считаем, что пользователь подписан (чтобы не блокировать доступ)
        return True
    finally:
        if bot:
            try:
                session = await bot.get_session()
                if session:
                    await session.close()
            except Exception as e:
                logger.warning(f"Ошибка при закрытии сессии бота: {e}")

@app.get("/api/auth")
async def auth_user(tg_id: int = Query(...)):
    try:
        logger.info(f"🔐 Запрос авторизации для пользователя {tg_id}")
        
        # Получаем username из Telegram Bot API
        username = None
        bot = None
        try:
            bot = Bot(token=BOT_TOKEN)
            # Для пользователей используем get_chat_member или get_chat
            try:
                user_info = await asyncio.wait_for(bot.get_chat(tg_id), timeout=5.0)
                username = getattr(user_info, 'username', None) or getattr(user_info, 'first_name', None)
            except asyncio.TimeoutError:
                logger.warning(f"Таймаут получения данных пользователя {tg_id} через Bot API, пропускаем username")
            except Exception as inner_exc:
                logger.warning(f"Не удалось получить username пользователя {tg_id}: {inner_exc}")
        except Exception as e:
            logger.warning(f"Не удалось инициализировать бота для пользователя {tg_id}: {e}")
        finally:
            if bot:
                try:
                    session_bot = await bot.get_session()
                    if session_bot:
                        await session_bot.close()
                except Exception as close_exc:
                    logger.warning(f"Ошибка при закрытии сессии бота (auth_user): {close_exc}")
        
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == tg_id))
            user = result.scalars().first()

            # Bootstrap creator on first login if needed
            if not user and tg_id == CREATOR_ID:
                logger.info(f"👤 Создание пользователя-создателя {tg_id}")
                user = User(telegram_id=tg_id, role="creator", username=username, created_at=datetime.now(timezone.utc))
                session.add(user)
                await session.commit()

            if not user:
                logger.warning(f"❌ Пользователь {tg_id} не найден")
                return {"authorized": False, "message": "Пользователь не найден"}

            # Обновляем username если он изменился или отсутствует
            if username and (not user.username or user.username != username):
                user.username = username
                await session.commit()
                logger.info(f"✅ Обновлен username для пользователя {tg_id}: {username}")

            logger.info(f"✅ Пользователь {tg_id} найден, роль: {user.role}")

            # Проверяем подписку на обязательный канал (кроме создателя)
            channel_username = "@monkeys_giveaways"
            is_subscribed = True  # По умолчанию для создателя
            
            if tg_id != CREATOR_ID:
                logger.info(f"🔍 Проверка подписки для пользователя {tg_id} при входе в приложение")
                try:
                    # Добавляем общий таймаут для всей проверки подписки
                    is_subscribed = await asyncio.wait_for(
                        check_subscription_to_channel_web(tg_id, channel_username),
                        timeout=5.0  # Уменьшаем таймаут до 5 секунд
                    )
                    logger.info(f"📊 Результат проверки подписки при входе в приложение для {tg_id}: {is_subscribed}")
                    
                    if not is_subscribed:
                        logger.warning(f"⚠️ Пользователь {tg_id} не подписан на канал {channel_username}")
                        return {
                            "authorized": False,
                            "message": f"Для пользования приложением необходимо подписаться на канал {channel_username}. Пожалуйста, подпишитесь и отправьте команду /start в боте."
                        }
                except asyncio.TimeoutError:
                    logger.warning(f"⏰ Таймаут при проверке подписки для пользователя {tg_id}, разрешаем доступ")
                    # При таймауте разрешаем доступ, чтобы не блокировать пользователя
                    is_subscribed = True
                except Exception as e:
                    logger.error(f"❌ Критическая ошибка при проверке подписки для {tg_id}: {e}", exc_info=True)
                    # При критической ошибке разрешаем доступ
                    is_subscribed = True

            logger.info(f"✅ Авторизация успешна для пользователя {tg_id}, роль: {user.role}")
            return {
                "authorized": True,
                "telegram_id": user.telegram_id,
                "role": user.role,
            }
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в auth_user для {tg_id}: {e}", exc_info=True)
        # В случае критической ошибки возвращаем отказ в доступе
        return {
            "authorized": False,
            "message": "Ошибка сервера при проверке доступа. Попробуйте позже."
        }

# ------------------- USERS / ADMINS API -------------------

@app.get("/api/admins")
async def list_admins():
    async with async_session() as session:
        result = await session.execute(select(User).where(User.role == "admin"))
        admins = result.scalars().all()
        return [{"id": u.telegram_id, "role": u.role} for u in admins]

@app.post("/api/admins")
async def add_admin(request: Request):
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # Проверяем наличие id
    if "id" not in data or data.get("id") is None:
        raise HTTPException(status_code=400, detail="id is required")
    
    # Преобразуем id в integer
    try:
        id_value = data.get("id")
        # Если это строка, проверяем что она не пустая
        if isinstance(id_value, str) and not id_value.strip():
            raise ValueError("ID cannot be empty")
        tg_id = int(id_value)
        if tg_id <= 0:
            raise ValueError("ID must be positive")
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"id must be a positive integer: {str(e)}")

    channel_link = data.get("channel_link", "").strip() or None
    chat_link = data.get("chat_link", "").strip() or None

    try:
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == tg_id))
            user = result.scalars().first()
            if user:
                user.role = "admin"
                if channel_link:
                    user.channel_link = channel_link
                if chat_link:
                    user.chat_link = chat_link
            else:
                user = User(
                    telegram_id=tg_id, 
                    role="admin", 
                    created_at=datetime.now(timezone.utc),
                    channel_link=channel_link,
                    chat_link=chat_link
                )
                session.add(user)
            await session.commit()
        return {"success": True, "message": f"Admin {tg_id} added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/api/profile")
async def get_profile(tg_id: int = Query(None)):
    """Получить профиль пользователя с опытом из базы данных"""
    if tg_id is None:
        return {}
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalars().first()
        if not user and tg_id == CREATOR_ID:
            user = User(telegram_id=tg_id, role="creator", created_at=datetime.now(timezone.utc))
            session.add(user)
            await session.commit()
        if not user:
            return {}
        # Получаем опыт из базы данных
        experience = user.experience if hasattr(user, 'experience') and user.experience is not None else 0
        
        # Подсчитываем статистику участий и побед
        from models import Participant, Winner
        contests_participated = 0
        contests_won = 0
        
        # Подсчитываем участия и победы
        if user.role == 'user':
            # Для пользователей считаем участия в конкурсах рисунков/коллекций
            participants_result = await session.execute(
                select(Participant).where(Participant.user_id == user.telegram_id)
            )
            participants = participants_result.scalars().all()
            
            # Получаем типы конкурсов для каждого участия
            for participant in participants:
                giveaway_result = await session.execute(
                    select(Giveaway).where(Giveaway.id == participant.giveaway_id)
                )
                giveaway = giveaway_result.scalars().first()
                if giveaway:
                    contest_type = getattr(giveaway, 'contest_type', 'random_comment')
                    # Для рисунков/коллекций считаем участие только если есть фото/коллекция
                    if contest_type in ['drawing', 'collection']:
                        if participant.photo_link:
                            contests_participated += 1
            
            # Для рандом соо считаем участие по комментариям в таблице Comment
            from models import Comment
            comments_result = await session.execute(
                select(Comment).where(Comment.user_id == user.telegram_id)
            )
            comments = comments_result.scalars().all()
            
            # Получаем уникальные конкурсы, в которых пользователь оставил комментарий
            commented_contest_ids = set()
            for comment in comments:
                # Находим конкурс по post_link
                if comment.chat_id and comment.post_message_id:
                    # Ищем конкурс с таким post_link
                    giveaways_result = await session.execute(
                        select(Giveaway).where(
                            Giveaway.contest_type == 'random_comment'
                        )
                    )
                    all_giveaways = giveaways_result.scalars().all()
                    
                    for giveaway in all_giveaways:
                        if not giveaway.post_link:
                            continue
                        # Парсим post_link конкурса
                        from post_parser import parse_telegram_link
                        parsed = parse_telegram_link(giveaway.post_link)
                        if parsed:
                            channel_id, post_message_id = parsed
                            if str(channel_id) == str(comment.chat_id) and post_message_id == comment.post_message_id:
                                commented_contest_ids.add(giveaway.id)
            
            contests_participated += len(commented_contest_ids)
            
            # Подсчитываем победы
            winners_result = await session.execute(
                select(Winner).where(Winner.user_id == user.telegram_id)
            )
            contests_won = len(winners_result.scalars().all())
        
        # Получаем купленные товары
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
        
        return {
            "id": user.telegram_id,
            "status": user.role,
            "username": user.username if hasattr(user, 'username') else None,
            "first_login": user.created_at.isoformat() if user.created_at else None,
            "channel_link": user.channel_link if hasattr(user, 'channel_link') else None,
            "chat_link": user.chat_link if hasattr(user, 'chat_link') else None,
            "experience": experience,
            "contests_participated": contests_participated,
            "contests_won": contests_won,
            "ton_wallet": user.ton_wallet if hasattr(user, 'ton_wallet') else None,
            "purchased_items": purchased_items
        }

# ------------------- Payment API -------------------

@app.get("/api/payment/get-ton-wallet")
async def get_ton_wallet(tg_id: int = Query(None)):
    """Получить адрес TON кошелька пользователя или креатора"""
    if tg_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == tg_id)
            )
            user = result.scalars().first()
            if user and user.ton_wallet:
                return {"wallet": user.ton_wallet}
    # Если у пользователя нет кошелька, возвращаем кошелек креатора
    return {"wallet": TON_WALLET}

@app.post("/api/payment/set-ton-wallet")
async def set_ton_wallet(request: Request):
    """Сохранить TON кошелек пользователя"""
    try:
        data = await request.json()
        tg_id = data.get("tg_id")
        wallet = data.get("wallet", "").strip()
        
        if not tg_id:
            raise HTTPException(status_code=400, detail="tg_id обязателен")
        
        # Если wallet пустой, это означает отключение кошелька
        if wallet:
            # Простая валидация формата TON адреса (начинается с UQ или EQ)
            if not (wallet.startswith("UQ") or wallet.startswith("EQ") or wallet.startswith("0:")):
                raise HTTPException(status_code=400, detail="Неверный формат TON адреса")
        
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == tg_id)
            )
            user = result.scalars().first()
            
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
            
            user.ton_wallet = wallet if wallet else None
            await session.commit()
            
            if wallet:
                logger.info(f"✅ TON кошелек сохранен для пользователя {tg_id}: {wallet}")
                return {"success": True, "message": "TON кошелек успешно сохранен", "wallet": wallet}
            else:
                logger.info(f"✅ TON кошелек отключен для пользователя {tg_id}")
                return {"success": True, "message": "TON кошелек отключен", "wallet": None}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при сохранении TON кошелька: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении кошелька: {str(e)}")

@app.get("/api/payment/get-creator-id")
async def get_creator_id():
    """Получить ID креатора для отправки подарков"""
    return {"creator_id": str(CREATOR_ID)}

@app.post("/api/payment/create-stars-invoice")
async def create_stars_invoice(request: Request):
    """
    Создать invoice для оплаты через Telegram Stars
    
    Принимает:
    - title: название товара
    - description: описание товара
    - amount: количество звезд
    - user_id: ID пользователя Telegram
    - category: категория товара (themes, etc.)
    - item_id: ID товара
    """
    try:
        data = await request.json()
        title = data.get("title")
        description = data.get("description", "")
        amount = data.get("amount")
        user_id = data.get("user_id")
        category = data.get("category")
        item_id = data.get("item_id")
        
        logger.info(f"📋 Запрос на создание счета: title={title}, amount={amount}, user_id={user_id}, category={category}, item_id={item_id}")
        
        if not title or not amount or not user_id:
            error_msg = "Необходимо указать title, amount и user_id"
            logger.error(f"❌ {error_msg}")
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Создаем payload для отслеживания платежа
        payload_data = {
            "category": category,
            "item_id": item_id,
            "user_id": str(user_id),
            "payment_method": "stars"
        }
        # Генерируем уникальный payload для каждого счета (добавляем timestamp)
        unique_payload = f"{json.dumps(payload_data)}_{int(time.time())}"
        start_param = f"shop_{category}_{item_id}_stars_{int(time.time())}"
        
        # Создаем invoice через бота - отправляем счет пользователю в чат
        bot = Bot(token=BOT_TOKEN)
        try:
            from aiogram.types import LabeledPrice
            
            # Для Stars amount передается напрямую (не в копейках)
            prices = [LabeledPrice(label=title, amount=int(amount))]
            
            # Получаем username пользователя для логов
            try:
                user_info = await bot.get_chat(user_id)
                username = user_info.username or user_info.first_name or f"ID_{user_id}"
            except:
                username = f"ID_{user_id}"
            
            # Отправляем invoice пользователю через бота
            message = await bot.send_invoice(
                chat_id=user_id,
                title=title,
                description=description,
                payload=unique_payload,
                provider_token="",  # Для Stars не нужен
                currency="XTR",  # Telegram Stars
                prices=prices,
                start_parameter=start_param
            )
            
            # Логируем создание счета
            logger.info(f"📋 Счет создан: Пользователь {username} (ID: {user_id}) получил счет на {amount} ⭐ за покупку {title} (категория: {category}, товар: {item_id})")
            
            # Сохраняем результат ПЕРЕД любыми дополнительными операциями
            invoice_id = str(message.message_id) if hasattr(message, 'message_id') else None
            result = {
                "success": True,
                "message": "Счет отправлен в бота",
                "invoice_id": invoice_id
            }
            
            logger.info(f"✅ Счет успешно отправлен. Invoice ID: {invoice_id}")
            
            # Сохраняем результат в переменную перед finally
            final_result = {
                "success": True,
                "message": "Счет отправлен в бота",
                "invoice_id": invoice_id
            }
            
            logger.info(f"✅ Возвращаем успешный ответ: {final_result}")
            
            # Закрываем сессию перед возвратом, чтобы избежать проблем с async
            try:
                session = await bot.get_session()
                if session:
                    await session.close()
                    logger.debug("✅ Сессия бота закрыта успешно")
            except Exception as close_error:
                # Игнорируем ошибки закрытия сессии - счет уже отправлен
                logger.debug(f"⚠️ Ошибка при закрытии сессии бота (не критично): {close_error}")
            
            return final_result
            
        except HTTPException as http_ex:
            # Закрываем сессию перед повторным выбросом
            try:
                session = await bot.get_session()
                if session:
                    await session.close()
            except:
                pass
            raise http_ex
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке invoice пользователю {user_id}: {e}", exc_info=True)
            # Закрываем сессию перед возвратом ошибки
            try:
                session = await bot.get_session()
                if session:
                    await session.close()
            except:
                pass
            raise HTTPException(status_code=500, detail=f"Ошибка при отправке счета: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при создании Stars invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при создании invoice: {str(e)}")

@app.post("/api/payment/create-invoice")
async def create_invoice(request: Request):
    """
    Создать счет на оплату через CryptoBot
    
    Принимает:
    - amount: сумма оплаты
    - currency: валюта (TON, BTC, ETH, USDT, USDC, BUSD)
    - description: описание платежа
    - user_id: ID пользователя Telegram
    - category: категория товара (themes, etc.)
    - item_id: ID товара
    """
    try:
        data = await request.json()
        amount = data.get("amount")
        currency = data.get("currency", "TON")
        description = data.get("description", "")
        user_id = data.get("user_id")
        category = data.get("category")
        item_id = data.get("item_id")
        
        if not amount or not user_id:
            raise HTTPException(status_code=400, detail="Необходимо указать amount и user_id")
        
        # Создаем payload для отслеживания платежа
        # В payload сохраняем user_id для проверки принадлежности счета
        payload_data = {
            "category": category,
            "item_id": item_id,
            "user_id": str(user_id),  # Преобразуем в строку для надежности
            "currency": currency,
            "amount": amount
        }
        
        # Создаем payload для отслеживания платежа
        payload_str = json.dumps(payload_data)
        
        # Добавляем информацию о пользователе в описание
        # Это поможет понять, кто должен оплатить счет
        description_with_user = f"{description}\n\n👤 Счет для пользователя ID: {user_id}"
        
        # Создаем счет через CryptoBot
        invoice = await cryptobot.create_invoice(
            amount=amount,
            currency=currency,
            description=description_with_user,
            user_id=user_id,
            payload=payload_str
        )
        
        if "error" in invoice:
            raise HTTPException(status_code=500, detail=f"Ошибка создания счета: {invoice.get('error')}")
        
        # Сохраняем информацию о счете для последующей проверки
        invoice_id = invoice.get("invoice_id")
        invoice_url = invoice.get("pay_url")
        
        return {
            "success": True,
            "invoice_id": invoice_id,
            "invoice_url": invoice_url,
            "payload": json.dumps(payload_data)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при создании счета: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при создании счета: {str(e)}")

@app.post("/api/payment/verify")
async def verify_payment(request: Request):
    """
    Проверка оплаты на сервере через CryptoBot
    
    Принимает invoice_id и проверяет его статус и принадлежность пользователю
    """
    try:
        data = await request.json()
        invoice_id = data.get("invoice_id")
        category = data.get("category")
        item_id = data.get("itemId")
        user_id = data.get("userId")
        
        if not invoice_id or not user_id:
            raise HTTPException(status_code=400, detail="Необходимо указать invoice_id и userId")
        
        # Проверяем статус счета через CryptoBot API
        verification_result = await cryptobot.verify_payment(invoice_id)
        
        if "error" in verification_result:
            logger.warning(f"❌ Ошибка получения информации о счете: {verification_result.get('error')}")
            return {"verified": False, "message": "Ошибка получения информации о счете"}
        
        is_paid = verification_result.get("paid", False)
        payload = verification_result.get("payload")
        invoice = verification_result.get("invoice", {})
        
        # Проверяем, что счет оплачен
        if not is_paid:
            logger.warning(f"❌ Счет не оплачен: invoice_id {invoice_id}")
            return {"verified": False, "message": "Счет не оплачен"}
        
        # Проверяем, что счет принадлежит правильному пользователю
        if payload:
            payload_user_id = payload.get("user_id")
            if payload_user_id and int(payload_user_id) != int(user_id):
                logger.warning(f"❌ Счет оплачен другим пользователем: invoice_id {invoice_id}, ожидался user_id {user_id}, получен {payload_user_id}")
                return {"verified": False, "message": "Счет принадлежит другому пользователю"}
            
            # Проверяем соответствие категории и товара
            payload_category = payload.get("category")
            payload_item_id = payload.get("item_id")
            if category and payload_category != category:
                logger.warning(f"❌ Несоответствие категории: invoice_id {invoice_id}, ожидалась {category}, получена {payload_category}")
                return {"verified": False, "message": "Несоответствие данных счета"}
            if item_id and str(payload_item_id) != str(item_id):
                logger.warning(f"❌ Несоответствие товара: invoice_id {invoice_id}, ожидался {item_id}, получен {payload_item_id}")
                return {"verified": False, "message": "Несоответствие данных счета"}
        else:
            # Если payload отсутствует, проверяем по invoice (может содержать информацию о пользователе)
            logger.warning(f"⚠️ Payload отсутствует в счете: invoice_id {invoice_id}")
            # В этом случае полагаемся на проверку статуса оплаты
            # Но лучше всегда использовать payload
        
        logger.info(f"✅ Оплата подтверждена: invoice_id {invoice_id}, пользователь {user_id}, товар {category}/{item_id}")
        return {"verified": True, "message": "Оплата подтверждена"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при проверке оплаты: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при проверке оплаты: {str(e)}")

@app.get("/api/payment/purchased-items")
async def get_purchased_items(tg_id: int = Query(...)):
    """Получить список купленных товаров пользователя"""
    try:
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == tg_id))
            user = result.scalars().first()
            
            if not user:
                return {"purchased_items": {"themes": [], "avatarStars": [], "nftGifts": []}}
            
            # Получаем купленные товары
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
            
            return {"purchased_items": purchased_items}
    except Exception as e:
        logger.error(f"Ошибка при получении покупок: {e}", exc_info=True)
        return {"purchased_items": {"themes": [], "avatarStars": [], "nftGifts": []}}

@app.post("/api/payment/add-purchase")
async def add_purchase(request: Request):
    """Добавить покупку пользователю"""
    try:
        data = await request.json()
        tg_id = data.get("tg_id")
        category = data.get("category")
        item_id = data.get("item_id")
        
        if not tg_id or not category or not item_id:
            raise HTTPException(status_code=400, detail="Необходимо указать tg_id, category и item_id")
        
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == tg_id))
            user = result.scalars().first()
            
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
            
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
            user.purchased_items = json.dumps(purchased_items) if isinstance(purchased_items, dict) else purchased_items
            await session.commit()
            
            logger.info(f"✅ Покупка добавлена: пользователь {tg_id}, категория {category}, товар {item_id}")
            return {"success": True, "purchased_items": purchased_items}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при добавлении покупки: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при добавлении покупки: {str(e)}")

@app.post("/api/payment/webhook")
async def payment_webhook(request: Request):
    """
    Вебхук для получения уведомлений об оплате от CryptoBot
    
    Этот endpoint должен быть настроен в CryptoBot для получения уведомлений
    о платежах
    """
    try:
        data = await request.json()
        logger.info(f"📨 Получен вебхук от CryptoBot: {data}")
        
        # Обработка уведомления о платеже от CryptoBot
        if "update_type" in data and data["update_type"] == "invoice_paid":
            invoice = data.get("payload", {}).get("invoice", {})
            invoice_id = invoice.get("invoice_id")
            
            if invoice_id:
                # Получаем информацию о счете с проверкой
                verification_result = await cryptobot.verify_payment(invoice_id)
                
                if verification_result.get("paid"):
                    # Парсим payload для получения информации о покупке
                    payload = verification_result.get("payload")
                    if payload:
                        try:
                            category = payload.get("category")
                            item_id = payload.get("item_id")
                            user_id = payload.get("user_id")
                            
                            if not user_id:
                                logger.warning(f"⚠️ Payload не содержит user_id для invoice_id {invoice_id}")
                                return {"ok": True}
                            
                            logger.info(f"✅ Успешная оплата через CryptoBot: invoice_id {invoice_id}, пользователь {user_id}, товар {category}/{item_id}")
                            
                            # Здесь можно добавить логику сохранения покупки в базу данных
                            # или отправки уведомления пользователю
                            # Покупка будет автоматически добавлена при следующей проверке статуса
                            
                            return {"ok": True}
                        except Exception as e:
                            logger.error(f"Ошибка обработки payload: {e}", exc_info=True)
                            return {"ok": False}
                    else:
                        logger.warning(f"⚠️ Payload отсутствует в счете: invoice_id {invoice_id}")
            
            return {"ok": True}
        
        return {"ok": True}
        
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука CryptoBot: {e}", exc_info=True)
        return {"ok": False}

@app.post("/api/profile/first_login")
async def mark_first_login(request: Request):
    # Optional hint endpoint; does nothing critical server-side for now
    return {"ok": True}

@app.post("/api/profile/update-username")
async def update_username(tg_id: int = Query(...), username: str = Query(...)):
    """Обновить username пользователя"""
    try:
        async with async_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == tg_id))
            user = result.scalars().first()
            
            if user:
                user.username = username
                await session.commit()
                logger.info(f"✅ Username обновлен для пользователя {tg_id}: {username}")
                return {"success": True}
            else:
                return {"success": False, "message": "Пользователь не найден"}
    except Exception as e:
        logger.error(f"Ошибка при обновлении username: {e}")
        return {"success": False, "message": str(e)}

@app.get("/api/rating")
async def get_rating(role: str = Query("user")):
    """Получить рейтинг пользователей или админов (топ 100)"""
    try:
        async with async_session() as session:
            # Определяем роль для фильтрации
            if role == "admin":
                role_filter = "admin"
            elif role == "creator":
                role_filter = "creator"
            else:
                role_filter = "user"
            
            # Получаем всех пользователей с нужной ролью
            users_result = await session.execute(
                select(User).where(User.role == role_filter)
            )
            users = users_result.scalars().all()
            
            # Для каждого пользователя считаем рейтинг
            ratings = []
            for user in users:
                # Количество побед
                wins_result = await session.execute(
                    select(func.count(Winner.id)).where(Winner.user_id == user.telegram_id)
                )
                wins_count = wins_result.scalar() or 0
                
                # Количество участий
                participations_result = await session.execute(
                    select(func.count(Participant.id)).where(Participant.user_id == user.telegram_id)
                )
                participations_count = participations_result.scalar() or 0
                
                # Рейтинг = количество побед * 10 + количество участий
                rating = wins_count * 10 + participations_count
                
                # Аватар будет получен через Telegram WebApp API на клиенте
                # Здесь оставляем None, так как получение аватара через Bot API требует дополнительных прав
                avatar_url = None
                
                ratings.append({
                    "telegram_id": user.telegram_id,
                    "username": user.username or f"User_{user.telegram_id}",
                    "rating": rating,
                    "wins": wins_count,
                    "participations": participations_count,
                    "avatar_url": avatar_url
                })
            
            # Сортируем по рейтингу (по убыванию)
            ratings.sort(key=lambda x: x["rating"], reverse=True)
            
            # Берем топ 100
            top_100 = ratings[:100]
            
            # Добавляем место (place)
            for idx, rating in enumerate(top_100):
                rating["place"] = idx + 1
            
            return {
                "success": True,
                "role": role_filter,
                "ratings": top_100
            }
    except Exception as e:
        logger.error(f"Ошибка при получении рейтинга: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ------------------- GIVEAWAYS API -------------------

@app.post("/api/giveaways")
async def create_giveaway(request: Request):
    """
    Создание нового конкурса (розыгрыша)
    Ожидает JSON:
    {
        "name": "Название",
        "prize": "Приз",
        "start_date": "2025-11-01T10:00:00",  # Опционально, дата начала (МСК)
        "end_date": "2025-11-01T21:00:00",  # Дата окончания (МСК)
        "prize_links": ["link1", "link2"],  # Опционально, массив ссылок на NFT-подарки
        "created_by": 123456789  # ID создателя (admin или creator)
    }
    """
    data = await request.json()

    name = data.get("name") or data.get("title")
    prize = data.get("prize")
    start_date_str = data.get("start_date") or data.get("start_at")
    end_date_str = data.get("end_date") or data.get("end_at")
    submission_end_date_str = data.get("submission_end_date")  # Дата окончания приема работ (для конкурса рисунков)
    post_link = data.get("post_link", "")
    discussion_group_link = data.get("discussion_group_link", "")
    conditions = data.get("conditions", "")
    winners_count = data.get("winners_count", 1)
    created_by = data.get("created_by")
    prize_links = data.get("prize_links", [])  # Массив ссылок на NFT-подарки
    contest_type = data.get("contest_type", "random_comment")  # Тип конкурса: "random_comment", "drawing" или "collection"
 
    # Базовая валидация: всегда нужно название
    if not name:
        return {"success": False, "message": "❌ Название обязательно"}
    
    # Валидация полей в зависимости от типа конкурса
    if contest_type == "drawing":
        # Для рисунков дата окончания голосования (end_date) обязательна
        if not end_date_str:
            return {"success": False, "message": "❌ Для конкурса рисунков обязательна дата окончания голосования (end_date)"}
        # Для конкурса рисунков:
        # - Обязательна дата окончания приема работ (submission_end_date)
        # - post_link НЕ обязателен (может быть пустым)
        if not submission_end_date_str:
            return {"success": False, "message": "❌ Для конкурса рисунков обязательна дата окончания приема работ (submission_end_date)"}
        # post_link не требуется для конкурса рисунков, поэтому не проверяем его
    elif contest_type == "collection":
        # Для конкурса коллекций:
        # - Обязательна дата окончания приема работ (submission_end_date)
        # - post_link НЕ обязателен (может быть пустым)
        if not submission_end_date_str:
            return {"success": False, "message": "❌ Для конкурса коллекций обязательна дата окончания приема работ (submission_end_date)"}
        # post_link не требуется для конкурса коллекций, поэтому не проверяем его
    elif contest_type == "random_comment":
        # Для конкурса рандом комментариев:
        # - Обязательна ссылка на пост (post_link)
        # - Время начала/окончания НЕ обязательно (можно подводить итоги вручную в любой момент)
        if not post_link or not post_link.strip():
            return {"success": False, "message": "❌ Для конкурса рандом комментариев обязательна ссылка на пост (post_link)"}
        # submission_end_date не требуется для рандом комментариев
    else:
        # Неизвестный тип конкурса
        return {"success": False, "message": f"❌ Неизвестный тип конкурса: {contest_type}. Доступные типы: 'random_comment', 'drawing', 'collection'"}
    
    # Валидация количества победителей
    try:
        winners_count = int(winners_count)
        if winners_count < 1:
            winners_count = 1
        elif winners_count > 50:
            winners_count = 50
    except (ValueError, TypeError):
        winners_count = 1
    
    # Валидация prize_links: количество должно совпадать с winners_count
    if prize_links and isinstance(prize_links, list):
        if len(prize_links) != winners_count:
            return {"success": False, "message": f"❌ Количество ссылок на NFT-подарки ({len(prize_links)}) должно совпадать с количеством победителей ({winners_count})"}
    else:
        prize_links = []
    
    # Преобразуем даты в МСК время
    msk_tz = pytz.timezone('Europe/Moscow')
    
    def parse_date(date_str):
        """Парсит дату из строки в МСК время"""
        if not date_str:
            return None
        if isinstance(date_str, str):
            date_clean = date_str.replace('Z', '').replace('+00:00', '')
            if not date_clean:
                return None
            if 'T' in date_clean:
                date_naive = datetime.fromisoformat(date_clean)
            else:
                date_naive = datetime.fromisoformat(f"{date_clean}T00:00:00")
        else:
            date_naive = date_str
        return msk_tz.localize(date_naive) if date_naive.tzinfo is None else date_naive.astimezone(msk_tz)
    
    start_date_msk = parse_date(start_date_str)
    end_date_msk = parse_date(end_date_str)
    submission_end_date_msk = parse_date(submission_end_date_str)
    
    if contest_type in ["drawing", "collection"] and submission_end_date_msk and end_date_msk:
        time_diff = (end_date_msk - submission_end_date_msk).total_seconds()
        if time_diff < 600:
            return {"success": False, "message": "❌ Между окончанием приема работ и голосованием должно быть минимум 10 минут"}
        if submission_end_date_msk >= end_date_msk:
            return {"success": False, "message": "❌ Дата окончания приема работ должна быть раньше даты окончания голосования"}
    
    start_date_db = to_msk_naive(start_date_msk)
    end_date_db = to_msk_naive(end_date_msk)
    submission_end_date_db = to_msk_naive(submission_end_date_msk)
    
    async with async_session() as session:
        # Проверяем, не существует ли уже конкурс для этого поста (только для рандом комментариев)
        # Для конкурса рисунков post_link может быть пустым, поэтому проверяем только для random_comment
        if post_link and post_link.strip() and contest_type == "random_comment":
            try:
                # Проверяем наличие колонки post_link
                if IS_SQLITE:
                    result = await session.execute(text("PRAGMA table_info(giveaways)"))
                    columns_info = result.fetchall()
                    existing_columns = {row[1]: row for row in columns_info}
                else:
                    result = await session.execute(text("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'giveaways'
                    """))
                    columns_info = result.fetchall()
                    existing_columns = {row[0]: row for row in columns_info}
                
                if 'post_link' in existing_columns:
                    # Используем прямой SQL запрос для проверки
                    check_result = await session.execute(
                        text("SELECT id FROM giveaways WHERE post_link = :post_link AND post_link IS NOT NULL AND post_link != ''"),
                        {"post_link": post_link}
                    )
                    existing_row = check_result.fetchone()
                    if existing_row:
                        return {"success": False, "message": f"❌ Для этого поста уже существует конкурс (ID: {existing_row[0]}). Один пост может иметь только один конкурс."}
            except Exception as e:
                # Если что-то пошло не так с проверкой, просто логируем и продолжаем
                logger.warning(f"Ошибка при проверке существующего конкурса: {e}")
        
        # Определяем канал и группу обсуждения
        channel_link = data.get("channel_link")  # Берем из запроса
        final_discussion_group_link = discussion_group_link  # Используем переданную в запросе
        
        if created_by:
            # Получаем информацию о создателе
            result = await session.execute(
                select(User).where(User.telegram_id == created_by)
            )
            creator_user = result.scalars().first()
            
            if creator_user:
                if creator_user.role == "creator":
                    # Для создателя - используем значения из запроса или из профиля пользователя
                    # Если не переданы в запросе, берем из профиля пользователя
                    if not channel_link:
                        channel_link = creator_user.channel_link
                    if not final_discussion_group_link:
                        final_discussion_group_link = creator_user.chat_link
                elif creator_user.role == "admin":
                    # Для админа - из активов
                    if not channel_link:
                        channel_link = creator_user.channel_link
                    if not final_discussion_group_link:
                        final_discussion_group_link = creator_user.chat_link or discussion_group_link
        
        # Если discussion_group_link был передан явно, используем его (приоритет)
        if discussion_group_link:
            final_discussion_group_link = discussion_group_link
        
        # Для конкурса рисунков post_link должен быть NULL или пустой строкой
        # Для рандом комментариев post_link обязателен (уже проверено выше)
        final_post_link = None
        if contest_type == "random_comment":
            # Для рандом комментариев post_link обязателен
            final_post_link = post_link if post_link and post_link.strip() else None
        else:
            # Для конкурса рисунков post_link не обязателен (может быть NULL)
            final_post_link = post_link if post_link and post_link.strip() else None
        
        created_at_msk = datetime.now(MSK_TZ).replace(tzinfo=None)

        new_giveaway = Giveaway(
            name=name,
            prize=prize or '',
            start_date=start_date_db,
            end_date=end_date_db,
            submission_end_date=submission_end_date_db if contest_type in ["drawing", "collection"] else None,
            post_link=final_post_link,  # Для рандом комментариев обязателен, для рисунков может быть NULL
            discussion_group_link=final_discussion_group_link,
            channel_link=channel_link,
            conditions=conditions,
            winners_count=winners_count,
            prize_links=prize_links if prize_links else None,
            created_at=created_at_msk,
            created_by=created_by if created_by else None,
            contest_type=contest_type,
        )
        session.add(new_giveaway)
        await session.commit()
        await session.refresh(new_giveaway)
        
        # Если это конкурс рисунков, создаем начальную запись в drawing_contests.json
        if contest_type == "drawing":
            async with drawing_data_lock:
                drawing_data = load_drawing_data()
                contest_key = str(new_giveaway.id)
                if contest_key not in drawing_data:
                    # Создаем начальную запись для конкурса рисунков
                    preferred_creator_id = created_by if created_by else None
                    msk_tz = pytz.timezone('Europe/Moscow')
                    now_msk = datetime.now(msk_tz)
                    drawing_data[contest_key] = {
                        "contest_id": new_giveaway.id,
                        "title": name,
                        "topic": conditions or '',
                        "created_by": preferred_creator_id,
                        "created_at": now_msk.isoformat(),
                        "works": []
                    }
                    save_drawing_data(drawing_data)
                    logger.info(f"✅ Создана начальная запись для конкурса рисунков {new_giveaway.id} в drawing_contests.json")
        
        # Если это конкурс коллекций, создаем начальную запись в collection_contests.json
        if contest_type == "collection":
            async with collection_data_lock:
                collection_data = load_collection_data()
                contest_key = str(new_giveaway.id)
                if contest_key not in collection_data:
                    # Создаем начальную запись для конкурса коллекций
                    preferred_creator_id = created_by if created_by else None
                    msk_tz = pytz.timezone('Europe/Moscow')
                    now_msk = datetime.now(msk_tz)
                    collection_data[contest_key] = {
                        "contest_id": new_giveaway.id,
                        "title": name,
                        "topic": conditions or '',
                        "created_by": preferred_creator_id,
                        "created_at": now_msk.isoformat(),
                        "collections": []
                    }
                    save_collection_data(collection_data)
                    logger.info(f"✅ Создана начальная запись для конкурса коллекций {new_giveaway.id} в collection_contests.json")

    return {"success": True, "message": "✅ Конкурс успешно создан!", "id": new_giveaway.id}


@app.get("/api/giveaways")
async def list_giveaways(admin_id: int = Query(None)):
    """Получить список конкурсов. Если передан admin_id, возвращает только конкурсы этого админа."""
    async with async_session() as session:
        # Check which columns exist
        try:
            if IS_SQLITE:
                result = await session.execute(text("PRAGMA table_info(giveaways)"))
                columns_info = result.fetchall()
                existing_columns = {row[1]: row for row in columns_info}
            else:
                result = await session.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'giveaways'
                """))
                columns_info = result.fetchall()
                existing_columns = {row[0]: row for row in columns_info}
            
            # Build SELECT query with only existing columns
            base_cols = ['id', 'post_link', 'created_at']
            optional_cols = {'name': 'name', 'prize': 'prize', 'end_date': 'end_date', 'conditions': 'conditions', 'discussion_group_link': 'discussion_group_link', 'prize_links': 'prize_links', 'contest_type': 'contest_type', 'submission_end_date': 'submission_end_date', 'winners_count': 'winners_count', 'start_date': 'start_date'}
            
            select_cols = []
            for col in base_cols:
                if col in existing_columns:
                    select_cols.append(col)
            
            for col_key, col_name in optional_cols.items():
                if col_name in existing_columns:
                    select_cols.append(col_name)
            
            if not select_cols:
                return []
            
            # Проверяем наличие поля created_by
            has_created_by = 'created_by' in existing_columns
            
            # Строим запрос с фильтрацией по admin_id если нужно
            select_cols_final = select_cols.copy()
            if has_created_by and 'created_by' not in select_cols_final:
                select_cols_final.append('created_by')
            
            query = f"SELECT {', '.join(select_cols_final)} FROM giveaways"
            
            # Добавляем фильтрацию для админа: показываем его конкурсы и конкурсы создателя
            if admin_id and has_created_by:
                # Проверяем, является ли пользователь создателем
                user_result = await session.execute(
                    select(User).where(User.telegram_id == admin_id)
                )
                user = user_result.scalars().first()
                
                if user and user.role == "admin":
                    # Для админа показываем его конкурсы и конкурсы создателя
                    query += f" WHERE (created_by = {admin_id} OR created_by = {CREATOR_ID})"
                elif user and user.role == "creator":
                    # Для создателя показываем все конкурсы
                    pass  # Без фильтрации
                else:
                    # Для обычного пользователя или если пользователь не найден - только его конкурсы
                    query += f" WHERE created_by = {admin_id}"
            
            result = await session.execute(text(query))
            rows = result.fetchall()
            
            # Map rows to dict format
            giveaways_list = []
            for row in rows:
                # Используем select_cols_final для правильного маппинга
                row_dict = dict(zip(select_cols_final, row))
                
                # Проверяем, окончен ли конкурс и нужно ли выбрать победителей
                end_date = row_dict.get('end_date')
                is_confirmed = row_dict.get('is_confirmed', False) if 'is_confirmed' in existing_columns else False
                winners_selected_at = row_dict.get('winners_selected_at') if 'winners_selected_at' in existing_columns else None
                winners_count = row_dict.get('winners_count', 1) if 'winners_count' in existing_columns else 1
                
                # Автоматически выбираем победителей, если конкурс окончен и победители еще не выбраны
                contest_id = row_dict.get('id')
                if end_date and not is_confirmed and not winners_selected_at:
                    # Преобразуем дату в МСК для сравнения
                    msk_tz = pytz.timezone('Europe/Moscow')
                    end_date_obj = None
                    
                    try:
                        if isinstance(end_date, str):
                            # Обрабатываем строку в формате "2025-11-04 15:54:00.000000" или ISO формате
                            end_date_clean = end_date.strip()
                            
                            # Если формат "YYYY-MM-DD HH:MM:SS.microseconds" или "YYYY-MM-DD HH:MM:SS"
                            if 'T' not in end_date_clean and ' ' in end_date_clean:
                                # Формат: "2025-11-04 15:54:00.000000" или "2025-11-04 15:54:00"
                                try:
                                    # Пробуем парсить с микросекундами
                                    if '.' in end_date_clean:
                                        end_date_naive = datetime.strptime(end_date_clean, '%Y-%m-%d %H:%M:%S.%f')
                                    else:
                                        end_date_naive = datetime.strptime(end_date_clean, '%Y-%m-%d %H:%M:%S')
                                    # Считаем, что это МСК время (как указал пользователь)
                                    end_date_obj = msk_tz.localize(end_date_naive)
                                except ValueError:
                                    # Если не получилось, пробуем ISO формат
                                    end_date_clean = end_date_clean.replace('Z', '').replace('+00:00', '')
                                    if 'T' in end_date_clean:
                                        end_date_naive = datetime.fromisoformat(end_date_clean)
                                    else:
                                        end_date_naive = datetime.fromisoformat(f"{end_date_clean}T00:00:00")
                                    # Считаем, что это МСК время
                                    end_date_obj = msk_tz.localize(end_date_naive) if end_date_naive.tzinfo is None else end_date_naive.astimezone(msk_tz)
                            else:
                                # ISO формат с T
                                end_date_clean = end_date_clean.replace('Z', '').replace('+00:00', '')
                                if 'T' in end_date_clean:
                                    end_date_naive = datetime.fromisoformat(end_date_clean)
                                else:
                                    end_date_naive = datetime.fromisoformat(f"{end_date_clean}T00:00:00")
                                # Считаем, что это МСК время
                                end_date_obj = msk_tz.localize(end_date_naive) if end_date_naive.tzinfo is None else end_date_naive.astimezone(msk_tz)
                        elif isinstance(end_date, datetime):
                            # Если это уже datetime объект
                            if end_date.tzinfo is None:
                                # Если naive datetime, считаем что это МСК
                                end_date_obj = msk_tz.localize(end_date)
                            else:
                                # Если timezone-aware, преобразуем в МСК
                                end_date_obj = end_date.astimezone(msk_tz)
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось преобразовать end_date в datetime для конкурса {contest_id}: {end_date}, ошибка: {e}")
                        end_date_obj = None
                    
                    if end_date_obj:
                        current_time_msk = datetime.now(msk_tz)
                        logger.debug(f"🔍 Проверка конкурса {contest_id}: end_date={end_date_obj}, current_time={current_time_msk}, окончен={end_date_obj < current_time_msk}")
                        # Удален автоматический выбор победителей - теперь только через кнопку "Подвести итоги"
                    else:
                        logger.warning(f"⚠️ Не удалось преобразовать end_date в datetime для конкурса {contest_id}: {end_date}")
                elif is_confirmed:
                    logger.debug(f"✓ Конкурс {contest_id} уже подтвержден")
                elif winners_selected_at:
                    logger.debug(f"✓ Победители для конкурса {contest_id} уже выбраны в {winners_selected_at}")
                
                # Парсим prize_links если это JSON строка
                prize_links = row_dict.get('prize_links')
                if isinstance(prize_links, str):
                    try:
                        import json
                        prize_links = json.loads(prize_links) if prize_links else []
                    except:
                        prize_links = []
                elif prize_links is None:
                    prize_links = []
                elif not isinstance(prize_links, list):
                    prize_links = []
                
                # Логируем для отладки (только если есть призы)
                if prize_links:
                    logger.debug(f"Конкурс {contest_id}: загружено {len(prize_links)} призов")
                
                # Получаем contest_type и submission_end_date
                contest_type = row_dict.get('contest_type', 'random_comment') if 'contest_type' in existing_columns else 'random_comment'
                submission_end_date = row_dict.get('submission_end_date') if 'submission_end_date' in existing_columns else None
                start_date = row_dict.get('start_date') if 'start_date' in existing_columns else None
                created_by = row_dict.get('created_by') if has_created_by else None
                
                giveaways_list.append({
                    "id": row_dict.get('id'),
                    "title": row_dict.get('name') or row_dict.get('post_link') or 'Без названия',
                    "name": row_dict.get('name') or '',
                    "post_link": row_dict.get('post_link') or '',
                    "discussion_group_link": row_dict.get('discussion_group_link') or '',
                    "conditions": row_dict.get('conditions') or '',
                    "prize": row_dict.get('prize') or '',
                    "prize_links": prize_links if prize_links else [],  # Всегда возвращаем список, даже если пустой
                    "end_at": to_iso(end_date),
                    "end_at_local": to_datetime_local(end_date),
                    "end_date": to_iso(end_date),
                    "start_at": to_iso(start_date),
                    "start_at_local": to_datetime_local(start_date),
                    "start_date": to_iso(start_date),
                    "submission_end_date": to_iso(submission_end_date),
                    "submission_end_date_local": to_datetime_local(submission_end_date),
                    "created_at": to_iso(row_dict.get('created_at')),
                    "created_at_local": to_datetime_local(row_dict.get('created_at')),
                    "created_by": created_by,  # Добавляем created_by в ответ
                    "is_confirmed": is_confirmed,
                    "winners_count": winners_count,
                    "contest_type": contest_type,
                })
            
            return giveaways_list
        except Exception as e:
            print(f"Error listing giveaways: {e}")
            return []

# Backward-compat aliases for creator.html JS expecting /api/contests
@app.get("/api/contests")
async def alias_list_contests(admin_id: int = Query(None)):
    """Получить список конкурсов. Для админа - только его конкурсы, для создателя - все."""
    return await list_giveaways(admin_id=admin_id)

@app.post("/api/contests")
async def alias_create_contest(request: Request):
    return await create_giveaway(request)

@app.post("/api/contests/{contest_id}/select-winners")
async def select_winners(
    contest_id: int,
    winners_count: int = Query(default=1),
    current_user_id: int = Query(default=None),
):
    """Выбирает победителей из конкурса на основе комментариев под постом через Telethon.

    Итоги может подводить только владелец конкурса (created_by), либо создатель (role=creator),
    в зависимости от настроек created_by.
    """
    try:
        # Получаем информацию о конкурсе и проверяем права
        async with async_session() as session:
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")

            # Если передан current_user_id — проверяем, что это владелец конкурса
            if current_user_id is not None:
                user_result = await session.execute(
                    select(User).where(User.telegram_id == current_user_id)
                )
                user = user_result.scalars().first()
                if not user:
                    raise HTTPException(status_code=403, detail="Пользователь не найден")

                # Разрешаем только владельцу конкурса (created_by)
                if giveaway.created_by is not None:
                    try:
                        if int(giveaway.created_by) != int(current_user_id):
                            raise HTTPException(
                                status_code=403,
                                detail="Подвести итоги может только создатель этого конкурса",
                            )
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=403,
                            detail="Подвести итоги может только создатель этого конкурса",
                        )

            # Для конкурсов рисунков post_link не требуется
            contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
            if contest_type == 'random_comment' and not giveaway.post_link:
                raise HTTPException(status_code=400, detail="У конкурса не указана ссылка на пост")
        
        # Выбираем победителей через Telethon (Telethon соберет комментарии и сохранит их в файл)
        # Бот больше не нужен, так как используем только Telethon
        try:
            # Создаем временный Bot объект только для передачи в функцию (но он не используется)
            bot = Bot(token=BOT_TOKEN)
            winners = await select_winners_from_contest(contest_id, winners_count, bot)
            # Не закрываем сессию бота, так как она может быть None
            return {"success": True, "winners": winners}
        except ValueError as e:
            # Если нет комментариев, возвращаем более понятное сообщение
            error_msg = str(e)
            # Если ошибка связана с тем, что комментарии еще собираются, возвращаем специальный статус
            if "комментариев" in error_msg.lower() or "не найдено" in error_msg.lower():
                # Возвращаем успех, но с информацией о том, что комментарии еще собираются
                return {
                    "success": False,
                    "collecting": True,  # Флаг, что комментарии еще собираются
                    "message": "Комментарии собираются через Telethon. Пожалуйста, подождите..."
                }
            raise HTTPException(status_code=400, detail=error_msg)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при выборе победителей: {e}", exc_info=True)
        error_msg = str(e)
        # Если ошибка связана с Telethon или сбором комментариев, возвращаем специальный статус
        if "telethon" in error_msg.lower() or "комментариев" in error_msg.lower() or "собираются" in error_msg.lower():
            return {
                "success": False,
                "collecting": True,
                "message": "Комментарии собираются через Telethon. Пожалуйста, подождите..."
            }
        # Для любых других ошибок тоже возвращаем collecting: true, чтобы не показывать ошибку пользователю
        # Telethon может работать долго, и это нормально
        return {
            "success": False,
            "collecting": True,
            "message": "Обработка данных. Пожалуйста, подождите..."
        }

@app.get("/api/contests/{contest_id}/winners")
async def get_winners(contest_id: int, current_user_id: int = Query(None)):
    """Получить список победителей конкурса.

    В текущей версии победителей видят все (и админ, и креатор, и пользователи),
    параметр current_user_id зарезервирован на будущее и сейчас не влияет на логику.
    """
    try:
        async with async_session() as session:
            # Получаем информацию о конкурсе
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            # Получаем победителей ТОЛЬКО для этого конкурса
            result = await session.execute(
                select(Winner).where(Winner.giveaway_id == contest_id)
            )
            winners = result.scalars().all()
            
            # Определяем тип конкурса для правильного возврата полей
            contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
            is_confirmed = getattr(giveaway, 'is_confirmed', False) if hasattr(giveaway, 'is_confirmed') else False
            winners_selected_at = giveaway.winners_selected_at.isoformat() if hasattr(giveaway, 'winners_selected_at') and giveaway.winners_selected_at else None

            logger.info(f"📊 Загружено {len(winners)} победителей для конкурса {contest_id} (тип: {contest_type}, post_link: {giveaway.post_link})")
            for w in winners:
                if contest_type == 'random_comment':
                    logger.debug(f"  - Победитель ID {w.id}, giveaway_id={w.giveaway_id}, comment_link={w.comment_link}")
                else:
                    logger.debug(f"  - Победитель ID {w.id}, giveaway_id={w.giveaway_id}, photo_link={w.photo_link}")
            
            winners_data = []
            for w in winners:
                winner_data = {
                    "id": w.id,
                    "user_id": w.user_id if hasattr(w, 'user_id') else None,
                    "user_username": w.user_username if hasattr(w, 'user_username') else None,
                    "prize_link": w.prize_link if hasattr(w, 'prize_link') else None,
                    "place": w.place if hasattr(w, 'place') else None,
                    "created_at": w.created_at.isoformat() if w.created_at else None
                }
                
                # Для рандом комментариев возвращаем comment_link
                if contest_type == 'random_comment':
                    winner_data["comment_link"] = w.comment_link if hasattr(w, 'comment_link') else None
                    winner_data["photo_link"] = None
                # Для конкурса рисунков возвращаем photo_link
                else:
                    winner_data["photo_link"] = w.photo_link if hasattr(w, 'photo_link') else None
                    winner_data["photo_message_id"] = w.photo_message_id if hasattr(w, 'photo_message_id') else None
                    winner_data["comment_link"] = None
                
                winners_data.append(winner_data)
            
            return {
                "winners": winners_data,
                "is_confirmed": is_confirmed,
                "winners_selected_at": winners_selected_at,
                "contest_type": contest_type
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/contests/{contest_id}/reroll-winner")
async def reroll_winner(contest_id: int, request: Request):
    """Рерандомизирует одного победителя. Доступно только создателю конкурса."""
    try:
        data = await request.json()
        old_winner_link = data.get("old_winner_link")
        current_user_id = data.get("current_user_id")
        
        if not old_winner_link:
            raise HTTPException(status_code=400, detail="old_winner_link обязателен")
        
        # Проверяем права: рероллить может только владелец конкурса
        async with async_session() as session:
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")

            if current_user_id is not None and giveaway.created_by is not None:
                try:
                    if int(giveaway.created_by) != int(current_user_id):
                        raise HTTPException(
                            status_code=403,
                            detail="Реролл доступен только создателю конкурса",
                        )
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=403,
                        detail="Реролл доступен только создателю конкурса",
                    )

        # Создаем временный Bot объект только для совместимости (но он не используется в reroll_single_winner)
        bot = Bot(token=BOT_TOKEN)
        try:
            new_winner = await reroll_single_winner(contest_id, old_winner_link, bot)
        finally:
            # Закрываем сессию бота, если она существует
            try:
                bot_session = await bot.get_session()
                if bot_session:
                    await bot_session.close()
            except Exception:
                pass
        
        return {"success": True, "winner": new_winner}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при рерандомизации победителя: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def parse_telegram_username(link: str) -> str:
    """Парсит username из ссылки Telegram"""
    if not link:
        return None
    if link.startswith('@'):
        return link
    if 't.me/' in link:
        match = re.search(r't\.me/([a-zA-Z0-9_]+)', link)
        if match:
            return '@' + match.group(1)
    return None

async def check_subscription(bot: Bot, chat_username: str, user_id: int) -> bool:
    """Проверяет подписку пользователя на канал/чат"""
    try:
        member = await bot.get_chat_member(chat_username, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

def normalize_datetime_to_msk(dt):
    """Приводит datetime к timezone-aware формату в МСК времени"""
    if dt is None:
        return None
    msk_tz = pytz.timezone('Europe/Moscow')
    if dt.tzinfo is None:
        # Если datetime naive, считаем что он в МСК времени
        return msk_tz.localize(dt)
    else:
        # Если datetime уже timezone-aware, преобразуем в МСК
        return dt.astimezone(msk_tz)

@app.post("/api/contests/{contest_id}/participate")
async def participate_in_contest(contest_id: int, request: Request):
    """Проверка подписки при попытке присоединиться к конкурсу. Возвращает список неподписанных каналов/чатов."""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        user_username = data.get("username")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id обязателен")
        
        async with async_session() as session:
            # Получаем информацию о конкурсе
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            # Проверяем, не участвует ли уже пользователь
            from models import Participant
            from sqlalchemy.exc import IntegrityError
            existing_participant_result = await session.execute(
                select(Participant).where(
                    Participant.giveaway_id == contest_id,
                    Participant.user_id == user_id
                )
            )
            existing_participant = existing_participant_result.scalars().first()
            if existing_participant:
                # Пользователь уже участвует - возвращаем успешный ответ
                # Для конкурса рисунков проверяем, загружена ли фотография
                contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
                has_photo = bool(existing_participant.photo_link) if existing_participant else False
                if contest_type == 'drawing' and has_photo:
                    return {"success": True, "message": "Вы уже участвуете в этом конкурсе и загрузили фотографию", "already_participating": True, "has_photo": True}
                elif contest_type == 'drawing':
                    return {"success": True, "message": "Вы уже участвуете в этом конкурсе. Загрузите фотографию.", "already_participating": True, "has_photo": False}
                else:
                    return {"success": True, "message": "Вы уже участвуете в этом конкурсе", "already_participating": True}
            
            # Собираем список каналов/чатов для проверки
            required_subscriptions = []
            
            # 1. Канал и чат админа, который создал конкурс
            if giveaway.created_by:
                creator_result = await session.execute(
                    select(User).where(User.telegram_id == giveaway.created_by)
                )
                creator_user = creator_result.scalars().first()
                
                if creator_user:
                    # Канал админа
                    if creator_user.channel_link:
                        channel_username = parse_telegram_username(creator_user.channel_link)
                        if channel_username:
                            required_subscriptions.append({
                                "type": "channel",
                                "link": creator_user.channel_link,
                                "username": channel_username,
                                "name": "Канал админа"
                            })
                    
                    # Чат админа
                    if creator_user.chat_link:
                        chat_username = parse_telegram_username(creator_user.chat_link)
                        if chat_username:
                            required_subscriptions.append({
                                "type": "chat",
                                "link": creator_user.chat_link,
                                "username": chat_username,
                                "name": "Чат админа"
                            })
            
            # 2. Обязательный канал создателя
            creator_channel_link = "t.me/monkeys_giveaways"
            creator_channel_username = parse_telegram_username(creator_channel_link)
            if creator_channel_username:
                required_subscriptions.append({
                    "type": "channel",
                    "link": creator_channel_link,
                    "username": creator_channel_username,
                    "name": "Канал создателя"
                })
            
            # 3. Извлекаем ссылки из условий конкурса (включая дополнительные условия)
            # Парсим поле conditions для поиска ссылок на каналы/чаты
            if giveaway.conditions:
                # Ищем все ссылки вида t.me/username или @username в тексте условий
                # Паттерн для поиска ссылок: t.me/username, telegram.me/username, @username
                link_patterns = [
                    r't\.me/([a-zA-Z0-9_]+)',
                    r'telegram\.me/([a-zA-Z0-9_]+)',
                    r'@([a-zA-Z0-9_]+)'
                ]
                
                found_links = set()  # Используем set, чтобы избежать дубликатов
                for pattern in link_patterns:
                    matches = re.findall(pattern, giveaway.conditions, re.IGNORECASE)
                    for match in matches:
                        if match:
                            username = f"@{match}"
                            # Проверяем, что это не ссылка, которая уже есть в списке
                            link = f"t.me/{match}"
                            found_links.add((username, link, match))
                
                # Добавляем найденные ссылки в список для проверки
                for username, link, name in found_links:
                    # Проверяем, что эта ссылка еще не добавлена
                    if not any(sub["username"] == username for sub in required_subscriptions):
                        # Определяем тип (канал или чат) по имени или оставляем как канал по умолчанию
                        required_subscriptions.append({
                            "type": "channel",  # По умолчанию канал, можно улучшить проверкой
                            "link": link,
                            "username": username,
                            "name": f"Канал {name}" if not name.startswith('@') else f"Канал {name[1:]}"
                        })
            
            # Для конкурса рисунков проверяем deadline приема работ
            contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
            if contest_type == 'drawing' and giveaway.submission_end_date:
                msk_tz = pytz.timezone('Europe/Moscow')
                now_msk = datetime.now(msk_tz)
                submission_end = normalize_datetime_to_msk(giveaway.submission_end_date)
                
                if now_msk > submission_end:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Время приема работ истекло. Окончание приема: {submission_end.strftime('%d.%m.%Y %H:%M')}"
                    )
            
            # Проверяем подписки
            bot = Bot(token=BOT_TOKEN)
            not_subscribed = []
            
            try:
                for sub in required_subscriptions:
                    is_subscribed = await check_subscription(bot, sub["username"], user_id)
                    if not is_subscribed:
                        not_subscribed.append(sub)
            finally:
                # ВАЖНО: используем другое имя переменной для сессии бота, чтобы не перезаписать SQLAlchemy session
                try:
                    bot_session = await bot.get_session()
                    if bot_session:
                        await bot_session.close()
                except Exception:
                    pass
            
            # Если есть неподписанные каналы/чаты, возвращаем их список
            if not_subscribed:
                return {
                    "success": False,
                    "requires_subscription": True,
                    "not_subscribed": not_subscribed,
                    "message": "Для участия в конкурсе необходимо подписаться на указанные каналы и чаты"
                }
            
            # Если все подписки есть, сразу добавляем участника
            # Для рандом комментариев photo_link = NULL
            # Для конкурса рисунков photo_link будет установлен позже, когда пользователь отправит фотографию
            try:
                participant = Participant(
                    giveaway_id=contest_id,
                    user_id=user_id,
                    username=user_username,
                    photo_link=None,  # Будет установлен позже для конкурса рисунков
                    photo_message_id=None
                )
                session.add(participant)
                await session.commit()
                
                return {"success": True, "message": "✅ Вы успешно присоединились к конкурсу!"}
            except IntegrityError as e:
                # Если возникла ошибка UNIQUE constraint, значит пользователь уже участвует
                await session.rollback()
                logger.warning(f"Попытка повторного участия пользователя {user_id} в конкурсе {contest_id}")
                # Проверяем статус участника еще раз (после rollback нужно перезагрузить giveaway)
                existing_participant_result = await session.execute(
                    select(Participant).where(
                        Participant.giveaway_id == contest_id,
                        Participant.user_id == user_id
                    )
                )
                existing_participant = existing_participant_result.scalars().first()
                
                # Перезагружаем giveaway после rollback
                giveaway_result = await session.execute(
                    select(Giveaway).where(Giveaway.id == contest_id)
                )
                giveaway = giveaway_result.scalars().first()
                
                if existing_participant and giveaway:
                    contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
                    has_photo = bool(existing_participant.photo_link) if existing_participant else False
                    if contest_type == 'drawing' and has_photo:
                        return {"success": True, "message": "Вы уже участвуете в этом конкурсе и загрузили фотографию", "already_participating": True, "has_photo": True}
                    elif contest_type == 'drawing':
                        return {"success": True, "message": "Вы уже участвуете в этом конкурсе. Загрузите фотографию.", "already_participating": True, "has_photo": False}
                    else:
                        return {"success": True, "message": "Вы уже участвуете в этом конкурсе", "already_participating": True}
                else:
                    # Если участник не найден, но была ошибка UNIQUE - возможно race condition
                    raise HTTPException(status_code=500, detail="Ошибка при добавлении участника. Попробуйте еще раз.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при участии в конкурсе: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/contests/{contest_id}/upload-photo")
async def upload_photo_for_drawing_contest(
    contest_id: int,
    file: UploadFile = File(...),
    user_id: int = Form(...),
    user_username: str = Form(None)
):
    """Загрузка фотографии для конкурса рисунков"""
    try:
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id обязателен")
        
        async with async_session() as session:
            # Получаем информацию о конкурсе
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            # Проверяем тип конкурса
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')
            if contest_type != 'drawing':
                raise HTTPException(status_code=400, detail="Этот конкурс не является конкурсом рисунков")
            
            # Проверяем время окончания приема работ
            if giveaway.submission_end_date:
                msk_tz = pytz.timezone('Europe/Moscow')
                now_msk = datetime.now(msk_tz)
                submission_end = normalize_datetime_to_msk(giveaway.submission_end_date)
                
                if now_msk > submission_end:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Время приема работ истекло. Окончание приема: {submission_end.strftime('%d.%m.%Y %H:%M')}"
                    )
            
            # Проверяем, участвует ли пользователь в конкурсе
            from models import Participant
            participant_result = await session.execute(
                select(Participant).where(
                    Participant.giveaway_id == contest_id,
                    Participant.user_id == user_id
                )
            )
            participant = participant_result.scalars().first()
            
            if not participant:
                raise HTTPException(status_code=404, detail="Вы не участвуете в этом конкурсе. Сначала присоединитесь к конкурсу.")
            
            # Проверяем, не загружена ли уже фотография
            if participant.photo_link:
                raise HTTPException(status_code=400, detail="Вы уже загрузили фотографию для этого конкурса")
            
            # Проверяем тип файла
            if not file.content_type or not file.content_type.startswith('image/'):
                raise HTTPException(status_code=400, detail="Файл должен быть изображением")
            
            # Читаем файл
            file_content = await file.read()
            if len(file_content) > 10 * 1024 * 1024:  # 10 MB
                raise HTTPException(status_code=400, detail="Размер файла не должен превышать 10 МБ")
            
            # Отправляем фотографию в бот для сохранения
            bot = Bot(token=BOT_TOKEN)
            photo_link = None
            photo_message_id = None
            photo_file_id = None
            work_number = None
            local_rel_path = None

            try:
                import tempfile
                import io

                try:
                    from aiogram.types import BufferedInputFile as LocalBufferedInputFile
                except ImportError:
                    LocalBufferedInputFile = None

                preferred_creator_id = getattr(giveaway, 'created_by', None)
                chat_candidates = []
                if preferred_creator_id is not None:
                    chat_candidates.append(preferred_creator_id)
                if CREATOR_ID:
                    chat_candidates.append(CREATOR_ID)
                chat_candidates.append(user_id)

                def normalize_chat_id(value):
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        return value

                chat_id = None
                for candidate in chat_candidates:
                    if candidate is None:
                        continue
                    chat_id = normalize_chat_id(candidate)
                    break

                if chat_id is None:
                    chat_id = user_id
                else:
                    chat_id = normalize_chat_id(chat_id)

                def build_buffered_input():
                    if LocalBufferedInputFile is None:
                        return None
                    try:
                        return LocalBufferedInputFile(file_content, filename=file.filename)
                    except Exception:
                        return None

                async def send_photo_with_fallback(target_chat_id: int, caption: str):
                    buffered = build_buffered_input()
                    if buffered is not None:
                        return await bot.send_photo(chat_id=target_chat_id, photo=buffered, caption=caption)
                    if FSInputFile is not None:
                        tmp_path = None
                        try:
                            with tempfile.NamedTemporaryFile(delete=False, suffix=(f"_{file.filename}" if file.filename else "")) as tmp:
                                tmp.write(file_content)
                                tmp_path = tmp.name
                            return await bot.send_photo(chat_id=target_chat_id, photo=FSInputFile(tmp_path), caption=caption)
                        finally:
                            if tmp_path and os.path.exists(tmp_path):
                                try:
                                    os.remove(tmp_path)
                                except Exception:
                                    pass
                    return await bot.send_photo(chat_id=target_chat_id, photo=file_content, caption=caption)

                logger.debug(f"📨 Обработка загрузки работы для конкурса {contest_id} пользователем {user_id}")

                async with drawing_data_lock:
                    drawing_data = load_drawing_data()
                    contest_key = str(contest_id)
                    contest_entry = drawing_data.get(contest_key)
                    if not contest_entry:
                        msk_tz = pytz.timezone('Europe/Moscow')
                        created_at_msk = None
                        if getattr(giveaway, 'created_at', None):
                            # Конвертируем UTC время в МСК
                            created_at_utc = giveaway.created_at
                            if created_at_utc.tzinfo is None:
                                # Если naive datetime, считаем что это UTC
                                created_at_utc = created_at_utc.replace(tzinfo=timezone.utc)
                            created_at_msk = created_at_utc.astimezone(msk_tz)
                        else:
                            created_at_msk = datetime.now(msk_tz)
                        contest_entry = {
                            "contest_id": contest_id,
                            "title": getattr(giveaway, 'name', '') or getattr(giveaway, 'title', '') or '',
                            "topic": getattr(giveaway, 'conditions', '') or '',
                            "created_by": preferred_creator_id,
                            "created_at": created_at_msk.isoformat(),
                            "works": []
                        }
                        drawing_data[contest_key] = contest_entry
                    else:
                        contest_entry["title"] = getattr(giveaway, 'name', '') or contest_entry.get("title") or ''
                        if getattr(giveaway, 'conditions', None):
                            contest_entry["topic"] = giveaway.conditions
                        contest_entry["created_by"] = preferred_creator_id

                    works = contest_entry.setdefault("works", [])
                    existing_work = next((w for w in works if w.get("participant_user_id") == user_id), None)
                    if existing_work and existing_work.get("work_number"):
                        work_number = existing_work["work_number"]
                    else:
                        work_number = len(works) + 1

                    file_ext = os.path.splitext(file.filename or "")[1].lower()
                    if not file_ext or len(file_ext) > 5:
                        file_ext = ".jpg"
                    work_dir = os.path.join(DRAWING_UPLOADS_DIR, f"contest_{contest_id}")
                    _ensure_dir(work_dir)
                    local_filename = f"work_{work_number}{file_ext}"
                    local_path = os.path.join(work_dir, local_filename)
                    with open(local_path, "wb") as f_out:
                        f_out.write(file_content)
                    local_rel_path = os.path.relpath(local_path, ROOT_DIR).replace("\\", "/")

                    # Получаем username: сначала из параметров, потом из базы данных, если не передан
                    final_username = user_username
                    if not final_username and participant and participant.username:
                        final_username = participant.username
                    
                    # Формируем подпись с username и ID
                    if final_username:
                        caption_creator = f"Конкурс рисунков #{contest_id}\nРабота #{work_number}\nУчастник: @{final_username} (ID: {user_id})"
                    else:
                        # Если username нет, показываем только ID
                        caption_creator = f"Конкурс рисунков #{contest_id}\nРабота #{work_number}\nУчастник: ID: {user_id}"
                    caption_user = f"Конкурс рисунков #{contest_id}\nВаша работа #{work_number}"

                    try:
                        sent_message = await send_photo_with_fallback(chat_id, caption_creator)
                    except Exception as send_error:
                        if chat_id != user_id:
                            try:
                                sent_message = await send_photo_with_fallback(user_id, caption_user)
                                chat_id = user_id
                            except Exception:
                                try:
                                    if os.path.exists(local_path):
                                        os.remove(local_path)
                                except Exception:
                                    pass
                                raise HTTPException(status_code=500, detail="Не удалось отправить фотографию в Telegram") from send_error
                        else:
                            try:
                                if os.path.exists(local_path):
                                    os.remove(local_path)
                            except Exception:
                                pass
                            raise HTTPException(status_code=500, detail="Не удалось отправить фотографию в Telegram") from send_error

                    photo_file_id = sent_message.photo[-1].file_id if sent_message.photo else None
                    photo_message_id = sent_message.message_id

                    chat_id_int = chat_id if isinstance(chat_id, int) else None
                    if chat_id_int is not None and chat_id_int < 0:
                        channel_id = str(chat_id_int).replace('-100', '')
                        photo_link = f"https://t.me/c/{channel_id}/{photo_message_id}"
                    else:
                        photo_link = f"tg://photo?file_id={photo_file_id}" if photo_file_id else None

                    work_record = existing_work or {
                        "work_number": work_number,
                        "participant_user_id": user_id,
                        "votes": {}
                    }
                    msk_tz = pytz.timezone('Europe/Moscow')
                    now_msk = datetime.now(msk_tz)
                    work_record.update({
                        "photo_link": photo_link,
                        "photo_message_id": photo_message_id,
                        "photo_file_id": photo_file_id,
                        "local_path": local_rel_path,
                        "uploaded_at": now_msk.isoformat()
                    })
                    if not existing_work:
                        works.append(work_record)

                    save_drawing_data(drawing_data)
            finally:
                try:
                    bot_session = await bot.get_session()
                    await bot_session.close()
                except Exception:
                    pass

            # Обновляем участника
            participant.photo_link = photo_link
            participant.photo_message_id = photo_message_id

            await session.commit()

            return {
                "success": True,
                "message": "✅ Фотография успешно загружена!",
                "photo_link": photo_link,
                "photo_message_id": photo_message_id,
                "work_number": work_number
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при загрузке фотографии: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/contests/{contest_id}/submit-collection")
async def submit_collection_for_contest(
    contest_id: int,
    request: Request
):
    """Отправка коллекции из 9 NFT для конкурса коллекций"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        user_username = data.get("username")
        nft_links = data.get("nft_links", [])
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id обязателен")
        
        if not isinstance(nft_links, list) or len(nft_links) != 9:
            raise HTTPException(status_code=400, detail="Необходимо отправить ровно 9 ссылок на NFT")
        
        # Валидация ссылок
        for link in nft_links:
            if not isinstance(link, str) or not link.strip():
                raise HTTPException(status_code=400, detail="Все ссылки должны быть непустыми строками")
            # Проверяем формат ссылки (должна быть t.me/nft/...)
            if not link.startswith("t.me/nft/"):
                raise HTTPException(status_code=400, detail=f"Неверный формат ссылки: {link}. Ожидается формат: t.me/nft/название-номер")
        
        async with async_session() as session:
            # Получаем информацию о конкурсе
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            # Проверяем тип конкурса
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')
            if contest_type != 'collection':
                raise HTTPException(status_code=400, detail="Этот конкурс не является конкурсом коллекций")
            
            # Проверяем время окончания приема работ
            if giveaway.submission_end_date:
                msk_tz = pytz.timezone('Europe/Moscow')
                now_msk = datetime.now(msk_tz)
                submission_end = normalize_datetime_to_msk(giveaway.submission_end_date)
                
                if now_msk > submission_end:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Время приема работ истекло. Окончание приема: {submission_end.strftime('%d.%m.%Y %H:%M')}"
                    )
            
            # Проверяем, участвует ли пользователь в конкурсе
            from models import Participant
            participant_result = await session.execute(
                select(Participant).where(
                    Participant.giveaway_id == contest_id,
                    Participant.user_id == user_id
                )
            )
            participant = participant_result.scalars().first()
            
            if not participant:
                raise HTTPException(status_code=404, detail="Вы не участвуете в этом конкурсе. Сначала присоединитесь к конкурсу.")
            
            # Проверяем, не отправлена ли уже коллекция
            if participant.photo_link:  # Используем photo_link для хранения флага отправки коллекции
                raise HTTPException(status_code=400, detail="Вы уже отправили коллекцию для этого конкурса")
            
            # Сохраняем коллекцию в collection_contests.json
            async with collection_data_lock:
                collection_data = load_collection_data()
                contest_key = str(contest_id)
                contest_entry = collection_data.get(contest_key)
                
                if not contest_entry:
                    raise HTTPException(status_code=404, detail="Данные о конкурсе не найдены")
                
                collections = contest_entry.setdefault("collections", [])
                
                # Проверяем, не отправлена ли уже коллекция этим пользователем
                existing_collection = next((c for c in collections if c.get("participant_user_id") == user_id), None)
                if existing_collection:
                    raise HTTPException(status_code=400, detail="Вы уже отправили коллекцию для этого конкурса")
                
                # Получаем username
                final_username = user_username
                if not final_username and participant and participant.username:
                    final_username = participant.username
                
                # Определяем номер коллекции
                collection_number = len(collections) + 1
                
                # Добавляем коллекцию
                collections.append({
                    "collection_number": collection_number,
                    "participant_user_id": user_id,
                    "participant_username": final_username,
                    "nft_links": nft_links,
                    "submitted_at": datetime.now(pytz.timezone('Europe/Moscow')).isoformat(),
                    "votes": {}
                })
                
                save_collection_data(collection_data)
                
                # Обновляем participant, чтобы отметить, что коллекция отправлена
                participant.photo_link = "collection_submitted"  # Используем как флаг
                await session.commit()
            
            return {
                "success": True,
                "message": "✅ Коллекция успешно отправлена!",
                "collection_number": collection_number
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при отправке коллекции: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/contests/{contest_id}/verify-subscription")
async def verify_subscription(contest_id: int, request: Request):
    """Проверка подписки после нажатия 'Выполнил' и добавление участника"""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        user_username = data.get("username")
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id обязателен")
        
        async with async_session() as session:
            # Получаем информацию о конкурсе
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            # Проверяем, не участвует ли уже пользователь
            from models import Participant
            from sqlalchemy.exc import IntegrityError
            existing_participant_result = await session.execute(
                select(Participant).where(
                    Participant.giveaway_id == contest_id,
                    Participant.user_id == user_id
                )
            )
            existing_participant = existing_participant_result.scalars().first()
            if existing_participant:
                # Пользователь уже участвует - возвращаем успешный ответ
                # Для конкурса рисунков проверяем, загружена ли фотография
                contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
                has_photo = bool(existing_participant.photo_link) if existing_participant else False
                if contest_type == 'drawing' and has_photo:
                    return {"success": True, "message": "Вы уже участвуете в этом конкурсе и загрузили фотографию", "already_participating": True, "has_photo": True}
                elif contest_type == 'drawing':
                    return {"success": True, "message": "Вы уже участвуете в этом конкурсе. Загрузите фотографию.", "already_participating": True, "has_photo": False}
                else:
                    return {"success": True, "message": "Вы уже участвуете в этом конкурсе", "already_participating": True}
            
            # Собираем список каналов/чатов для проверки
            required_subscriptions = []
            
            # 1. Канал и чат админа, который создал конкурс
            if giveaway.created_by:
                creator_result = await session.execute(
                    select(User).where(User.telegram_id == giveaway.created_by)
                )
                creator_user = creator_result.scalars().first()
                
                if creator_user:
                    # Канал админа
                    if creator_user.channel_link:
                        channel_username = parse_telegram_username(creator_user.channel_link)
                        if channel_username:
                            required_subscriptions.append({
                                "type": "channel",
                                "link": creator_user.channel_link,
                                "username": channel_username,
                                "name": "Канал админа"
                            })
                    
                    # Чат админа
                    if creator_user.chat_link:
                        chat_username = parse_telegram_username(creator_user.chat_link)
                        if chat_username:
                            required_subscriptions.append({
                                "type": "chat",
                                "link": creator_user.chat_link,
                                "username": chat_username,
                                "name": "Чат админа"
                            })
            
            # 2. Обязательный канал создателя
            creator_channel_link = "t.me/monkeys_giveaways"
            creator_channel_username = parse_telegram_username(creator_channel_link)
            if creator_channel_username:
                required_subscriptions.append({
                    "type": "channel",
                    "link": creator_channel_link,
                    "username": creator_channel_username,
                    "name": "Канал создателя"
                })
            
            # 3. Извлекаем ссылки из условий конкурса (включая дополнительные условия)
            # Парсим поле conditions для поиска ссылок на каналы/чаты
            if giveaway.conditions:
                # Ищем все ссылки вида t.me/username или @username в тексте условий
                link_patterns = [
                    r't\.me/([a-zA-Z0-9_]+)',
                    r'telegram\.me/([a-zA-Z0-9_]+)',
                    r'@([a-zA-Z0-9_]+)'
                ]
                
                found_links = set()  # Используем set, чтобы избежать дубликатов
                for pattern in link_patterns:
                    matches = re.findall(pattern, giveaway.conditions, re.IGNORECASE)
                    for match in matches:
                        if match:
                            username = f"@{match}"
                            link = f"t.me/{match}"
                            found_links.add((username, link, match))
                
                # Добавляем найденные ссылки в список для проверки
                for username, link, name in found_links:
                    # Проверяем, что эта ссылка еще не добавлена
                    if not any(sub["username"] == username for sub in required_subscriptions):
                        required_subscriptions.append({
                            "type": "channel",  # По умолчанию канал
                            "link": link,
                            "username": username,
                            "name": f"Канал {name}" if not name.startswith('@') else f"Канал {name[1:]}"
                        })
            
            # Для конкурса рисунков проверяем deadline приема работ
            contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
            if contest_type == 'drawing' and giveaway.submission_end_date:
                msk_tz = pytz.timezone('Europe/Moscow')
                now_msk = datetime.now(msk_tz)
                submission_end = normalize_datetime_to_msk(giveaway.submission_end_date)
                
                if now_msk > submission_end:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Время приема работ истекло. Окончание приема: {submission_end.strftime('%d.%m.%Y %H:%M')}"
                    )
            
            # Проверяем подписки
            bot = Bot(token=BOT_TOKEN)
            not_subscribed = []
            
            try:
                for sub in required_subscriptions:
                    is_subscribed = await check_subscription(bot, sub["username"], user_id)
                    if not is_subscribed:
                        not_subscribed.append(sub)
            finally:
                # ВАЖНО: используем другое имя переменной для сессии бота, чтобы не перезаписать SQLAlchemy session
                try:
                    bot_session = await bot.get_session()
                    if bot_session:
                        await bot_session.close()
                except Exception:
                    pass
            
            # Если есть неподписанные каналы/чаты, возвращаем их список
            if not_subscribed:
                return {
                    "success": False,
                    "not_subscribed": not_subscribed,
                    "message": "Вы не подписаны на некоторые каналы и чаты"
                }
            
            # Если все подписки есть, добавляем участника
            # Для рандом комментариев photo_link = NULL
            # Для конкурса рисунков photo_link будет установлен позже, когда пользователь отправит фотографию
            try:
                participant = Participant(
                    giveaway_id=contest_id,
                    user_id=user_id,
                    username=user_username,
                    photo_link=None,  # Будет установлен позже для конкурса рисунков
                    photo_message_id=None
                )
                session.add(participant)
                await session.commit()
                
                return {"success": True, "message": "✅ Вы успешно присоединились к конкурсу!"}
            except IntegrityError as e:
                # Если возникла ошибка UNIQUE constraint, значит пользователь уже участвует
                await session.rollback()
                logger.warning(f"Попытка повторного участия пользователя {user_id} в конкурсе {contest_id} (verify_subscription)")
                # Проверяем статус участника еще раз
                existing_participant_result = await session.execute(
                    select(Participant).where(
                        Participant.giveaway_id == contest_id,
                        Participant.user_id == user_id
                    )
                )
                existing_participant = existing_participant_result.scalars().first()
                if existing_participant:
                    contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
                    has_photo = bool(existing_participant.photo_link) if existing_participant else False
                    if contest_type == 'drawing' and has_photo:
                        return {"success": True, "message": "Вы уже участвуете в этом конкурсе и загрузили фотографию", "already_participating": True, "has_photo": True}
                    elif contest_type == 'drawing':
                        return {"success": True, "message": "Вы уже участвуете в этом конкурсе. Загрузите фотографию.", "already_participating": True, "has_photo": False}
                    else:
                        return {"success": True, "message": "Вы уже участвуете в этом конкурсе", "already_participating": True}
                else:
                    # Если участник не найден, но была ошибка UNIQUE - возможно race condition
                    raise HTTPException(status_code=500, detail="Ошибка при добавлении участника. Попробуйте еще раз.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/contests/{contest_id}/participant-status")
async def get_participant_status(contest_id: int, user_id: int = Query(...)):
    """Получить статус участия пользователя в конкурсе (участвует ли, загружена ли фотография/коллекция)"""
    try:
        async with async_session() as session:
            from models import Participant
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            
            # Если конкурс не найден, возвращаем дефолтные значения (не ошибку)
            if not giveaway:
                logger.warning(f"Конкурс {contest_id} не найден при проверке статуса участника {user_id}")
                return {
                    "is_participating": False,
                    "has_photo": False,
                    "has_collection": False
                }
            
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')
            
            result = await session.execute(
                select(Participant).where(
                    Participant.giveaway_id == contest_id,
                    Participant.user_id == user_id
                )
            )
            participant = result.scalars().first()
            
            if not participant:
                return {
                    "is_participating": False,
                    "has_photo": False,
                    "has_collection": False
                }
            
            has_photo_or_collection = bool(participant.photo_link)
            
            return {
                "is_participating": True,
                "has_photo": has_photo_or_collection if contest_type == 'drawing' else False,
                "has_collection": has_photo_or_collection if contest_type == 'collection' else False
            }
    except Exception as e:
        logger.error(f"Ошибка при получении статуса участника для конкурса {contest_id}, пользователь {user_id}: {e}", exc_info=True)
        # Возвращаем дефолтные значения вместо ошибки, чтобы UI мог продолжить работу
        return {
            "is_participating": False,
            "has_photo": False,
            "has_collection": False
        }

@app.get("/api/contests/{contest_id}/voting-queue")
async def get_voting_queue(contest_id: int, user_id: int = Query(...)):
    """Получить список работ для голосования в конкурсе рисунков"""
    from models import Participant

    async with async_session() as session:
        giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
        giveaway = giveaway_result.scalars().first()

        if not giveaway:
            raise HTTPException(status_code=404, detail="Конкурс не найден")

        contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
        if contest_type != 'drawing':
            raise HTTPException(status_code=400, detail="Голосование доступно только для конкурса рисунков")

        participant_result = await session.execute(
            select(Participant).where(
                Participant.giveaway_id == contest_id,
                Participant.user_id == user_id
            )
        )
        participant = participant_result.scalars().first()
        if not participant:
            raise HTTPException(status_code=403, detail="Вы не участвуете в этом конкурсе")

        msk_tz = pytz.timezone('Europe/Moscow')
        now_msk = datetime.now(msk_tz)
        submission_end = normalize_datetime_to_msk(getattr(giveaway, 'submission_end_date', None))
        if submission_end and now_msk <= submission_end:
            raise HTTPException(status_code=400, detail="Голосование еще не началось")
        voting_end = normalize_datetime_to_msk(getattr(giveaway, 'end_date', None))
        if voting_end and now_msk > voting_end:
            raise HTTPException(status_code=400, detail="Голосование завершено")

    async with drawing_data_lock:
        drawing_data = load_drawing_data()
        contest_entry = drawing_data.get(str(contest_id))
        if not contest_entry:
            return {"success": True, "works": [], "total": 0}

        works_raw = contest_entry.get("works", [])
        works_sorted = sorted(works_raw, key=lambda w: w.get("work_number", 0))
        sanitized = []
        for work in works_sorted:
            work_number = work.get("work_number")
            local_path = work.get("local_path")
            participant_user_id = work.get("participant_user_id")
            
            # Пропускаем работы без необходимых данных
            if not work_number or not local_path or not participant_user_id:
                continue
            
            # Пропускаем собственную работу пользователя
            if participant_user_id == user_id:
                continue
            
            votes = work.get("votes", {}) or {}
            sanitized.append({
                "work_number": work_number,
                "image_url": f"/api/drawing-contests/{contest_id}/works/{work_number}/image",
                "already_rated": str(user_id) in votes,
                "rating": votes.get(str(user_id)),
                "is_own": False  # Все работы здесь уже не свои, так как мы их отфильтровали
            })

        return {
            "success": True,
            "works": sanitized,
            "total": len(sanitized)
        }

@app.post("/api/contests/{contest_id}/vote")
async def submit_vote(contest_id: int, request: Request):
    """Сохранить оценку за работу конкурса рисунков"""
    data = await request.json()
    user_id = data.get("user_id")
    work_number = data.get("work_number")
    score = data.get("score")

    if user_id is None or work_number is None or score is None:
        raise HTTPException(status_code=400, detail="Необходимо указать user_id, work_number и score")

    try:
        user_id = int(user_id)
        work_number = int(work_number)
        score = int(score)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные данные для голосования")

    if score < 1 or score > 5:
        raise HTTPException(status_code=400, detail="Оценка должна быть в диапазоне от 1 до 5")

    from models import Participant

    async with async_session() as session:
        giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
        giveaway = giveaway_result.scalars().first()

        if not giveaway:
            raise HTTPException(status_code=404, detail="Конкурс не найден")

        contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
        if contest_type != 'drawing':
            raise HTTPException(status_code=400, detail="Голосование доступно только для конкурса рисунков")

        participant_result = await session.execute(
            select(Participant).where(
                Participant.giveaway_id == contest_id,
                Participant.user_id == user_id
            )
        )
        participant = participant_result.scalars().first()
        if not participant:
            raise HTTPException(status_code=403, detail="Вы не участвуете в этом конкурсе")

        msk_tz = pytz.timezone('Europe/Moscow')
        now_msk = datetime.now(msk_tz)
        submission_end = normalize_datetime_to_msk(getattr(giveaway, 'submission_end_date', None))
        if submission_end and now_msk <= submission_end:
            raise HTTPException(status_code=400, detail="Голосование еще не началось")
        voting_end = normalize_datetime_to_msk(getattr(giveaway, 'end_date', None))
        if voting_end and now_msk > voting_end:
            raise HTTPException(status_code=400, detail="Голосование завершено")

    async with drawing_data_lock:
        drawing_data = load_drawing_data()
        contest_entry = drawing_data.get(str(contest_id))
        if not contest_entry:
            raise HTTPException(status_code=404, detail="Работы для голосования не найдены")

        works = contest_entry.get("works", [])
        work = next((w for w in works if w.get("work_number") == work_number), None)
        if not work:
            raise HTTPException(status_code=404, detail="Работа не найдена")

        if work.get("participant_user_id") == user_id:
            raise HTTPException(status_code=400, detail="Вы не можете оценивать собственную работу")

        votes = work.setdefault("votes", {})
        # Проверяем, не оценил ли пользователь уже эту работу
        if str(user_id) in votes:
            raise HTTPException(status_code=400, detail="Вы уже оценили эту работу. Повторная оценка не разрешена.")
        
        votes[str(user_id)] = score

        remaining = sum(
            1
            for w in works
            if w.get("participant_user_id") != user_id and str(user_id) not in (w.get("votes") or {})
        )

        save_drawing_data(drawing_data)

    return {
        "success": True,
        "score": score,
        "work_number": work_number,
        "remaining": remaining
    }

@app.get("/api/drawing-contests/{contest_id}/works/{work_number}/image")
async def get_drawing_work_image(contest_id: int, work_number: int):
    async with drawing_data_lock:
        drawing_data = load_drawing_data()
        contest_entry = drawing_data.get(str(contest_id))
        if not contest_entry:
            raise HTTPException(status_code=404, detail="Конкурс не найден")
        work = next((w for w in contest_entry.get("works", []) if w.get("work_number") == work_number), None)
        if not work:
            raise HTTPException(status_code=404, detail="Работа не найдена")
        local_path = work.get("local_path")

    if not local_path:
        raise HTTPException(status_code=404, detail="Файл не найден")

    full_path = os.path.abspath(os.path.join(ROOT_DIR, local_path))
    uploads_root = os.path.abspath(DRAWING_UPLOADS_DIR)
    if not full_path.startswith(uploads_root):
        raise HTTPException(status_code=400, detail="Некорректный путь к файлу")
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Файл не найден")

    media_type = mimetypes.guess_type(full_path)[0] or "image/jpeg"
    return FileResponse(full_path, media_type=media_type)

@app.get("/api/contests/{contest_id}/collection-voting-queue")
async def get_collection_voting_queue(contest_id: int, user_id: int = Query(...)):
    """Получить список коллекций для голосования в конкурсе коллекций"""
    from models import Participant

    async with async_session() as session:
        giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
        giveaway = giveaway_result.scalars().first()

        if not giveaway:
            raise HTTPException(status_code=404, detail="Конкурс не найден")

        contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
        if contest_type != 'collection':
            raise HTTPException(status_code=400, detail="Голосование доступно только для конкурса коллекций")

        participant_result = await session.execute(
            select(Participant).where(
                Participant.giveaway_id == contest_id,
                Participant.user_id == user_id
            )
        )
        participant = participant_result.scalars().first()
        if not participant:
            raise HTTPException(status_code=403, detail="Вы не участвуете в этом конкурсе")

        msk_tz = pytz.timezone('Europe/Moscow')
        now_msk = datetime.now(msk_tz)
        submission_end = normalize_datetime_to_msk(getattr(giveaway, 'submission_end_date', None))
        if submission_end and now_msk <= submission_end:
            raise HTTPException(status_code=400, detail="Голосование еще не началось")
        voting_end = normalize_datetime_to_msk(getattr(giveaway, 'end_date', None))
        if voting_end and now_msk > voting_end:
            raise HTTPException(status_code=400, detail="Голосование завершено")

    async with collection_data_lock:
        collection_data = load_collection_data()
        contest_entry = collection_data.get(str(contest_id))
        if not contest_entry:
            return {"success": True, "collections": [], "total": 0}

        collections_raw = contest_entry.get("collections", [])
        collections_sorted = sorted(collections_raw, key=lambda c: c.get("collection_number", 0))
        sanitized = []
        for collection in collections_sorted:
            collection_number = collection.get("collection_number")
            nft_links = collection.get("nft_links", [])
            participant_user_id = collection.get("participant_user_id")
            
            # Пропускаем коллекции без необходимых данных
            if not collection_number or not nft_links or len(nft_links) != 9 or not participant_user_id:
                continue
            
            # Пропускаем собственную коллекцию пользователя
            if participant_user_id == user_id:
                continue
            
            votes = collection.get("votes", {}) or {}
            sanitized.append({
                "collection_number": collection_number,
                "nft_links": nft_links,
                "already_rated": str(user_id) in votes,
                "rating": votes.get(str(user_id)),
                "is_own": False
            })

        return {
            "success": True,
            "collections": sanitized,
            "total": len(sanitized)
        }

@app.post("/api/contests/{contest_id}/vote-collection")
async def submit_collection_vote(contest_id: int, request: Request):
    """Сохранить оценку за коллекцию конкурса коллекций"""
    data = await request.json()
    user_id = data.get("user_id")
    collection_number = data.get("collection_number")
    score = data.get("score")

    if not user_id or collection_number is None or score is None:
        raise HTTPException(status_code=400, detail="Необходимо указать user_id, collection_number и score")

    try:
        user_id = int(user_id)
        collection_number = int(collection_number)
        score = int(score)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные данные для голосования")

    if score < 1 or score > 5:
        raise HTTPException(status_code=400, detail="Оценка должна быть в диапазоне от 1 до 5")

    from models import Participant

    async with async_session() as session:
        giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
        giveaway = giveaway_result.scalars().first()

        if not giveaway:
            raise HTTPException(status_code=404, detail="Конкурс не найден")

        contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
        if contest_type != 'collection':
            raise HTTPException(status_code=400, detail="Голосование доступно только для конкурса коллекций")

        participant_result = await session.execute(
            select(Participant).where(
                Participant.giveaway_id == contest_id,
                Participant.user_id == user_id
            )
        )
        participant = participant_result.scalars().first()
        if not participant:
            raise HTTPException(status_code=403, detail="Вы не участвуете в этом конкурсе")

        msk_tz = pytz.timezone('Europe/Moscow')
        now_msk = datetime.now(msk_tz)
        submission_end = normalize_datetime_to_msk(getattr(giveaway, 'submission_end_date', None))
        if submission_end and now_msk <= submission_end:
            raise HTTPException(status_code=400, detail="Голосование еще не началось")
        voting_end = normalize_datetime_to_msk(getattr(giveaway, 'end_date', None))
        if voting_end and now_msk > voting_end:
            raise HTTPException(status_code=400, detail="Голосование завершено")

    async with collection_data_lock:
        collection_data = load_collection_data()
        contest_entry = collection_data.get(str(contest_id))
        if not contest_entry:
            raise HTTPException(status_code=404, detail="Коллекции для голосования не найдены")

        collections = contest_entry.get("collections", [])
        collection = next((c for c in collections if c.get("collection_number") == collection_number), None)
        if not collection:
            raise HTTPException(status_code=404, detail="Коллекция не найдена")

        if collection.get("participant_user_id") == user_id:
            raise HTTPException(status_code=400, detail="Вы не можете оценивать собственную коллекцию")

        votes = collection.setdefault("votes", {})
        # Проверяем, не оценил ли пользователь уже эту коллекцию
        if str(user_id) in votes:
            raise HTTPException(status_code=400, detail="Вы уже оценили эту коллекцию. Повторная оценка не разрешена.")
        
        votes[str(user_id)] = score

        remaining = sum(
            1
            for c in collections
            if c.get("participant_user_id") != user_id and str(user_id) not in (c.get("votes") or {})
        )

        save_collection_data(collection_data)

    return {
        "success": True,
        "score": score,
        "collection_number": collection_number,
        "remaining": remaining
    }

@app.get("/api/contests/{contest_id}/participants-count")
async def get_participants_count(contest_id: int):
    """Получить количество участников конкурса"""
    try:
        async with async_session() as session:
            from models import Participant
            result = await session.execute(
                select(func.count(Participant.id)).where(Participant.giveaway_id == contest_id)
            )
            count = result.scalar() or 0
            return {"count": count}
    except Exception as e:
        logger.error(f"Ошибка при получении количества участников: {e}", exc_info=True)
        return {"count": 0}

@app.post("/api/contests/{contest_id}/calculate-results")
async def calculate_drawing_contest_results(contest_id: int, current_user_id: int = Query(...)):
    """Подсчитать итоги конкурса рисунков (среднее арифметическое оценок)"""
    try:
        async with async_session() as session:
            giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')
            if contest_type != 'drawing':
                raise HTTPException(status_code=400, detail="Этот конкурс не является конкурсом рисунков")
            
            # Проверяем права доступа - только создатель может подсчитывать итоги
            if giveaway.created_by != current_user_id:
                # Проверяем, является ли пользователь админом или создателем
                user_result = await session.execute(
                    select(User).where(User.telegram_id == current_user_id)
                )
                user = user_result.scalars().first()
                if not user or (user.role != "creator" and user.role != "admin"):
                    raise HTTPException(status_code=403, detail="Только создатель конкурса может подсчитывать итоги")
                if user.role == "admin" and giveaway.created_by != current_user_id:
                    raise HTTPException(status_code=403, detail="Только создатель конкурса может подсчитывать итоги")
            
            # Проверяем, что время голосования истекло
            msk_tz = pytz.timezone('Europe/Moscow')
            now_msk = datetime.now(msk_tz)
            voting_end = normalize_datetime_to_msk(getattr(giveaway, 'end_date', None))
            if voting_end and now_msk <= voting_end:
                raise HTTPException(status_code=400, detail="Время голосования еще не истекло")
            
            # Загружаем данные о работах
            async with drawing_data_lock:
                drawing_data = load_drawing_data()
                contest_entry = drawing_data.get(str(contest_id))
                if not contest_entry:
                    raise HTTPException(status_code=404, detail="Данные о работах не найдены")
                
                works = contest_entry.get("works", [])
                if not works:
                    raise HTTPException(status_code=400, detail="Нет работ для подсчета")
                
                # Подсчитываем среднее арифметическое для каждой работы
                results = []
                from models import Participant
                
                for work in works:
                    work_number = work.get("work_number")
                    participant_user_id = work.get("participant_user_id")
                    votes = work.get("votes", {}) or {}
                    
                    if not work_number or not participant_user_id:
                        continue
                    
                    # Получаем username участника из таблицы User (приоритет) или Participant
                    username = None
                    user_result = await session.execute(
                        select(User).where(User.telegram_id == participant_user_id)
                    )
                    user = user_result.scalars().first()
                    if user and user.username:
                        username = user.username
                    else:
                        # Если username нет в User, берем из Participant
                        participant_result = await session.execute(
                            select(Participant).where(
                                Participant.giveaway_id == contest_id,
                                Participant.user_id == participant_user_id
                            )
                        )
                        participant = participant_result.scalars().first()
                        if participant:
                            username = participant.username
                    
                    # Подсчитываем среднее арифметическое
                    scores = [int(score) for score in votes.values() if score]
                    if scores:
                        average_score = sum(scores) / len(scores)
                    else:
                        average_score = 0.0
                    
                    results.append({
                        "work_number": work_number,
                        "participant_user_id": participant_user_id,
                        "username": username,
                        "average_score": round(average_score, 2),
                        "votes_count": len(scores),
                        "photo_link": work.get("photo_link"),
                        "local_path": work.get("local_path")
                    })
                
                # Сортируем по среднему баллу (по убыванию)
                results.sort(key=lambda x: x["average_score"], reverse=True)
                
                # Добавляем место (place) для каждой работы
                for idx, result in enumerate(results):
                    result["place"] = idx + 1
                
                # Сохраняем результаты в drawing_data
                msk_tz = pytz.timezone('Europe/Moscow')
                now_msk = datetime.now(msk_tz)
                contest_entry["results_calculated"] = True
                contest_entry["results_calculated_at"] = now_msk.isoformat()
                contest_entry["results"] = results
                
                save_drawing_data(drawing_data)
            
            return {
                "success": True,
                "message": "Итоги успешно подсчитаны",
                "results_count": len(results)
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при подсчете итогов конкурса {contest_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/contests/{contest_id}/results")
async def get_drawing_contest_results(contest_id: int):
    """Получить итоги конкурса рисунков"""
    try:
        async with async_session() as session:
            giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')
            if contest_type != 'drawing':
                raise HTTPException(status_code=400, detail="Этот конкурс не является конкурсом рисунков")
            
            # Получаем призы
            prize_links = giveaway.prize_links if hasattr(giveaway, 'prize_links') and giveaway.prize_links else []
            if not isinstance(prize_links, list):
                prize_links = []
            
            # Загружаем данные о результатах
            async with drawing_data_lock:
                drawing_data = load_drawing_data()
                contest_entry = drawing_data.get(str(contest_id))
                if not contest_entry:
                    # Если данных нет, возвращаем что итоги не подсчитаны (это нормально для нового конкурса)
                    logger.info(f"Данные о конкурсе {contest_id} не найдены в drawing_contests.json, возвращаем results_calculated=false")
                    return {
                        "results_calculated": False,
                        "message": "Итоги еще не подсчитаны"
                    }
                
                results_calculated = contest_entry.get("results_calculated", False)
                if not results_calculated:
                    return {
                        "results_calculated": False,
                        "message": "Итоги еще не подсчитаны"
                    }
                
                results = contest_entry.get("results", [])
                
                # Обновляем username из таблицы User для каждого результата
                for result in results:
                    participant_user_id = result.get("participant_user_id")
                    if participant_user_id:
                        user_result = await session.execute(
                            select(User).where(User.telegram_id == participant_user_id)
                        )
                        user = user_result.scalars().first()
                        if user and user.username:
                            result["username"] = user.username
                    
                    place = result.get("place", 0)
                    if place > 0 and place <= len(prize_links):
                        result["prize_link"] = prize_links[place - 1]
                    else:
                        result["prize_link"] = None
                
                return {
                    "results_calculated": True,
                    "results": results,
                    "prize_links": prize_links
                }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении итогов конкурса {contest_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/contests/{contest_id}/calculate-collection-results")
async def calculate_collection_contest_results(contest_id: int, current_user_id: int = Query(...)):
    """Подсчитать итоги конкурса коллекций (среднее арифметическое оценок)"""
    try:
        async with async_session() as session:
            giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')
            if contest_type != 'collection':
                raise HTTPException(status_code=400, detail="Этот конкурс не является конкурсом коллекций")
            
            # Проверяем права доступа
            if hasattr(giveaway, 'created_by') and giveaway.created_by and giveaway.created_by != current_user_id:
                raise HTTPException(status_code=403, detail="Только создатель конкурса может подсчитывать итоги")
            
            # Проверяем, что время голосования истекло
            msk_tz = pytz.timezone('Europe/Moscow')
            now_msk = datetime.now(msk_tz)
            voting_end = normalize_datetime_to_msk(getattr(giveaway, 'end_date', None))
            if voting_end and now_msk <= voting_end:
                raise HTTPException(status_code=400, detail="Время голосования еще не истекло")
            
            # Загружаем данные о коллекциях
            async with collection_data_lock:
                collection_data = load_collection_data()
                contest_entry = collection_data.get(str(contest_id))
                if not contest_entry:
                    raise HTTPException(status_code=404, detail="Данные о коллекциях не найдены")
                
                collections = contest_entry.get("collections", [])
                if not collections:
                    raise HTTPException(status_code=400, detail="Нет коллекций для подсчета")
                
                # Подсчитываем среднее арифметическое для каждой коллекции
                results = []
                from models import Participant
                
                for collection in collections:
                    collection_number = collection.get("collection_number")
                    participant_user_id = collection.get("participant_user_id")
                    votes = collection.get("votes", {}) or {}
                    nft_links = collection.get("nft_links", [])
                    
                    if not collection_number or not participant_user_id:
                        continue
                    
                    # Получаем username участника из таблицы User (приоритет) или Participant
                    username = None
                    user_result = await session.execute(
                        select(User).where(User.telegram_id == participant_user_id)
                    )
                    user = user_result.scalars().first()
                    if user and user.username:
                        username = user.username
                    else:
                        # Если username нет в User, берем из Participant
                        participant_result = await session.execute(
                            select(Participant).where(
                                Participant.giveaway_id == contest_id,
                                Participant.user_id == participant_user_id
                            )
                        )
                        participant = participant_result.scalars().first()
                        if participant:
                            username = participant.username
                    
                    # Подсчитываем среднее арифметическое
                    scores = [int(score) for score in votes.values() if score]
                    if scores:
                        average_score = sum(scores) / len(scores)
                    else:
                        average_score = 0.0
                    
                    results.append({
                        "collection_number": collection_number,
                        "participant_user_id": participant_user_id,
                        "username": username,
                        "average_score": round(average_score, 2),
                        "votes_count": len(scores),
                        "nft_links": nft_links
                    })
                
                # Сортируем по среднему баллу (по убыванию)
                results.sort(key=lambda x: x["average_score"], reverse=True)
                
                # Добавляем место (place) для каждой коллекции
                for idx, result in enumerate(results):
                    result["place"] = idx + 1
                
                # Сохраняем результаты в collection_data
                msk_tz = pytz.timezone('Europe/Moscow')
                now_msk = datetime.now(msk_tz)
                contest_entry["results_calculated"] = True
                contest_entry["results_calculated_at"] = now_msk.isoformat()
                contest_entry["results"] = results
                
                save_collection_data(collection_data)
            
            return {
                "success": True,
                "message": "Итоги успешно подсчитаны",
                "results_count": len(results)
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при подсчете итогов конкурса коллекций {contest_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/contests/{contest_id}/collection-results")
async def get_collection_contest_results(contest_id: int):
    """Получить итоги конкурса коллекций"""
    try:
        async with async_session() as session:
            giveaway_result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
            giveaway = giveaway_result.scalars().first()
            
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')
            if contest_type != 'collection':
                raise HTTPException(status_code=400, detail="Этот конкурс не является конкурсом коллекций")
            
            # Получаем призы
            prize_links = giveaway.prize_links if hasattr(giveaway, 'prize_links') and giveaway.prize_links else []
            if not isinstance(prize_links, list):
                prize_links = []
            
            # Загружаем данные о результатах
            async with collection_data_lock:
                collection_data = load_collection_data()
                contest_entry = collection_data.get(str(contest_id))
                if not contest_entry:
                    return {
                        "results_calculated": False,
                        "message": "Итоги еще не подсчитаны"
                    }
                
                results_calculated = contest_entry.get("results_calculated", False)
                if not results_calculated:
                    return {
                        "results_calculated": False,
                        "message": "Итоги еще не подсчитаны"
                    }
                
                results = contest_entry.get("results", [])
                
                # Обновляем username из таблицы User для каждого результата
                for result in results:
                    participant_user_id = result.get("participant_user_id")
                    if participant_user_id:
                        user_result = await session.execute(
                            select(User).where(User.telegram_id == participant_user_id)
                        )
                        user = user_result.scalars().first()
                        if user and user.username:
                            result["username"] = user.username
                    
                    place = result.get("place", 0)
                    if place > 0 and place <= len(prize_links):
                        result["prize_link"] = prize_links[place - 1]
                    else:
                        result["prize_link"] = None
                
                return {
                    "results_calculated": True,
                    "results": results,
                    "prize_links": prize_links
                }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении итогов конкурса коллекций {contest_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/contests/{contest_id}/confirm-winners")
async def confirm_contest_winners(contest_id: int, current_user_id: int = Query(default=None)):
    """Подтверждает победителей конкурса (финализирует выбор).

    Подтверждать победителей может только владелец конкурса (created_by).
    """
    try:
        # Проверяем права доступа
        async with async_session() as session:
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()
            if not giveaway:
                raise HTTPException(status_code=404, detail="Конкурс не найден")

            if current_user_id is not None and giveaway.created_by is not None:
                try:
                    if int(giveaway.created_by) != int(current_user_id):
                        raise HTTPException(
                            status_code=403,
                            detail="Подтверждать победителей может только создатель конкурса",
                        )
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=403,
                        detail="Подтверждать победителей может только создатель конкурса",
                    )

        result = await confirm_winners(contest_id)
        return {"success": True, "message": "Победители подтверждены"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при подтверждении победителей: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/contests/{contest_id}")
async def delete_contest(contest_id: int, current_user_id: int = Query(None)):
    """Удалить конкурс. Админ может удалять только свои конкурсы."""
    async with async_session() as session:
        try:
            result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
            contest = result.scalars().first()
            if not contest:
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            # Проверяем права доступа
            if current_user_id:
                user_result = await session.execute(
                    select(User).where(User.telegram_id == current_user_id)
                )
                user = user_result.scalars().first()
                
                if user:
                    if user.role == "admin":
                        # Админ может удалять только свои конкурсы
                        if contest.created_by != current_user_id:
                            raise HTTPException(status_code=403, detail="Вы можете удалять только свои конкурсы")
                    elif user.role == "creator":
                        # Создатель может удалять любые конкурсы
                        pass
                    else:
                        # Обычный пользователь не может удалять конкурсы
                        raise HTTPException(status_code=403, detail="Недостаточно прав для удаления конкурса")
            
            # Проверяем, подтвержден ли конкурс
            if hasattr(contest, 'is_confirmed') and contest.is_confirmed:
                raise HTTPException(status_code=403, detail="Нельзя удалить подтвержденный конкурс")
            
            # Удаляем всех победителей конкурса
            from models import Winner, Participant
            winners_result = await session.execute(
                select(Winner).where(Winner.giveaway_id == contest_id)
            )
            winners = winners_result.scalars().all()
            for winner in winners:
                await session.delete(winner)
            
            # Удаляем всех участников конкурса
            participants_result = await session.execute(
                select(Participant).where(Participant.giveaway_id == contest_id)
            )
            participants = participants_result.scalars().all()
            for participant in participants:
                await session.delete(participant)
            
            # Проверяем тип конкурса - если это конкурс рисунков, удаляем данные из файла
            contest_type = getattr(contest, 'contest_type', 'random_comment')
            if contest_type == 'drawing':
                async with drawing_data_lock:
                    drawing_data = load_drawing_data()
                    contest_key = str(contest_id)
                    if contest_key in drawing_data:
                        # Удаляем данные о конкурсе из файла
                        del drawing_data[contest_key]
                        save_drawing_data(drawing_data)
                        logger.info(f"🗑️ Удалены данные конкурса рисунков {contest_id} из файла drawing_contests.json")
                    
                    # Также удаляем папку с загруженными фотографиями
                    try:
                        import shutil
                        work_dir = os.path.join(DRAWING_UPLOADS_DIR, f"contest_{contest_id}")
                        if os.path.exists(work_dir):
                            shutil.rmtree(work_dir)
                            logger.info(f"🗑️ Удалена папка с фотографиями конкурса {contest_id}: {work_dir}")
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось удалить папку с фотографиями конкурса {contest_id}: {e}")
            
            # Удаляем сам конкурс
            await session.delete(contest)
            await session.commit()
            return {"success": True, "message": "Конкурс удален"}
        except HTTPException:
            raise
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admins/{admin_id}")
async def delete_admin(admin_id: int):
    """Удалить администратора (изменить роль на user)"""
    async with async_session() as session:
        try:
            result = await session.execute(select(User).where(User.telegram_id == admin_id))
            user = result.scalars().first()
            if not user:
                raise HTTPException(status_code=404, detail="Администратор не найден")
            user.role = "user"
            await session.commit()
            return {"success": True, "message": "Администратор удален"}
        except HTTPException:
            raise
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admins/{admin_id}")
async def update_admin(admin_id: int, request: Request):
    """Обновить данные администратора"""
    data = await request.json()
    async with async_session() as session:
        try:
            result = await session.execute(select(User).where(User.telegram_id == admin_id))
            user = result.scalars().first()
            if not user:
                raise HTTPException(status_code=404, detail="Администратор не найден")
            
            # Обновляем поля, если они переданы
            channel_link = data.get("channel_link")
            chat_link = data.get("chat_link")
            if channel_link is not None:
                user.channel_link = channel_link if channel_link else None
            if chat_link is not None:
                user.chat_link = chat_link if chat_link else None
            
            await session.commit()
            return {"success": True, "message": "Администратор обновлен"}
        except HTTPException:
            raise
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/contests/{contest_id}")
async def update_contest(contest_id: int, request: Request):
    """Обновить данные конкурса"""
    try:
        data = await request.json()
        logger.info(f"Обновление конкурса {contest_id}: получены данные {list(data.keys())}")
    except Exception as e:
        logger.error(f"Ошибка парсинга JSON в update_contest: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Ошибка парсинга данных: {str(e)}")
    
    async with async_session() as session:
        try:
            result = await session.execute(select(Giveaway).where(Giveaway.id == contest_id))
            contest = result.scalars().first()
            if not contest:
                logger.warning(f"Конкурс {contest_id} не найден")
                raise HTTPException(status_code=404, detail="Конкурс не найден")
            
            # Проверяем права доступа
            current_user_id = data.get("current_user_id")
            if current_user_id:
                user_result = await session.execute(
                    select(User).where(User.telegram_id == current_user_id)
                )
                user = user_result.scalars().first()
                
                if user:
                    if user.role == "admin":
                        # Админ может изменять только свои конкурсы
                        if contest.created_by != current_user_id:
                            raise HTTPException(status_code=403, detail="Вы можете изменять только свои конкурсы")
                    elif user.role == "creator":
                        # Создатель может изменять любые конкурсы
                        pass
                    else:
                        # Обычный пользователь не может изменять конкурсы
                        raise HTTPException(status_code=403, detail="Недостаточно прав для изменения конкурса")
            
            # Обновляем поля, если они переданы
            if "title" in data or "name" in data:
                contest.name = data.get("title") or data.get("name")
            if "prize" in data:
                contest.prize = data.get("prize")
            if "end_date" in data or "end_at" in data:
                end_date = data.get("end_date") or data.get("end_at")
                if end_date:
                    try:
                        # Попытка распарсить дату в разных форматах
                        if isinstance(end_date, str):
                            # Убираем Z и обрабатываем
                            end_date_clean = end_date.replace('Z', '').replace('+00:00', '')
                            if 'T' in end_date_clean:
                                contest.end_date = datetime.fromisoformat(end_date_clean)
                            else:
                                contest.end_date = datetime.fromisoformat(f"{end_date_clean}T00:00:00")
                        else:
                            contest.end_date = end_date
                    except Exception:
                        pass  # Игнорируем ошибки парсинга даты
            if "start_at" in data:
                start_at = data.get("start_at")
                # start_at может храниться в другом поле или не поддерживаться
            if "post_link" in data:
                new_post_link = data.get("post_link")
                # Получаем тип конкурса (возможно, он был обновлен выше, если contest_type обновлялся раньше)
                contest_type = getattr(contest, 'contest_type', 'random_comment') if hasattr(contest, 'contest_type') else 'random_comment'
                
                # Валидация в зависимости от типа конкурса
                if contest_type == "random_comment":
                    # Для рандом комментариев post_link обязателен
                    if not new_post_link or not new_post_link.strip():
                        raise HTTPException(status_code=400, detail="❌ Для конкурса рандом комментариев обязательна ссылка на пост (post_link)")
                    
                    # Проверяем уникальность post_link только для конкурсов рандом комментариев
                    existing_contest = await session.execute(
                        select(Giveaway).where(
                            Giveaway.post_link == new_post_link,
                            Giveaway.id != contest_id,  # Исключаем текущий конкурс
                            Giveaway.post_link.isnot(None),
                            Giveaway.post_link != ""
                        )
                    )
                    existing = existing_contest.scalars().first()
                    if existing:
                        raise HTTPException(status_code=400, detail=f"❌ Этот пост уже используется в конкурсе (ID: {existing.id}). Один пост может иметь только один конкурс.")
                    
                    contest.post_link = new_post_link
                else:
                    # Для конкурсов рисунков post_link не требуется и может быть пустым
                    contest.post_link = new_post_link if new_post_link and new_post_link.strip() else None
            if "discussion_group_link" in data:
                contest.discussion_group_link = data.get("discussion_group_link") or None
            if "conditions" in data:
                contest.conditions = data.get("conditions")
            if "winners_count" in data:
                contest.winners_count = data.get("winners_count")
            if "submission_end_date" in data:
                submission_end_date = data.get("submission_end_date")
                if submission_end_date:
                    try:
                        if isinstance(submission_end_date, str):
                            submission_end_date_clean = submission_end_date.replace('Z', '').replace('+00:00', '')
                            if 'T' in submission_end_date_clean:
                                contest.submission_end_date = datetime.fromisoformat(submission_end_date_clean)
                            else:
                                contest.submission_end_date = datetime.fromisoformat(f"{submission_end_date_clean}T00:00:00")
                        else:
                            contest.submission_end_date = submission_end_date
                    except Exception as e:
                        logger.warning(f"Ошибка парсинга submission_end_date: {e}")
                else:
                    contest.submission_end_date = None
            if "contest_type" in data:
                new_contest_type = data.get("contest_type")
                old_contest_type = getattr(contest, 'contest_type', 'random_comment')
                contest.contest_type = new_contest_type
                
                # Валидация полей в зависимости от типа конкурса при обновлении
                if new_contest_type == "drawing":
                    # Для конкурса рисунков требуется submission_end_date
                    if "submission_end_date" not in data and not contest.submission_end_date:
                        raise HTTPException(status_code=400, detail="❌ Для конкурса рисунков обязательна дата окончания приема работ (submission_end_date)")
                    # post_link не требуется для конкурса рисунков, но если его убрали - это нормально
                elif new_contest_type == "random_comment":
                    # Для рандом комментариев требуется post_link
                    if "post_link" in data:
                        new_post_link = data.get("post_link")
                        if not new_post_link or not new_post_link.strip():
                            raise HTTPException(status_code=400, detail="❌ Для конкурса рандом комментариев обязательна ссылка на пост (post_link)")
                    elif not contest.post_link or not contest.post_link.strip():
                        raise HTTPException(status_code=400, detail="❌ Для конкурса рандом комментариев обязательна ссылка на пост (post_link)")
                    # submission_end_date не требуется для рандом комментариев - можно обнулить
                    if old_contest_type == "drawing" and "submission_end_date" not in data:
                        contest.submission_end_date = None
            
            if "prize_links" in data:
                prize_links = data.get("prize_links")
                logger.info(f"Обновление призов для конкурса {contest_id}: получено {len(prize_links) if isinstance(prize_links, list) else 0} призов, тип: {type(prize_links)}")
                if isinstance(prize_links, list):
                    contest.prize_links = prize_links
                    logger.info(f"Призы сохранены в БД: {prize_links}")
                else:
                    contest.prize_links = None
                    logger.warning(f"prize_links не является списком: {type(prize_links)}, значение: {prize_links}")
            
            # Проверяем, подтвержден ли конкурс
            if hasattr(contest, 'is_confirmed') and contest.is_confirmed:
                raise HTTPException(status_code=403, detail="Нельзя редактировать подтвержденный конкурс")
            
            # Финальная валидация полей в зависимости от типа конкурса
            # (проверяем после всех обновлений, чтобы убедиться, что все поля корректны)
            final_contest_type = getattr(contest, 'contest_type', 'random_comment') if hasattr(contest, 'contest_type') else 'random_comment'
            
            if final_contest_type == "drawing":
                # Для конкурса рисунков требуется submission_end_date
                if not contest.submission_end_date:
                    raise HTTPException(status_code=400, detail="❌ Для конкурса рисунков обязательна дата окончания приема работ (submission_end_date)")
                # post_link не требуется для конкурса рисунков
            elif final_contest_type == "random_comment":
                # Для рандом комментариев требуется post_link
                if not contest.post_link or not contest.post_link.strip():
                    raise HTTPException(status_code=400, detail="❌ Для конкурса рандом комментариев обязательна ссылка на пост (post_link)")
                # submission_end_date не требуется для рандом комментариев
            
            await session.commit()
            # Обновляем объект из БД, чтобы убедиться, что изменения сохранены
            await session.refresh(contest)
            logger.info(f"Конкурс {contest_id} успешно обновлен. prize_links после сохранения: {contest.prize_links}")
            return {"success": True, "message": "Конкурс обновлен"}
        except HTTPException:
            raise
        except Exception as e:
            await session.rollback()
            logger.error(f"Ошибка при обновлении конкурса {contest_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Ошибка при обновлении конкурса: {str(e)}")

@app.get("/api/nft-preview")
async def get_nft_preview(nft_link: str = Query(...)):
    """Получить превью изображения NFT из Telegram ссылки"""
    from fastapi.responses import RedirectResponse, Response
    import aiohttp
    import re
    
    try:
        # Нормализуем ссылку
        if not nft_link.startswith('http'):
            nft_link = 'https://' + nft_link
        
        # Сначала пытаемся получить изображение через парсинг HTML страницы
        # Это более надежный способ для Telegram NFT
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                async with session.get(nft_link, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        
                        # Ищем og:image в мета-тегах
                        og_image_match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                        if og_image_match:
                            image_url = og_image_match.group(1)
                            logger.info(f"✅ Найдено изображение через og:image: {image_url}")
                            # Используем 301 (Permanent Redirect) вместо 307 для лучшей совместимости
                            return RedirectResponse(url=image_url, status_code=301)
                        
                        # Ищем обычный meta image
                        image_match = re.search(r'<meta\s+name=["\']image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
                        if image_match:
                            image_url = image_match.group(1)
                            logger.info(f"✅ Найдено изображение через meta image: {image_url}")
                            return RedirectResponse(url=image_url, status_code=301)
                        
                        # Ищем img теги с классом или id, связанными с NFT
                        img_match = re.search(r'<img[^>]+(?:class|id)=["\'][^"\']*(?:nft|preview|image|photo)[^"\']*["\'][^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
                        if img_match:
                            image_url = img_match.group(1)
                            # Если относительный URL, делаем его абсолютным
                            if image_url.startswith('/'):
                                from urllib.parse import urljoin
                                image_url = urljoin(nft_link, image_url)
                            logger.info(f"✅ Найдено изображение через img тег: {image_url}")
                            return RedirectResponse(url=image_url, status_code=301)
        except Exception as e:
            logger.debug(f"Не удалось получить изображение из HTML: {e}")
        
        # Альтернативный способ: пытаемся получить превью через Telegram Bot API
        try:
            from aiogram import Bot
            from config import BOT_TOKEN
            
            bot = Bot(token=BOT_TOKEN)
            try:
                preview = await bot.get_web_page_preview(url=nft_link)
                
                if preview and hasattr(preview, 'photo') and preview.photo:
                    photo = preview.photo
                    if hasattr(photo, 'sizes') and photo.sizes:
                        largest = max(photo.sizes, key=lambda x: getattr(x, 'w', 0) * getattr(x, 'h', 0))
                        if hasattr(largest, 'location'):
                            file_id = largest.location.file_id if hasattr(largest.location, 'file_id') else None
                            if file_id:
                                file = await bot.get_file(file_id)
                                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
                                session = await bot.get_session()
                                if session:
                                    await session.close()
                                logger.info(f"✅ Найдено изображение через Telegram Bot API: {file_url}")
                                return RedirectResponse(url=file_url, status_code=301)
                
                session = await bot.get_session()
                if session:
                    await session.close()
            except Exception as e:
                session = await bot.get_session()
                if session:
                    await session.close()
                logger.debug(f"Telegram Bot API не смог получить превью: {e}")
        except Exception as e:
            logger.debug(f"Ошибка при использовании Telegram Bot API: {e}")
        
        # Если ничего не получилось, возвращаем прозрачный пиксель
        logger.warning(f"⚠️ Не удалось получить изображение для NFT: {nft_link}")
        transparent_pixel = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82'
        return Response(content=transparent_pixel, media_type="image/png")
    except Exception as e:
        logger.error(f"Ошибка в get_nft_preview: {e}", exc_info=True)
        transparent_pixel = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82'
        return Response(content=transparent_pixel, media_type="image/png")

@app.get("/api/chat-info")
async def get_chat_info(link: str = Query(...)):
    """Получить название чата/канала по ссылке через Telegram Bot API"""
    try:
        # Извлекаем username из ссылки
        match = re.search(r'(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)|@([a-zA-Z0-9_]+)', link)
        if not match:
            return {"title": link, "username": None, "error": "Неверный формат ссылки"}
        
        username = match.group(1) or match.group(2)
        if not username:
            return {"title": link, "username": None, "error": "Не удалось извлечь username"}
        
        bot = Bot(token=BOT_TOKEN)
        try:
            # Пытаемся получить информацию о чате/канале
            chat = await bot.get_chat(f"@{username}")
            title = chat.title if chat.title else f"@{username}"
            try:
                session = await bot.get_session()
                await session.close()
            except Exception:
                pass
            return {"title": title, "username": username}
        except Exception as e:
            try:
                session = await bot.get_session()
                await session.close()
            except Exception:
                pass
            # Если не удалось получить название, возвращаем username
            return {"title": f"@{username}", "username": username, "error": str(e)}
    except Exception as e:
        return {"title": link, "username": None, "error": str(e)}

# ------------------- MESSAGES API -------------------

@app.post("/api/messages")
async def create_message(request: Request):
    """Создать сообщение (от админа/пользователя к создателю)"""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    from_user_id = data.get("from_user_id")
    message_text = data.get("message_text", "").strip()
    
    if not from_user_id:
        raise HTTPException(status_code=400, detail="from_user_id is required")
    
    if not message_text:
        raise HTTPException(status_code=400, detail="message_text is required")
    
    try:
        async with async_session() as session:
            message = Message(
                from_user_id=int(from_user_id),
                to_user_id=None,  # Сообщения для создателя
                message_text=message_text,
                status="pending",
                created_at=datetime.now(timezone.utc)
            )
            session.add(message)
            await session.commit()
        return {"success": True, "message_id": message.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/api/messages")
async def list_messages(user_id: int = Query(None), status: str = Query(None)):
    """Получить список сообщений"""
    try:
        async with async_session() as session:
            query = select(Message)
            
            # Если передан user_id, фильтруем сообщения для создателя (все pending)
            if user_id:
                # Для создателя показываем все pending сообщения
                query = query.where(Message.status == (status or "pending"))
            else:
                if status:
                    query = query.where(Message.status == status)
            
            query = query.order_by(Message.created_at.desc())
            result = await session.execute(query)
            messages = result.scalars().all()
            
            return [{
                "id": m.id,
                "from_user_id": m.from_user_id,
                "message_text": m.message_text,
                "status": m.status,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "responded_at": m.responded_at.isoformat() if m.responded_at else None
            } for m in messages]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/messages/unread-count")
async def get_unread_count():
    """Получить количество непрочитанных сообщений (pending)"""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Message).where(Message.status == "pending")
            )
            messages = result.scalars().all()
            count = len(messages)
            return {"count": count}
    except Exception as e:
        logger.error(f"Ошибка при получении количества непрочитанных сообщений: {e}", exc_info=True)
        # Возвращаем 0 вместо ошибки, чтобы не ломать UI
        return {"count": 0}

@app.put("/api/messages/{message_id}/respond")
async def respond_to_message(message_id: int, request: Request):
    """Одобрить или отклонить сообщение и отправить ответ пользователю"""
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    action = data.get("action")  # "approve" или "reject"
    if action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")
    
    try:
        async with async_session() as session:
            # Получаем сообщение
            result = await session.execute(
                select(Message).where(Message.id == message_id)
            )
            message = result.scalars().first()
            
            if not message:
                raise HTTPException(status_code=404, detail="Message not found")
            
            if message.status != "pending":
                raise HTTPException(status_code=400, detail="Message already responded")
            
            # Обновляем статус
            message.status = "approved" if action == "approve" else "rejected"
            message.responded_at = datetime.now(timezone.utc)
            
            # Отправляем сообщение пользователю через Telegram бота
            try:
                bot = Bot(token=BOT_TOKEN)
                from_user_id = message.from_user_id
                
                if action == "approve":
                    response_text = "✅ Ваше сообщение было одобрено!"
                else:
                    response_text = "❌ Ваше сообщение было отклонено."
                
                await bot.send_message(
                    chat_id=from_user_id,
                    text=response_text
                )
                try:
                    bot_session = await bot.get_session()
                    await bot_session.close()
                except Exception:
                    pass
            except Exception as bot_error:
                # Логируем ошибку, но не прерываем процесс
                print(f"⚠️ Ошибка отправки сообщения в Telegram: {bot_error}")
            
            await session.commit()
            return {"success": True, "status": message.status}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Drawing contest endpoints removed - all drawing contest functionality has been rolled back

DRAWING_DATA_FILE = os.path.join(ROOT_DIR, "drawing_contests.json")
DRAWING_UPLOADS_DIR = os.path.join(ROOT_DIR, "drawing_uploads")
drawing_data_lock = asyncio.Lock()

COLLECTION_DATA_FILE = os.path.join(ROOT_DIR, "collection_contests.json")
collection_data_lock = asyncio.Lock()


def _ensure_dir(path: str):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        logger.error(f"Не удалось создать директорию {path}: {e}")


def load_drawing_data() -> dict:
    if not os.path.exists(DRAWING_DATA_FILE):
        return {}
    try:
        with open(DRAWING_DATA_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                # Файл пустой, возвращаем пустой словарь
                logger.warning(f"Файл {DRAWING_DATA_FILE} пустой, возвращаем пустой словарь")
                return {}
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON в файле данных конкурсов рисунков: {e}")
        # Если файл поврежден, создаем резервную копию и возвращаем пустой словарь
        try:
            backup_path = DRAWING_DATA_FILE + ".backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            if os.path.exists(DRAWING_DATA_FILE):
                import shutil
                shutil.copy2(DRAWING_DATA_FILE, backup_path)
                logger.warning(f"Создана резервная копия поврежденного файла: {backup_path}")
        except Exception:
            pass
        return {}
    except Exception as e:
        logger.error(f"Не удалось прочитать файл данных конкурсов рисунков: {e}")
        return {}


def save_drawing_data(data: dict) -> None:
    _ensure_dir(os.path.dirname(DRAWING_DATA_FILE) or ROOT_DIR)
    temp_path = DRAWING_DATA_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, DRAWING_DATA_FILE)
    except Exception as e:
        logger.error(f"Не удалось сохранить файл данных конкурсов рисунков: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


_ensure_dir(DRAWING_UPLOADS_DIR)


def load_collection_data() -> dict:
    if not os.path.exists(COLLECTION_DATA_FILE):
        return {}
    try:
        with open(COLLECTION_DATA_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                logger.warning(f"Файл {COLLECTION_DATA_FILE} пустой, возвращаем пустой словарь")
                return {}
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON в файле данных конкурсов коллекций: {e}")
        try:
            backup_path = COLLECTION_DATA_FILE + ".backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            if os.path.exists(COLLECTION_DATA_FILE):
                import shutil
                shutil.copy2(COLLECTION_DATA_FILE, backup_path)
                logger.warning(f"Создана резервная копия поврежденного файла: {backup_path}")
        except Exception:
            pass
        return {}
    except Exception as e:
        logger.error(f"Не удалось прочитать файл данных конкурсов коллекций: {e}")
        return {}


def save_collection_data(data: dict) -> None:
    _ensure_dir(os.path.dirname(COLLECTION_DATA_FILE) or ROOT_DIR)
    temp_path = COLLECTION_DATA_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, COLLECTION_DATA_FILE)
    except Exception as e:
        logger.error(f"Не удалось сохранить файл данных конкурсов коллекций: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
