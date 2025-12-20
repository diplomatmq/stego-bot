from aiogram import Dispatcher, types, Bot
from aiogram.types import Message
from aiogram.utils.exceptions import ChatNotFound, MessageNotModified
from db import get_session, async_session, IS_SQLITE
from models import Giveaway, Winner, Comment
from telethon_comments import collect_comments_via_telethon, get_comments_file_path, pick_random_winners_from_file
from helpers import log_action
from post_parser import parse_telegram_link, parse_telegram_chat_link, get_message_link
from sqlalchemy.future import select
from sqlalchemy import or_, and_
from config import BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH
from datetime import datetime, timezone
import logging
import json
import pytz

logger = logging.getLogger(__name__)
MSK_TZ = pytz.timezone('Europe/Moscow')


def now_msk_naive():
    return datetime.now(MSK_TZ).replace(tzinfo=None)

# В aiogram 2.x GetDiscussionMessage доступен через bot.get_discussion_message()
# Это основной способ получения связанного сообщения из группы обсуждения
# Удалено использование GetDiscussionMessage - используем только Telethon

# Кэш для комментариев (временный, до перезапуска)
comments_cache = {}

# Проверка наличия Telethon
try:
    from telethon import TelegramClient
    from telethon.errors import BotMethodInvalidError
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    logger.warning("⚠️ Telethon не установлен. Установите: pip install telethon")


# Удалена функция collect_all_comments_for_post - теперь используем только Telethon при нажатии кнопки

async def collect_all_comments_for_post_deprecated(bot: Bot, chat_id: str, message_id: int, discussion_group_link: str = None) -> int:
    """
    DEPRECATED: Функция больше не используется. Комментарии собираются через Telethon при нажатии кнопки "Подвести итоги".
    """
    logger.info(f"🚀 ПОЛУЧЕНИЕ КОММЕНТАРИЕВ: post_id={message_id}, chat_id={chat_id}, discussion_group_link={discussion_group_link}")
    try:
        # Получаем информацию о чате
        if chat_id.startswith('@'):
            chat = await bot.get_chat(chat_id)
            channel_id = chat.id
        else:
            chat = await bot.get_chat(int(chat_id))
            channel_id = int(chat_id)
        
        linked_chat_id = None
        discussion_message_id = None
        
        # Если указана ссылка на группу обсуждения, используем её напрямую
        if discussion_group_link:
            try:
                # Сначала пробуем парсить как ссылку с message_id
                parsed_group = parse_telegram_link(discussion_group_link)
                if parsed_group:
                    group_chat_id, _ = parsed_group
                    # Получаем информацию о чате группы
                    group_chat = await bot.get_chat(group_chat_id)
                    linked_chat_id = group_chat.id
                    logger.info(f"✅ Используем указанную группу обсуждения: {discussion_group_link} (ID: {linked_chat_id})")
                else:
                    # Если не получилось, пробуем парсить как ссылку на группу/канал без message_id
                    group_chat_id_str = parse_telegram_chat_link(discussion_group_link)
                    if group_chat_id_str:
                        # Получаем информацию о чате группы
                        group_chat = await bot.get_chat(group_chat_id_str)
                        linked_chat_id = group_chat.id
                        logger.info(f"✅ Используем указанную группу обсуждения: {discussion_group_link} (ID: {linked_chat_id})")
                    else:
                        logger.warning(f"⚠️ Не удалось распарсить ссылку на группу обсуждения: {discussion_group_link}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при получении группы обсуждения по ссылке {discussion_group_link}: {e}")
        
        # Удалено использование GetDiscussionMessage - используем только Telethon при нажатии кнопки
        # Если группа обсуждения все еще не найдена, пробуем через linked_chat
        if not linked_chat_id:
            try:
                if hasattr(chat, 'linked_chat') and chat.linked_chat:
                    linked_chat_id = chat.linked_chat.id
                    logger.info(f"✅ Найдена группа обсуждения через linked_chat: {linked_chat_id}")
            except Exception as e:
                logger.debug(f"Ошибка при проверке linked_chat: {e}")
        
        if not linked_chat_id:
            logger.warning(f"⚠️ Не удалось найти связанную группу обсуждения для {chat_id}")
            return 0
        
        # Получаем комментарии из БД
        async with async_session() as db_session:
            # Сначала ищем по comment_chat_id (ID группы обсуждения) и discussion_message_id
            if discussion_message_id:
                result = await db_session.execute(
                    select(Comment).where(
                        Comment.post_message_id == message_id,
                        Comment.comment_chat_id == str(linked_chat_id)
                    )
                )
                existing_comments = result.scalars().all()
            else:
                existing_comments = []
            
            # Если не нашли, пробуем по chat_id канала
            if not existing_comments:
                result2 = await db_session.execute(
                    select(Comment).where(
                        Comment.post_message_id == message_id,
                        Comment.chat_id == str(chat_id)
                    )
                )
                existing_comments = result2.scalars().all()
            
            logger.info(f"✅ Найдено {len(existing_comments)} комментариев в БД для поста {message_id}")
        
        # Если комментариев нет в БД, пытаемся собрать исторические через Telethon
        if not existing_comments and linked_chat_id and discussion_message_id:
            logger.info(f"📥 В БД нет комментариев. Пытаемся собрать все комментарии из группы обсуждения через Telethon...")
            collected_count = await fetch_all_comments_from_discussion_group(
                linked_chat_id, 
                message_id,
                discussion_message_id,
                chat_id
            )
            logger.info(f"✅ Telethon собрал {collected_count} комментариев")
        
        return len(existing_comments)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при получении комментариев: {e}", exc_info=True)
        return 0


async def fetch_all_comments_from_discussion_group(
    discussion_chat_id: int, 
    post_message_id: int, 
    discussion_message_id: int, 
    channel_chat_id: str
) -> int:
    """
    Собирает ВСЕ комментарии из группы обсуждения используя Telethon
    Используется для получения комментариев, которые были написаны когда бот был выключен
    
    discussion_chat_id - ID группы обсуждения
    post_message_id - ID поста в канале
    discussion_message_id - ID сообщения в группе обсуждения (получено через GetDiscussionMessage)
    channel_chat_id - ID канала
    """
    if not HAS_TELETHON:
        logger.warning(f"⚠️ Telethon не установлен. Установите: pip install telethon")
        return 0
    
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.warning(f"⚠️ TELEGRAM_API_ID и TELEGRAM_API_HASH не настроены в .env файле")
        logger.warning(f"⚠️ Получите их на https://my.telegram.org/apps")
        return 0
    
    saved_count = 0
    try:
        logger.info(f"🔍 Telethon: Начало сбора ВСЕХ комментариев из группы {discussion_chat_id} для поста {post_message_id}")
        
        # Создаем Telethon клиент
        session_file = 'giveaway_session.session'
        client = TelegramClient(session_file, int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        
        try:
            # Подключаемся (используем существующую сессию пользователя)
            if client.is_connected():
                await client.disconnect()
            
            try:
                await client.start()
            except Exception as auth_error:
                logger.warning(f"⚠️ Telethon: Не удалось аутентифицироваться: {auth_error}")
                logger.warning(f"⚠️ 📋 Запустите скрипт setup_telethon_session.py для создания пользовательской сессии")
                return 0
            
            # Проверяем, что это не бот
            try:
                me = await client.get_me()
                if me.bot:
                    logger.warning(f"⚠️ Telethon: Обнаружена сессия бота. Нужна сессия пользователя.")
                    logger.warning(f"⚠️ 📋 Запустите: python setup_telethon_session.py")
                    return 0
                logger.info(f"✅ Telethon: Клиент запущен как пользователь ({me.first_name})")
            except Exception as e:
                logger.warning(f"⚠️ Telethon: Ошибка проверки типа аутентификации: {e}")
                return 0
            
            # Получаем информацию о чате
            entity = None
            try:
                entity = await client.get_entity(discussion_chat_id)
                logger.info(f"✅ Telethon: Получена информация о чате {discussion_chat_id}")
            except Exception as e:
                logger.error(f"❌ Telethon: Не удалось получить чат {discussion_chat_id}: {e}")
                return 0
            
            if not entity:
                return 0
            
            # Получаем все сообщения из чата
            logger.info(f"📥 Telethon: Получение всех сообщений из группы {entity.id}...")
            logger.info(f"🔍 Telethon: Ищем комментарии для discussion_message_id={discussion_message_id}")
            
            all_messages = []
            offset_id = 0
            limit = 100
            max_messages = 50000  # Ограничение
            iterations_without_new = 0
            max_iterations_without_new = 10
            
            async with async_session() as db_session:
                while len(all_messages) < max_messages:
                    try:
                        messages = await client.get_messages(entity, limit=limit, offset_id=offset_id)
                        
                        if not messages:
                            break
                        
                        found_in_batch = 0
                        # Фильтруем сообщения, которые являются ответами на нужный пост
                        for msg in messages:
                            if msg.reply_to:
                                reply_to_top_id = None
                                reply_to_msg_id = None
                                
                                if hasattr(msg.reply_to, 'reply_to_top_id'):
                                    reply_to_top_id = msg.reply_to.reply_to_top_id
                                if hasattr(msg.reply_to, 'reply_to_msg_id'):
                                    reply_to_msg_id = msg.reply_to.reply_to_msg_id
                                
                                is_comment_to_post = False
                                
                                # Проверяем по reply_to_top_id (цепочка ответов)
                                if reply_to_top_id is not None:
                                    if reply_to_top_id == discussion_message_id:
                                        is_comment_to_post = True
                                # Проверяем по reply_to_msg_id (прямой ответ)
                                elif reply_to_msg_id is not None:
                                    if reply_to_msg_id == discussion_message_id or reply_to_msg_id == post_message_id:
                                        is_comment_to_post = True
                                
                                if is_comment_to_post:
                                    found_in_batch += 1
                                    
                                    # Проверяем, не сохранен ли уже
                                    existing = await db_session.execute(
                                        select(Comment).where(
                                            Comment.comment_message_id == msg.id,
                                            Comment.comment_chat_id == str(discussion_chat_id)
                                        )
                                    )
                                    if existing.scalar_one_or_none():
                                        continue
                                    
                                    # Получаем информацию о пользователе
                                    user_id = None
                                    username = None
                                    if msg.from_id:
                                        try:
                                            user_entity = await client.get_entity(msg.from_id)
                                            user_id = user_entity.id if hasattr(user_entity, 'id') else None
                                            username = user_entity.username if hasattr(user_entity, 'username') else None
                                        except:
                                            pass
                                    
                                    # Формируем ссылку на комментарий
                                    comment_link = get_message_link(str(discussion_chat_id), msg.id)
                                    
                                    # Сохраняем в БД
                                    comment = Comment(
                                        chat_id=channel_chat_id,
                                        post_message_id=post_message_id,
                                        comment_message_id=msg.id,
                                        comment_chat_id=str(discussion_chat_id),
                                        comment_link=comment_link,
                                        user_id=user_id,
                                        username=username,
                                        text=msg.text or msg.message or ""
                                    )
                                    db_session.add(comment)
                                    saved_count += 1
                                    all_messages.append(msg)
                                    
                                    if saved_count % 50 == 0:
                                        await db_session.commit()
                                        logger.info(f"💾 Telethon: Промежуточный коммит: сохранено {saved_count} комментариев...")
                        
                        # Обновляем offset_id
                        if messages:
                            new_offset_id = messages[-1].id
                            if new_offset_id == offset_id:
                                break
                            offset_id = new_offset_id
                            
                            if found_in_batch == 0:
                                iterations_without_new += 1
                                if iterations_without_new >= max_iterations_without_new:
                                    logger.info(f"📥 Telethon: {max_iterations_without_new} итераций без новых комментариев, прекращаем поиск")
                                    break
                            else:
                                iterations_without_new = 0
                        else:
                            break
                        
                        logger.info(f"🔍 Telethon: Обработано сообщений: {len(all_messages)}, найдено комментариев: {saved_count}")
                        
                    except Exception as e:
                        if BotMethodInvalidError and isinstance(e, BotMethodInvalidError):
                            logger.warning(f"⚠️ Telethon: Боты не могут получать историю сообщений.")
                            logger.warning(f"⚠️ Нужна аутентификация как пользователь. Запустите: python setup_telethon_session.py")
                            break
                        logger.error(f"❌ Telethon: Ошибка при получении сообщений: {e}", exc_info=True)
                        break
                
                # Финальный коммит
                if saved_count > 0:
                    await db_session.commit()
                    logger.info(f"💾 Telethon: Финальный коммит: сохранено {saved_count} комментариев")
            
            logger.info(f"✅ Telethon: Сбор завершен. Сохранено {saved_count} комментариев")
            return saved_count
            
        finally:
            await client.disconnect()
            logger.info(f"✅ Telethon: Клиент отключен")
            
    except Exception as e:
        logger.error(f"❌ Telethon: Ошибка при сборе комментариев: {e}", exc_info=True)
        return saved_count


async def get_comments_from_post(bot: Bot, chat_id: str, message_id: int) -> list[dict]:
    """
    Получает все комментарии/ответы под конкретным постом
    Возвращает список словарей с информацией о комментариях
    """
    return await get_comments_replies(bot, chat_id, message_id)


async def get_comments_replies(bot: Bot, chat_id: str, message_id: int, discussion_group_link: str = None) -> list[dict]:
    """
    Получает комментарии под постом из БД (если они есть)
    DEPRECATED: Комментарии больше не сохраняются автоматически. Используйте Telethon при нажатии кнопки "Подвести итоги".
    """
    comments = []
    try:
        logger.info(f"Получение комментариев для поста {message_id} в чате {chat_id}, discussion_group={discussion_group_link}")
        
        # Получаем информацию о чате
        if chat_id.startswith('@'):
            chat = await bot.get_chat(chat_id)
            channel_id = chat.id
        else:
            chat = await bot.get_chat(int(chat_id))
            channel_id = int(chat_id)
        
        linked_chat_id = None
        discussion_message_id = None
        
        # Если указана ссылка на группу обсуждения, используем её напрямую
        if discussion_group_link:
            try:
                # Сначала пробуем парсить как ссылку с message_id
                parsed_group = parse_telegram_link(discussion_group_link)
                if parsed_group:
                    group_chat_id, _ = parsed_group
                    # Получаем информацию о чате группы
                    group_chat = await bot.get_chat(group_chat_id)
                    linked_chat_id = group_chat.id
                    logger.info(f"✅ Используем указанную группу обсуждения: {discussion_group_link} (ID: {linked_chat_id})")
                else:
                    # Если не получилось, пробуем парсить как ссылку на группу/канал без message_id
                    group_chat_id_str = parse_telegram_chat_link(discussion_group_link)
                    if group_chat_id_str:
                        # Получаем информацию о чате группы
                        group_chat = await bot.get_chat(group_chat_id_str)
                        linked_chat_id = group_chat.id
                        logger.info(f"✅ Используем указанную группу обсуждения: {discussion_group_link} (ID: {linked_chat_id})")
                    else:
                        logger.warning(f"⚠️ Не удалось распарсить ссылку на группу обсуждения: {discussion_group_link}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при получении группы обсуждения по ссылке {discussion_group_link}: {e}")
        
        # Удалено использование GetDiscussionMessage - используем только Telethon при нажатии кнопки
        # Если группа обсуждения не указана, пробуем через linked_chat
        if not linked_chat_id:
                try:
                    if hasattr(chat, 'linked_chat') and chat.linked_chat:
                        linked_chat_id = chat.linked_chat.id
                        logger.info(f"✅ Найдена группа обсуждения через linked_chat: {linked_chat_id}")
                except Exception as e:
                    logger.debug(f"Ошибка при проверке linked_chat: {e}")
        
        # Получаем комментарии из БД
        if linked_chat_id:
            async with async_session() as session:
                # Ищем по comment_chat_id (ID группы обсуждения)
                result = await session.execute(
                    select(Comment).where(
                        Comment.post_message_id == message_id,
                        Comment.comment_chat_id == str(linked_chat_id)
                    )
                )
                db_comments = result.scalars().all()
                
                # Если не нашли, пробуем по chat_id канала
                if not db_comments:
                    result2 = await session.execute(
                        select(Comment).where(
                            Comment.post_message_id == message_id,
                            Comment.chat_id == str(chat_id)
                        )
                    )
                    db_comments = result2.scalars().all()
                
                # Преобразуем в формат словарей
                for comment in db_comments:
                    comments.append({
                        'link': comment.comment_link,
                        'user_id': comment.user_id,
                        'username': comment.username,
                        'text': comment.text or '',
                        'message_id': comment.comment_message_id,
                        'chat_id': comment.comment_chat_id
                    })
        
        logger.info(f"✅ Найдено {len(comments)} комментариев в БД для поста {message_id}")
        return comments
        
    except Exception as e:
        logger.error(f"❌ Ошибка при получении комментариев: {e}", exc_info=True)
        return []


# Удалена функция check_comment_via_telethon - больше не используется
# Удалена функция handle_message_with_reply - комментарии не сохраняются автоматически

async def start_giveaway(message: types.Message):
    """Команда для запуска розыгрыша (legacy, можно удалить)"""
    bot = message.bot
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Используй: /giveaway <chat_id> <message_id>")
        return
    
    try:
        chat_id = args[1]
        message_id = int(args[2])
        comments = await get_comments_replies(bot, chat_id, message_id)
        await message.answer(f"Найдено {len(comments)} комментариев")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


async def select_winners_from_contest(contest_id: int, winners_count: int, bot: Bot, skip_existing: bool = True, use_telethon: bool = True) -> list[dict]:
    """
    Выбирает победителей из конкурса на основе комментариев под постом
    
    Если use_telethon=True, использует Telethon для сбора комментариев и сохраняет их в файл.
    Затем выбирает победителей из файла через randomizer.
    
    Комментарии получаются из БД (сохраненные автоматически) и кэша (новые).
    Если skip_existing=True, удаляет существующих временных победителей перед выбором новых.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Giveaway).where(Giveaway.id == contest_id)
        )
        giveaway = result.scalars().first()
        
        if not giveaway:
            raise ValueError(f"Конкурс с ID {contest_id} не найден")
        
        if giveaway.is_confirmed:
            raise ValueError("Победители уже подтверждены. Редактирование невозможно.")
        
        # Определяем тип конкурса
        contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
        
        # Для конкурсов рисунков post_link не требуется
        if contest_type == 'drawing':
            # Для конкурсов рисунков выбираем победителей из участников с фотографиями
            from models import Participant
            import random
            
            # Получаем всех участников с загруженными фотографиями
            participants_result = await session.execute(
                select(Participant).where(
                    Participant.giveaway_id == contest_id,
                    Participant.photo_link.isnot(None),
                    Participant.photo_link != ''
                )
            )
            participants = participants_result.scalars().all()
            
            if not participants:
                raise ValueError("⚠️ Не найдено участников с загруженными фотографиями")
            
            if len(participants) < winners_count:
                raise ValueError(f"⚠️ Недостаточно участников с фотографиями. Найдено: {len(participants)}, требуется: {winners_count}")
            
            # Выбираем случайных победителей
            selected_participants = random.sample(list(participants), winners_count)
            
            # Удаляем существующих временных победителей, если нужно
            if skip_existing:
                existing_winners = await session.execute(
                    select(Winner).where(Winner.giveaway_id == contest_id)
                )
                existing_winners_list = existing_winners.scalars().all()
                if existing_winners_list:
                    logger.info(f"🗑️ Удаляем {len(existing_winners_list)} существующих победителей для конкурса {contest_id}")
                    for winner in existing_winners_list:
                        await session.delete(winner)
                await session.commit()
            
            # Получаем призы из конкурса
            prize_links = giveaway.prize_links if hasattr(giveaway, 'prize_links') and giveaway.prize_links else []
            if not isinstance(prize_links, list):
                prize_links = []
            
            # Сохраняем победителей в БД
            winners_list = []
            for index, participant in enumerate(selected_participants):
                prize_link = prize_links[index] if index < len(prize_links) else None
                
                winner = Winner(
                    giveaway_id=contest_id,
                    user_id=participant.user_id,
                    user_username=participant.user_username,
                    photo_link=participant.photo_link,
                    photo_message_id=participant.photo_message_id,
                    comment_link=None,  # Для конкурсов рисунков comment_link = None
                    prize_link=prize_link,
                    place=index + 1,
                    created_at=now_msk_naive()
                )
                session.add(winner)
                winners_list.append({
                    "id": winner.id,
                    "user_id": participant.user_id,
                    "user_username": participant.user_username,
                    "photo_link": participant.photo_link,
                    "photo_message_id": participant.photo_message_id,
                    "comment_link": None,
                    "prize_link": prize_link,
                    "place": index + 1
                })
            
            await session.commit()
            logger.info(f"✅ Выбрано {len(winners_list)} победителей для конкурса рисунков {contest_id}")
            return winners_list
        
        # Для рандом комментариев требуется post_link
        if not giveaway.post_link:
            raise ValueError("У конкурса не указана ссылка на пост")
        
        parsed = parse_telegram_link(giveaway.post_link)
        if not parsed:
            raise ValueError(f"Не удалось распарсить ссылку: {giveaway.post_link}")
        
        chat_id, message_id = parsed
        
        # Извлекаем username канала из chat_id (убираем @ если есть)
        # Если chat_id - это числовой ID (не username), используем его напрямую
        if chat_id.startswith('@'):
            channel_username = chat_id.replace('@', '')
        elif chat_id.isdigit() or (chat_id.startswith('-') and chat_id[1:].isdigit()):
            # Это числовой ID, нужно будет использовать его напрямую в Telethon
            channel_username = chat_id
        else:
            # Пробуем использовать как username
            channel_username = chat_id
        
        logger.info(f"Получение комментариев для конкурса {contest_id}: чат={chat_id}, сообщение={message_id}")
        
        # Удаляем существующих временных победителей, если нужно
        if skip_existing:
            existing_winners = await session.execute(
                select(Winner).where(Winner.giveaway_id == contest_id)
            )
            existing_winners_list = existing_winners.scalars().all()
            if existing_winners_list:
                logger.info(f"🗑️ Удаляем {len(existing_winners_list)} существующих победителей для конкурса {contest_id}")
                for winner in existing_winners_list:
                    logger.info(f"  - Удаляем победителя ID {winner.id} с ссылкой {winner.comment_link}")
                    await session.delete(winner)
            await session.commit()
        
        # Используем Telethon для сбора комментариев и сохранения в файл
        if use_telethon and HAS_TELETHON and TELEGRAM_API_ID and TELEGRAM_API_HASH:
            try:
                logger.info(f"🔄 Используем Telethon для сбора комментариев конкурса {contest_id}")
                
                # Извлекаем username группы обсуждения из discussion_group_link если есть
                discussion_group_username_param = None
                if giveaway.discussion_group_link:
                    # Парсим ссылку на группу обсуждения
                    parsed_group = parse_telegram_chat_link(giveaway.discussion_group_link)
                    if parsed_group:
                        # Убираем @ если есть
                        discussion_group_username_param = parsed_group.replace('@', '') if parsed_group.startswith('@') else parsed_group
                
                # Собираем комментарии через Telethon и сохраняем в файл
                # Передаем дату окончания конкурса для фильтрации по времени
                end_date = giveaway.end_date if hasattr(giveaway, 'end_date') else None
                result_data = await collect_comments_via_telethon(
                    channel_username=channel_username,
                    post_message_id=message_id,
                    contest_id=contest_id,
                    api_id=int(TELEGRAM_API_ID),
                    api_hash=TELEGRAM_API_HASH,
                    session_file='giveaway_session.session',
                    discussion_group_username=discussion_group_username_param,
                    end_date=end_date
                )
                
                comments_count = result_data['count']
                file_path = result_data['file_path']
                
                logger.info(f"✅ Telethon собрал {comments_count} комментариев, сохранено в {file_path}")
                
                if comments_count == 0:
                    raise ValueError(f"⚠️ Не найдено комментариев для поста {giveaway.post_link}")
                
                # Используем winners_count из конкурса
                actual_winners_count = giveaway.winners_count if hasattr(giveaway, 'winners_count') else winners_count
                
                # Получаем призы из конкурса
                prize_links = giveaway.prize_links if hasattr(giveaway, 'prize_links') and giveaway.prize_links else []
                if not isinstance(prize_links, list):
                    prize_links = []
                
                # Выбираем победителей из файла
                winners = pick_random_winners_from_file(file_path, actual_winners_count)
                
                # Сохраняем победителей в БД (пока без подтверждения, только временно)
                winners_list = []
                for index, winner_data in enumerate(winners):
                    comment_link = winner_data.get('comment_link', '')
                    if comment_link:
                        # Определяем место (1, 2, 3 и т.д.)
                        place = index + 1
                        
                        # Получаем приз для этого места (если есть)
                        prize_link = prize_links[index] if index < len(prize_links) else None
                        
                        # Создаем победителя (пока без подтверждения, user_id и prize_link будут добавлены при подтверждении)
                        # Определяем тип конкурса
                        contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
                        
                        # Для рандом комментариев используем comment_link, для конкурса рисунков - photo_link
                        if contest_type == 'random_comment':
                            winner = Winner(
                                giveaway_id=contest_id,
                                comment_link=comment_link,
                                photo_link=None,  # NULL для рандом комментариев
                                photo_message_id=None,
                                user_id=winner_data.get('user_id'),
                                user_username=winner_data.get('user_username'),
                                prize_link=prize_link,
                                place=place
                            )
                        else:
                            # Для конкурса рисунков используем photo_link из участника
                            # Пока что используем comment_link как заглушку, позже нужно будет получать photo_link из participants
                            winner = Winner(
                                giveaway_id=contest_id,
                                comment_link=None,  # NULL для конкурса рисунков
                                photo_link=winner_data.get('photo_link'),  # Будет получено из participants
                                photo_message_id=winner_data.get('photo_message_id'),
                                user_id=winner_data.get('user_id'),
                                user_username=winner_data.get('user_username'),
                                prize_link=prize_link,
                                place=place
                            )
                        session.add(winner)
                        if contest_type == 'random_comment':
                            logger.info(f"✅ Сохранен победитель #{place} для конкурса {contest_id}: {comment_link} (user_id: {winner_data.get('user_id')}, prize: {prize_link})")
                            winners_list.append({
                                "comment_link": comment_link,
                                "message_id": winner_data.get('message_id'),
                                "user_id": winner_data.get('user_id'),
                                "user_first_name": winner_data.get('user_first_name'),
                                "user_username": winner_data.get('user_username'),
                                "text": winner_data.get('text', '')[:100],  # Первые 100 символов
                                "place": place,
                                "prize_link": prize_link
                            })
                        else:
                            logger.info(f"✅ Сохранен победитель #{place} для конкурса {contest_id}: photo_link={winner.photo_link} (user_id: {winner_data.get('user_id')}, prize: {prize_link})")
                            winners_list.append({
                                "photo_link": winner.photo_link,
                                "photo_message_id": winner.photo_message_id,
                                "user_id": winner_data.get('user_id'),
                                "user_first_name": winner_data.get('user_first_name'),
                                "user_username": winner_data.get('user_username'),
                                "place": place,
                                "prize_link": prize_link
                            })
                
                # Обновляем время выбора победителей (naive UTC)
                giveaway.winners_selected_at = now_msk_naive()
                
                await session.commit()
                await log_action(session, None, f"Выбраны победители для конкурса {contest_id} через Telethon")
                
                logger.info(f"✅ Выбрано {len(winners_list)} победителей из {comments_count} комментариев (Telethon)")
                
                return winners_list
                
            except Exception as telethon_error:
                logger.error(f"❌ Ошибка при использовании Telethon: {telethon_error}", exc_info=True)
                logger.info(f"🔄 Пробуем использовать метод через БД...")
                # Продолжаем с обычным методом
        
        # Fallback: используем старый метод через БД
        discussion_group_link = giveaway.discussion_group_link if hasattr(giveaway, 'discussion_group_link') else None
        
        logger.info(f"Получение комментариев для конкурса {contest_id}: чат={chat_id}, сообщение={message_id}, discussion_group={discussion_group_link}")
        
        # Получаем комментарии под постом (передаем ссылку на группу обсуждения, если указана)
        comments = await get_comments_replies(bot, chat_id, message_id, discussion_group_link)
        
        if not comments:
            error_msg = (
                f"⚠️ Не найдено комментариев для поста {giveaway.post_link}\n\n"
                f"📋 ВОЗМОЖНЫЕ ПРИЧИНЫ:\n\n"
                f"1. Комментарии еще не были оставлены в группе обсуждения\n"
                f"2. Бот еще не обработал комментарии (они сохраняются автоматически)\n"
                f"3. Связанная группа обсуждения не настроена\n\n"
                f"💡 РЕШЕНИЕ:\n"
                f"- Убедитесь, что группа @monkeys_gifts связана с каналом\n"
                f"- Попросите участников оставить комментарии в группе обсуждения\n"
                f"- Комментарии будут сохраняться автоматически при их появлении\n"
                f"- Попробуйте выбрать победителей через несколько минут после появления комментариев"
            )
            logger.warning(error_msg)
            raise ValueError(error_msg)
        
        # Извлекаем ссылки на комментарии
        comment_links = [c.get('link', '') for c in comments if c.get('link')]
        
        if not comment_links:
            raise ValueError("Не найдено ни одного комментария под постом")
        
        logger.info(f"Найдено {len(comment_links)} комментариев для выбора победителей")
        
        # Используем winners_count из конкурса
        actual_winners_count = giveaway.winners_count if hasattr(giveaway, 'winners_count') else winners_count
        
        # Выбираем победителей
        from randomizer import pick_random_winners
        winner_links = pick_random_winners(comment_links, actual_winners_count)
        
        # Сохраняем победителей
        winners_list = []
        for link in winner_links:
            winner = Winner(giveaway_id=contest_id, comment_link=link)
            session.add(winner)
            logger.info(f"✅ Сохранен победитель для конкурса {contest_id}: {link}")
            winners_list.append({"comment_link": link})
        
        # Обновляем время выбора победителей (naive UTC)
        giveaway.winners_selected_at = now_msk_naive()
        
        await session.commit()
        await log_action(session, None, f"Выбраны победители для конкурса {contest_id}")
        
        logger.info(f"Выбрано {len(winner_links)} победителей из {len(comment_links)} комментариев")
        
        return winners_list


async def reroll_single_winner(contest_id: int, old_winner_link: str, bot: Bot) -> dict:
    """
    Рерандомизирует одного конкретного победителя
    """
    async with async_session() as session:
        giveaway_result = await session.execute(
            select(Giveaway).where(Giveaway.id == contest_id)
        )
        giveaway = giveaway_result.scalars().first()
        
        if not giveaway:
            raise ValueError(f"Конкурс с ID {contest_id} не найден")
        
        if giveaway.is_confirmed:
            raise ValueError("Победители уже подтверждены. Редактирование невозможно.")
        
        # Определяем тип конкурса
        contest_type = getattr(giveaway, 'contest_type', 'random_comment') if hasattr(giveaway, 'contest_type') else 'random_comment'
        
        # Для конкурсов рисунков используем участников, для рандом комментариев - файл комментариев
        if contest_type == 'drawing':
            # Для конкурсов рисунков реролл из участников с фотографиями
            from models import Participant
            import random
            
            # Получаем всех участников с загруженными фотографиями
            participants_result = await session.execute(
                select(Participant).where(
                    Participant.giveaway_id == contest_id,
                    Participant.photo_link.isnot(None),
                    Participant.photo_link != ''
                )
            )
            participants = participants_result.scalars().all()
            
            if not participants:
                raise ValueError("Не найдено участников с загруженными фотографиями")
            
            # Получаем текущих победителей
            existing_winners_result = await session.execute(
                select(Winner).where(Winner.giveaway_id == contest_id)
            )
            existing_winners = existing_winners_result.scalars().all()
            existing_photo_links = [w.photo_link for w in existing_winners if w.photo_link]
            
            # Удаляем старого победителя и получаем его место и приз
            old_winner_result = await session.execute(
                select(Winner).where(
                    Winner.giveaway_id == contest_id,
                    Winner.photo_link == old_winner_link
                )
            )
            old_winner = old_winner_result.scalar_one_or_none()
            old_place = None
            old_prize_link = None
            old_reroll_count = 0
            if old_winner:
                old_place = old_winner.place if hasattr(old_winner, 'place') else None
                old_prize_link = old_winner.prize_link if hasattr(old_winner, 'prize_link') else None
                old_reroll_count = getattr(old_winner, 'reroll_count', 0) or 0
                await session.delete(old_winner)
                logger.info(f"🗑️ Удален старый победитель для конкурса {contest_id}: {old_winner_link}")

            # Исключаем текущих победителей из выборки
            available_participants = [p for p in participants if p.photo_link != old_winner_link and p.photo_link not in existing_photo_links]
            
            if not available_participants:
                raise ValueError("Нет доступных участников для рерандома")
            
            # Выбираем случайного нового победителя
            new_participant = random.choice(available_participants)
            
            new_winner_data = {
                'photo_link': new_participant.photo_link,
                'photo_message_id': new_participant.photo_message_id,
                'user_id': new_participant.user_id,
                'user_username': new_participant.user_username
            }
            new_winner_links = [new_participant.photo_link]
        else:
            # Для рандом комментариев используем файл комментариев
            if not giveaway.post_link:
                raise ValueError("У конкурса не указана ссылка на пост")
            
            # Используем файл комментариев для рерандомизации
            from telethon_comments import read_comments_from_file, get_comments_file_path
            from randomizer import pick_random_winners
            
            file_path = get_comments_file_path(contest_id)
            comments_data = read_comments_from_file(file_path)
            
            if not comments_data:
                raise ValueError(f"Не найдено комментариев в файле для конкурса {contest_id}. Сначала нажмите 'Подвести итоги' для сбора комментариев.")
            
            # Извлекаем ссылки на комментарии из файла
            comment_links = [c.get('comment_link', '') for c in comments_data if c.get('comment_link')]
            
            if not comment_links:
                raise ValueError("Не найдено ни одного комментария в файле")
            
            # Получаем текущих победителей
            existing_winners_result = await session.execute(
                select(Winner).where(Winner.giveaway_id == contest_id)
            )
            existing_winners = existing_winners_result.scalars().all()
            existing_links = [w.comment_link for w in existing_winners if w.comment_link]
            
            # Удаляем старого победителя и получаем его место и приз
            old_winner_result = await session.execute(
                select(Winner).where(
                    Winner.giveaway_id == contest_id,
                    Winner.comment_link == old_winner_link
                )
            )
            old_winner = old_winner_result.scalar_one_or_none()
            old_place = None
            old_prize_link = None
            old_reroll_count = 0
            if old_winner:
                old_place = old_winner.place if hasattr(old_winner, 'place') else None
                old_prize_link = old_winner.prize_link if hasattr(old_winner, 'prize_link') else None
                old_reroll_count = getattr(old_winner, 'reroll_count', 0) or 0
                await session.delete(old_winner)
                logger.info(f"🗑️ Удален старый победитель для конкурса {contest_id}: {old_winner_link}")

            # Убираем старый победитель и исключаем его из выборки
            available_links = [link for link in comment_links if link != old_winner_link and link not in existing_links]
            
            if not available_links:
                raise ValueError("Нет доступных комментариев для рерандома")
            
            # Выбираем нового победителя
            new_winner_links = pick_random_winners(available_links, 1)
            
            if not new_winner_links:
                raise ValueError("Не удалось выбрать нового победителя")
            
            # Находим данные о новом победителе из файла комментариев
            new_winner_data = None
            for comment in comments_data:
                if comment.get('comment_link') == new_winner_links[0]:
                    new_winner_data = comment
                    break
        
        # Добавляем нового победителя с сохранением места и приза
        if contest_type == 'random_comment':
            new_winner = Winner(
                giveaway_id=contest_id,
                comment_link=new_winner_links[0],
                photo_link=None,  # NULL для рандом комментариев
                photo_message_id=None,
                user_id=new_winner_data.get('user_id') if new_winner_data else None,
                user_username=new_winner_data.get('user_username') if new_winner_data else None,
                prize_link=old_prize_link,  # Сохраняем приз от старого победителя
                place=old_place,  # Сохраняем место от старого победителя
                reroll_count=old_reroll_count + 1  # Увеличиваем счетчик реролов
            )
        else:
            # Для конкурса рисунков используем photo_link
            new_winner = Winner(
                giveaway_id=contest_id,
                comment_link=None,  # NULL для конкурса рисунков
                photo_link=new_winner_data.get('photo_link') if new_winner_data else None,
                photo_message_id=new_winner_data.get('photo_message_id') if new_winner_data else None,
                user_id=new_winner_data.get('user_id') if new_winner_data else None,
                user_username=new_winner_data.get('user_username') if new_winner_data else None,
                prize_link=old_prize_link,  # Сохраняем приз от старого победителя
                place=old_place,  # Сохраняем место от старого победителя
                reroll_count=old_reroll_count + 1  # Увеличиваем счетчик реролов
            )
        session.add(new_winner)
        await session.commit()
        
        if contest_type == 'random_comment':
            logger.info(f"✅ Сохранен новый победитель для конкурса {contest_id}: {new_winner_links[0]} (место: {old_place}, приз: {old_prize_link})")
            return {
                "comment_link": new_winner_links[0],
                "photo_link": None,
                "user_id": new_winner_data.get('user_id') if new_winner_data else None,
                "user_username": new_winner_data.get('user_username') if new_winner_data else None,
                "place": old_place,
                "prize_link": old_prize_link
            }
        else:
            logger.info(f"✅ Сохранен новый победитель для конкурса {contest_id}: photo_link={new_winner.photo_link} (место: {old_place}, приз: {old_prize_link})")
            return {
                "comment_link": None,
                "photo_link": new_winner.photo_link,
                "photo_message_id": new_winner.photo_message_id,
                "user_id": new_winner_data.get('user_id') if new_winner_data else None,
                "user_username": new_winner_data.get('user_username') if new_winner_data else None,
                "place": old_place,
                "prize_link": old_prize_link
            }


async def award_experience_for_contest(contest_id: int, session) -> None:
    """
    Начисляет опыт пользователям за участие и победы в конкурсе
    
    Система начисления опыта:
    - Рандом соо (random_comment):
      * Победы: 1 место - 100, 2 место - 80, 3 место - 60, 4+ место - 40
      * Участие: 10 опыта (для всех участников, которые не победили)
    - Рисунки/Коллекции (drawing/collection):
      * Победы: 1 место - 50, 2 место - 40, 3 место - 30, 4+ место - 20
      * Участие: 5 опыта (для всех участников, которые не победили)
    """
    from models import User, Winner, Participant
    
    # Получаем конкурс
    result = await session.execute(
        select(Giveaway).where(Giveaway.id == contest_id)
    )
    giveaway = result.scalars().first()
    
    if not giveaway:
        logger.warning(f"Конкурс {contest_id} не найден для начисления опыта")
        return
    
    contest_type = getattr(giveaway, 'contest_type', 'random_comment')
    
    # Получаем всех победителей
    winners_result = await session.execute(
        select(Winner).where(Winner.giveaway_id == contest_id).order_by(Winner.place)
    )
    winners = winners_result.scalars().all()
    
    # Словарь для хранения опыта по местам
    if contest_type == 'random_comment':
        # Больше опыта за победы в рандом соо
        experience_by_place = {
            1: 100,
            2: 80,
            3: 60
        }
        participation_experience = 10
    else:
        # Меньше опыта за победы в рисунках/коллекциях
        experience_by_place = {
            1: 50,
            2: 40,
            3: 30
        }
        participation_experience = 5
    
    # Начисляем опыт победителям
    winner_user_ids = set()
    for winner in winners:
        if not winner.place:
            continue
            
        # Определяем user_id победителя
        user_id = None
        
        if contest_type == 'random_comment':
            # Для рандом соо проверяем user_id из Winner или извлекаем из comment_link через Comment
            if winner.user_id:
                user_id = winner.user_id
            elif winner.comment_link:
                # Пытаемся найти user_id в таблице Comment
                from models import Comment as CommentModel
                comment_result = await session.execute(
                    select(CommentModel).where(CommentModel.comment_link == winner.comment_link)
                )
                comment = comment_result.scalars().first()
                if comment and comment.user_id:
                    user_id = comment.user_id
                    # Обновляем Winner с найденным user_id
                    winner.user_id = user_id
        else:
            # Для рисунков/коллекций используем user_id из Winner
            user_id = winner.user_id
        
        if not user_id:
            logger.warning(f"Не найден user_id для победителя {winner.id} конкурса {contest_id}")
            continue
        
        # Проверяем, есть ли пользователь в боте
        user_result = await session.execute(
            select(User).where(User.telegram_id == user_id)
        )
        user = user_result.scalars().first()
        
        if not user:
            logger.info(f"Пользователь {user_id} не найден в боте, пропускаем начисление опыта")
            continue
        
        winner_user_ids.add(user_id)
        
        # Определяем опыт за место
        place = winner.place
        if place in experience_by_place:
            experience = experience_by_place[place]
        else:
            # Для мест 4+ используем меньшее значение
            experience = 40 if contest_type == 'random_comment' else 20
        
        # Начисляем опыт
        if user.experience is None:
            user.experience = 0
        user.experience += experience
        logger.info(f"✅ Начислено {experience} опыта пользователю {user_id} (место {place}) в конкурсе {contest_id}")
    
    # Начисляем опыт за участие (для всех участников, которые не победили)
    if contest_type == 'random_comment':
        # Для рандом соо начисляем опыт всем, кто оставил комментарий (из таблицы Comment)
        # Получаем post_link для поиска комментариев
        if giveaway.post_link:
            parsed = parse_telegram_link(giveaway.post_link)
            if parsed:
                channel_id, post_message_id = parsed
                # Нормализуем channel_id (может быть @username или числовой ID)
                channel_id_str = str(channel_id)
                if channel_id_str.startswith('@'):
                    channel_id_str = channel_id_str[1:]  # Убираем @
                
                # Ищем все комментарии к этому посту
                # Пробуем разные варианты channel_id
                from models import Comment as CommentModel
                comments_result = await session.execute(
                    select(CommentModel).where(
                        and_(
                            or_(
                                CommentModel.chat_id == channel_id_str,
                                CommentModel.chat_id == str(channel_id),
                                CommentModel.chat_id == f"@{channel_id_str}"
                            ),
                            CommentModel.post_message_id == post_message_id
                        )
                    )
                )
                comments = comments_result.scalars().all()
                
                for comment in comments:
                    if not comment.user_id:
                        continue
                    
                    # Пропускаем победителей (они уже получили опыт)
                    if comment.user_id in winner_user_ids:
                        continue
                    
                    # Проверяем, есть ли пользователь в боте
                    user_result = await session.execute(
                        select(User).where(User.telegram_id == comment.user_id)
                    )
                    user = user_result.scalars().first()
                    
                    if not user:
                        logger.info(f"Пользователь {comment.user_id} не найден в боте, пропускаем начисление опыта за участие")
                        continue
                    
                    # Начисляем опыт за участие
                    if user.experience is None:
                        user.experience = 0
                    user.experience += participation_experience
                    logger.info(f"✅ Начислено {participation_experience} опыта за участие пользователю {comment.user_id} в конкурсе {contest_id}")
    
    elif contest_type in ['drawing', 'collection']:
        # Для конкурсов рисунков/коллекций начисляем опыт всем участникам
        participants_result = await session.execute(
            select(Participant).where(Participant.giveaway_id == contest_id)
        )
        participants = participants_result.scalars().all()
        
        for participant in participants:
            if participant.user_id in winner_user_ids:
                # Победители уже получили опыт, пропускаем
                continue
            
            # Проверяем, есть ли пользователь в боте
            user_result = await session.execute(
                select(User).where(User.telegram_id == participant.user_id)
            )
            user = user_result.scalars().first()
            
            if not user:
                continue
            
            # Начисляем опыт за участие
            if user.experience is None:
                user.experience = 0
            user.experience += participation_experience
            logger.info(f"✅ Начислено {participation_experience} опыта за участие пользователю {participant.user_id} в конкурсе {contest_id}")
    
    await session.commit()
    logger.info(f"✅ Опыт начислен для конкурса {contest_id}")


async def confirm_winners(contest_id: int) -> bool:
    """
    Подтверждает победителей конкурса (финализирует выбор) и удаляет файл с комментариями
    """
    async with async_session() as session:
        result = await session.execute(
            select(Giveaway).where(Giveaway.id == contest_id)
        )
        giveaway = result.scalars().first()
        
        if not giveaway:
            raise ValueError(f"Конкурс с ID {contest_id} не найден")
        
        if giveaway.is_confirmed:
            return True  # Уже подтвержден
        
        # Начисляем опыт перед подтверждением
        try:
            await award_experience_for_contest(contest_id, session)
        except Exception as e:
            logger.error(f"Ошибка при начислении опыта для конкурса {contest_id}: {e}", exc_info=True)
            # Продолжаем подтверждение даже если начисление опыта не удалось
        
        giveaway.is_confirmed = True
        await session.commit()
        await log_action(session, None, f"Подтверждены победители для конкурса {contest_id}")
        
        # Удаляем файл с комментариями после подтверждения победителей
        try:
            from telethon_comments import get_comments_file_path
            import os
            file_path = get_comments_file_path(contest_id)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"🗑️ Удален файл с комментариями для конкурса {contest_id}: {file_path}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось удалить файл с комментариями для конкурса {contest_id}: {e}")
        
        return True


async def send_congratulations_messages(contest_id: int, bot: Bot) -> None:
    """
    Отправляет поздравительные сообщения победителям конкурса в группу обсуждения
    """
    try:
        async with async_session() as session:
            # Получаем информацию о конкурсе
            giveaway_result = await session.execute(
                select(Giveaway).where(Giveaway.id == contest_id)
            )
            giveaway = giveaway_result.scalars().first()

            if not giveaway:
                logger.error(f"Конкурс {contest_id} не найден для отправки поздравлений")
                return

            # Получаем всех победителей конкурса
            winners_result = await session.execute(
                select(Winner).where(Winner.giveaway_id == contest_id).order_by(Winner.place)
            )
            winners = winners_result.scalars().all()

            if not winners:
                logger.warning(f"Нет победителей для конкурса {contest_id}")
                return

            # Определяем тип конкурса
            contest_type = getattr(giveaway, 'contest_type', 'random_comment')

            # Получаем информацию о группе обсуждения
            discussion_group_link = giveaway.discussion_group_link
            if not discussion_group_link:
                logger.warning(f"У конкурса {contest_id} не указана группа обсуждения")
                return

            logger.info(f"Конкурс {contest_id}: discussion_group_link = {discussion_group_link}")

            # Парсим ссылку на группу обсуждения
            from post_parser import parse_telegram_chat_link
            group_chat_id = parse_telegram_chat_link(discussion_group_link)
            if not group_chat_id:
                logger.error(f"Не удалось распарсить ссылку на группу обсуждения: {discussion_group_link}")
                return

            logger.info(f"Группа обсуждения: {discussion_group_link} -> {group_chat_id}")

            # Получаем post_link для определения reply_to_message_id
            post_link = giveaway.post_link
            if contest_type == 'random_comment' and not post_link:
                logger.error(f"У конкурса рандом комментариев {contest_id} не указан post_link")
                return

            reply_to_message_id = None
            if contest_type == 'random_comment':
                # Для рандом комментариев пытаемся найти message_id группы обсуждения
                # Сначала парсим post_link чтобы получить message_id поста в канале
                from post_parser import parse_telegram_link
                parsed = parse_telegram_link(post_link)
                if parsed:
                    channel_chat_id, post_message_id = parsed
                    logger.info(f"Парсинг post_link: channel={channel_chat_id}, message_id={post_message_id}")

                    # Пытаемся найти соответствующее сообщение в группе обсуждения
                    try:
                        # Используем Telethon для получения discussion message
                        if HAS_TELETHON and TELEGRAM_API_ID and TELEGRAM_API_HASH:
                            logger.info("Используем Telethon для получения discussion message")
                            from telethon import TelegramClient
                            from telethon.errors import BotMethodInvalidError

                            session_file = 'giveaway_session.session'
                            client = TelegramClient(session_file, int(TELEGRAM_API_ID), TELEGRAM_API_HASH)

                            try:
                                await client.start()
                                # Получаем discussion message для поста в канале
                                discussion_message = await client.get_discussion_message(channel_chat_id, post_message_id)
                                if discussion_message:
                                    reply_to_message_id = discussion_message.id
                                    logger.info(f"Найден discussion message: {reply_to_message_id}")
                                else:
                                    logger.warning(f"Не найден discussion message для поста {post_message_id}")
                            except Exception as e:
                                logger.warning(f"Ошибка при получении discussion message: {e}")
                            finally:
                                await client.disconnect()
                        else:
                            logger.warning("Telethon не настроен, невозможно получить discussion message")
                    except Exception as e:
                        logger.warning(f"Ошибка при работе с Telethon: {e}")

            # Проверяем, что бот имеет доступ к группе обсуждения
            try:
                chat_info = await bot.get_chat(group_chat_id)
                logger.info(f"Бот имеет доступ к группе: {chat_info.title} (ID: {chat_info.id})")
            except Exception as e:
                logger.error(f"Бот не имеет доступа к группе {group_chat_id}: {e}")
                return

            # Отправляем поздравления для каждого победителя
            for winner in winners:
                try:
                    if contest_type == 'random_comment':
                        # Для рандом комментариев
                        if not winner.comment_link:
                            logger.warning(f"У победителя {winner.id} нет comment_link")
                            continue

                        # Получаем username победителя
                        username = winner.user_username or "пользователь"
                        if username.startswith('@'):
                            username_display = username
                        else:
                            username_display = f"@{username}"

                        # Формируем поздравительное сообщение
                        congratulation_text = f"🎉 Поздравляем победителя!\n\n"
                        congratulation_text += f"🏆 {winner.comment_link}\n"
                        congratulation_text += f"👤 {username_display}"

                        # Добавляем информацию о призе, если есть
                        if winner.prize_link:
                            congratulation_text += f"\n🎁 Приз: {winner.prize_link}"

                        # Добавляем место победителя
                        if winner.place:
                            place_text = ""
                            if winner.place == 1:
                                place_text = "🥇 1 место"
                            elif winner.place == 2:
                                place_text = "🥈 2 место"
                            elif winner.place == 3:
                                place_text = "🥉 3 место"
                            else:
                                place_text = f"🏅 {winner.place} место"

                            congratulation_text += f"\n{place_text}"

                        # Добавляем информацию о реролах, если они были
                        reroll_count = getattr(winner, 'reroll_count', 0) or 0
                        if reroll_count > 0:
                            congratulation_text += f"\n🔄 Реролов: {reroll_count}"

                        # Отправляем сообщение в группу обсуждения в ответ на пост
                        try:
                            if reply_to_message_id:
                                await bot.send_message(
                                    chat_id=group_chat_id,
                                    text=congratulation_text,
                                    reply_to_message_id=reply_to_message_id
                                )
                                logger.info(f"✅ Отправлено поздравление победителю конкурса {contest_id}: {username_display} (reply_to: {reply_to_message_id})")
                            else:
                                await bot.send_message(
                                    chat_id=group_chat_id,
                                    text=congratulation_text
                                )
                                logger.info(f"✅ Отправлено поздравление победителю конкурса {contest_id}: {username_display} (без reply_to)")
                        except Exception as send_error:
                            logger.error(f"❌ Ошибка при отправке поздравления победителю {winner.id}: {send_error}")
                            # Пробуем отправить без reply_to_message_id
                            try:
                                await bot.send_message(
                                    chat_id=group_chat_id,
                                    text=f"{congratulation_text}\n\n❌ Не удалось ответить на пост"
                                )
                                logger.info(f"✅ Отправлено поздравление без reply_to для победителя конкурса {contest_id}: {username_display}")
                            except Exception as fallback_error:
                                logger.error(f"❌ Ошибка при отправке fallback поздравления победителю {winner.id}: {fallback_error}")

                    else:
                        # Для конкурсов рисунков
                        if not winner.photo_link:
                            logger.warning(f"У победителя {winner.id} нет photo_link")
                            continue

                        username = winner.user_username or "пользователь"
                        if username.startswith('@'):
                            username_display = username
                        else:
                            username_display = f"@{username}"

                        congratulation_text = f"🎉 Поздравляем победителя конкурса рисунков!\n\n"
                        congratulation_text += f"🏆 {winner.photo_link}\n"
                        congratulation_text += f"👤 {username_display}"

                        if winner.prize_link:
                            congratulation_text += f"\n🎁 Приз: {winner.prize_link}"

                        if winner.place:
                            place_text = ""
                            if winner.place == 1:
                                place_text = "🥇 1 место"
                            elif winner.place == 2:
                                place_text = "🥈 2 место"
                            elif winner.place == 3:
                                place_text = "🥉 3 место"
                            else:
                                place_text = f"🏅 {winner.place} место"

                            congratulation_text += f"\n{place_text}"

                        # Добавляем информацию о реролах, если они были
                        reroll_count = getattr(winner, 'reroll_count', 0) or 0
                        if reroll_count > 0:
                            congratulation_text += f"\n🔄 Реролов: {reroll_count}"

                        await bot.send_message(
                            chat_id=group_chat_id,
                            text=congratulation_text
                        )

                        logger.info(f"✅ Отправлено поздравление победителю конкурса рисунков {contest_id}: {username_display}")

                except Exception as e:
                    logger.error(f"❌ Ошибка при отправке поздравления победителю {winner.id}: {e}")
                    continue

            logger.info(f"✅ Отправлены поздравления для всех победителей конкурса {contest_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка при отправке поздравительных сообщений для конкурса {contest_id}: {e}")


async def check_all_giveaways_historical_comments(bot: Bot):
    """
    Проверяет все активные конкурсы и собирает исторические комментарии через Telethon
    Вызывается при запуске бота
    """
    try:
        msk_tz = pytz.timezone('Europe/Moscow')
        current_time_msk = datetime.now(msk_tz)
        
        async with async_session() as session:
            # Получаем все конкурсы, которые еще не закончились
            # Используем text() для безопасного запроса
            from sqlalchemy import text
            
            # Проверяем наличие колонки discussion_group_link
            if IS_SQLITE:
                result = await session.execute(text("PRAGMA table_info(giveaways)"))
                columns_info = result.fetchall()
                existing_columns = [row[1] for row in columns_info]
            else:
                result = await session.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'giveaways'
                """))
                columns_info = result.fetchall()
                existing_columns = [row[0] for row in columns_info]
            has_discussion_group_link = 'discussion_group_link' in existing_columns
            
            # Формируем запрос с учетом наличия колонки
            if has_discussion_group_link:
                query = text("""
                    SELECT id, post_link, discussion_group_link, end_date 
                    FROM giveaways 
                    WHERE end_date > :current_time 
                    AND post_link IS NOT NULL 
                    AND post_link != ''
                """)
            else:
                query = text("""
                    SELECT id, post_link, NULL as discussion_group_link, end_date 
                    FROM giveaways 
                    WHERE end_date > :current_time 
                    AND post_link IS NOT NULL 
                    AND post_link != ''
                """)
            
            result = await session.execute(query, {"current_time": current_time_msk})
            giveaways = result.fetchall()
            
            logger.info(f"🔍 Проверка исторических комментариев для {len(giveaways)} активных конкурсов...")
            
            for giveaway_row in giveaways:
                try:
                    giveaway_id = giveaway_row[0] if isinstance(giveaway_row, tuple) else giveaway_row.id
                    post_link = giveaway_row[1] if isinstance(giveaway_row, tuple) else giveaway_row.post_link
                    discussion_group_link = giveaway_row[2] if isinstance(giveaway_row, tuple) else (giveaway_row.discussion_group_link if hasattr(giveaway_row, 'discussion_group_link') else None)
                    
                    if not post_link:
                        continue
                    
                    parsed = parse_telegram_link(post_link)
                    if not parsed:
                        continue
                    
                    chat_id, message_id = parsed
                    
                    # Проверяем, есть ли уже комментарии в БД
                    result = await session.execute(
                        select(Comment).where(
                            Comment.post_message_id == message_id
                        )
                    )
                    existing_comments = result.scalars().all()
                    
                    if not existing_comments:
                        logger.info(f"📥 Для конкурса {giveaway_id} нет комментариев в БД. Комментарии будут собраны через Telethon при нажатии кнопки 'Подвести итоги'.")
                        # Удален автоматический сбор комментариев - теперь только через кнопку
                    else:
                        logger.debug(f"✅ Для конкурса {giveaway_id} уже есть {len(existing_comments)} комментариев в БД")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка при проверке конкурса {giveaway_row[0] if isinstance(giveaway_row, tuple) else 'unknown'}: {e}", exc_info=True)
                    continue
            
            logger.info(f"✅ Проверка исторических комментариев завершена")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке всех конкурсов: {e}", exc_info=True)


def register_giveaway_handlers(dp: Dispatcher):
    """
    Регистрирует обработчики для розыгрышей
    """
    # Удалена регистрация handle_message_with_reply - комментарии не сохраняются автоматически
    dp.register_message_handler(start_giveaway, commands=['giveaway'])
