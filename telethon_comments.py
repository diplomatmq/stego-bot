"""
Функции для получения комментариев через Telethon и сохранения в файл
"""
import json
import os
import logging
from typing import List, Dict, Optional
from datetime import datetime
from post_parser import get_message_link
import pytz

logger = logging.getLogger(__name__)

# Проверка наличия Telethon
try:
    from telethon import TelegramClient
    from telethon.tl import types
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    logger.warning("⚠️ Telethon не установлен. Установите: pip install telethon")


def get_comments_file_path(contest_id: int) -> str:
    """Возвращает путь к файлу с комментариями для конкурса"""
    return f"comments_contest_{contest_id}.jsonl"


async def collect_comments_via_telethon(
    channel_username: str,
    post_message_id: int,
    contest_id: int,
    api_id: int,
    api_hash: str,
    session_file: str = 'giveaway_session.session',
    discussion_group_username: Optional[str] = None,
    end_date: Optional[datetime] = None
) -> Dict:
    """
    Собирает все комментарии под постом через Telethon и сохраняет в файл
    
    Args:
        channel_username: Username канала (например, "monkeys_giveaways")
        post_message_id: ID поста в канале
        contest_id: ID конкурса
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        session_file: Путь к файлу сессии Telethon
        discussion_group_username: Username группы обсуждения (опционально, например "monkeys_gifts")
    
    Returns:
        Словарь с результатами: {'count': int, 'file_path': str, 'comments': List[Dict]}
    """
    if not HAS_TELETHON:
        raise ValueError("Telethon не установлен")
    
    if not api_id or not api_hash:
        raise ValueError("TELEGRAM_API_ID и TELEGRAM_API_HASH не настроены")
    
    comments = []
    file_path = get_comments_file_path(contest_id)
    
    try:
        client = TelegramClient(session_file, api_id, api_hash)
        
        try:
            if not client.is_connected():
                await client.start()
            
            logger.info(f"✅ Telethon: Сбор комментариев для поста {channel_username}/{post_message_id}")
            
            # Получаем канал (может быть username или числовой ID)
            try:
                # Пробуем получить как username
                if not channel_username.isdigit() and not (channel_username.startswith('-') and channel_username[1:].isdigit()):
                    channel = await client.get_entity(channel_username)
                else:
                    # Это числовой ID, используем его напрямую
                    channel = await client.get_entity(int(channel_username))
                logger.info(f"✅ Telethon: Получен канал {channel.title if hasattr(channel, 'title') else 'N/A'} (ID: {channel.id})")
            except Exception as e:
                logger.error(f"❌ Ошибка при получении канала {channel_username}: {e}")
                raise
            
            # Инициализируем переменные
            discussion_group_id = None
            source_entity = None
            reply_to_id = post_message_id  # По умолчанию используем post_message_id из канала
            
            # Получаем сообщение поста из канала для проверки связанной группы обсуждения
            post_message = None
            try:
                post_message = await client.get_messages(channel.id, ids=post_message_id)
                if post_message:
                    logger.info(f"✅ Telethon: Получен пост {post_message_id} из канала {channel_username}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось получить пост {post_message_id} из канала: {e}")
            
            # Пробуем получить группу обсуждения через discussion_group_username
            if discussion_group_username:
                try:
                    discussion_group_entity = await client.get_entity(discussion_group_username)
                    if discussion_group_entity:
                        discussion_group_id = discussion_group_entity.id
                        source_entity = discussion_group_username
                        logger.info(f"✅ Telethon: Используем указанную группу обсуждения: {discussion_group_username} (ID: {discussion_group_id})")
                        
                        # Если есть пост из канала, пытаемся найти связанное сообщение в группе обсуждения
                        if post_message and hasattr(post_message, 'replies') and post_message.replies:
                            replies = post_message.replies
                            if hasattr(replies, 'channel_id') and replies.channel_id == discussion_group_id:
                                # Если есть max_id в replies, это ID связанного сообщения в группе обсуждения
                                if hasattr(replies, 'max_id') and replies.max_id:
                                    reply_to_id = replies.max_id
                                    logger.info(f"✅ Telethon: Найден связанный пост в группе обсуждения с ID {reply_to_id}")
                                elif hasattr(replies, 'replies') and replies.replies:
                                    # Альтернативный способ - используем replies.replies
                                    reply_to_id = replies.replies
                                    logger.info(f"✅ Telethon: Найден связанный пост в группе обсуждения с ID {reply_to_id}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось получить группу обсуждения по username {discussion_group_username}: {e}")
            
            # Если не нашли через username, пробуем получить из канала
            if not source_entity and post_message:
                try:
                    if hasattr(post_message, 'replies') and post_message.replies:
                        replies = post_message.replies
                        if hasattr(replies, 'channel_id') and replies.channel_id:
                            # Получаем группу обсуждения
                            discussion_group_entity = await client.get_entity(replies.channel_id)
                            if discussion_group_entity:
                                discussion_group_id = replies.channel_id
                                logger.info(f"✅ Telethon: Найдена группа обсуждения через replies: {discussion_group_id}")
                                
                                # Используем группу обсуждения для iter_messages
                                if hasattr(discussion_group_entity, 'username') and discussion_group_entity.username:
                                    source_entity = discussion_group_entity.username
                                else:
                                    source_entity = discussion_group_id
                                
                                # Находим ID связанного сообщения в группе обсуждения
                                if hasattr(replies, 'max_id') and replies.max_id:
                                    reply_to_id = replies.max_id
                                    logger.info(f"✅ Telethon: Найден связанный пост в группе обсуждения с ID {reply_to_id}")
                                elif hasattr(replies, 'replies') and replies.replies:
                                    reply_to_id = replies.replies
                                    logger.info(f"✅ Telethon: Найден связанный пост в группе обсуждения с ID {reply_to_id}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось получить группу обсуждения из канала: {e}")
            
            # Если все еще не определили, используем канал как fallback
            if not source_entity:
                source_entity = channel_username
                reply_to_id = post_message_id  # Используем исходный post_message_id для канала
                logger.info(f"✅ Telethon: Используем канал как источник комментариев (группа обсуждения не найдена)")
            else:
                logger.info(f"✅ Telethon: Используем группу обсуждения: {source_entity}")
            
            logger.info(f"🔍 Telethon: Ищем комментарии к посту (reply_to={reply_to_id}) в {source_entity}")
            
            # Настраиваем фильтрацию по времени, если указана дата окончания
            msk_tz = pytz.timezone('Europe/Moscow')
            filter_time = None
            if end_date:
                from datetime import timedelta
                end_date_msk = end_date.astimezone(msk_tz) if end_date.tzinfo else msk_tz.localize(end_date)
                
                # Если конец конкурса до 17:30, то комментарии после 17:31 не учитываются
                if end_date_msk.hour < 17 or (end_date_msk.hour == 17 and end_date_msk.minute < 30):
                    # Устанавливаем время фильтрации: 17:31 того же дня
                    filter_time = end_date_msk.replace(hour=17, minute=31, second=0, microsecond=0)
                    logger.info(f"⏰ Фильтрация комментариев: конкурс закончился до 17:30, учитываются только комментарии до {filter_time.strftime('%Y-%m-%d %H:%M:%S')} МСК")
                else:
                    # Если конец конкурса после 17:30, учитываем все комментарии до конца конкурса + 1 минута
                    filter_time = end_date_msk.replace(second=0, microsecond=0) + timedelta(minutes=1)
                    logger.info(f"⏰ Фильтрация комментариев: учитываются только комментарии до {filter_time.strftime('%Y-%m-%d %H:%M:%S')} МСК")
            
            # Используем iter_messages с reply_to для получения комментариев
            # По примеру: iter_messages('channel', reply_to=message_id, reverse=True)
            collected_count = 0
            filtered_count = 0
            async for message in client.iter_messages(source_entity, reply_to=reply_to_id, reverse=True):
                if not message:
                    continue
                
                collected_count += 1
                
                # Фильтрация по времени
                if filter_time and message.date:
                    message_date_msk = message.date.astimezone(msk_tz) if message.date.tzinfo else msk_tz.localize(message.date)
                    if message_date_msk > filter_time:
                        filtered_count += 1
                        logger.debug(f"  ⏭️ Пропущен комментарий {message.id} (время: {message_date_msk.strftime('%Y-%m-%d %H:%M:%S')} МСК, после {filter_time.strftime('%Y-%m-%d %H:%M:%S')} МСК)")
                        continue
                
                # Получаем chat_id из сообщения (где находится комментарий)
                comment_chat_id = None
                if hasattr(message, 'chat_id'):
                    comment_chat_id = message.chat_id
                elif hasattr(message, 'peer_id'):
                    if hasattr(message.peer_id, 'channel_id'):
                        comment_chat_id = message.peer_id.channel_id
                    elif hasattr(message.peer_id, 'chat_id'):
                        comment_chat_id = message.peer_id.chat_id
                
                # Формируем данные комментария
                comment_data = {
                    'message_id': message.id,
                    'date': message.date.isoformat() if message.date else None,
                    'text': message.text or message.message or '',
                    'comment_link': None,
                    'user_id': None,
                    'user_first_name': None,
                    'user_username': None,
                    'user_title': None,
                    'chat_id': comment_chat_id
                }
                
                # Получаем информацию об отправителе
                if isinstance(message.sender, types.User):
                    comment_data['user_id'] = message.sender.id
                    comment_data['user_first_name'] = message.sender.first_name
                    # Получаем username (может быть None если у пользователя нет публичного username)
                    comment_data['user_username'] = message.sender.username if hasattr(message.sender, 'username') else None
                    comment_data['user_title'] = None
                    sender_name = message.sender.first_name
                    if message.sender.last_name:
                        sender_name += f" {message.sender.last_name}"
                    # Добавляем username к имени если он есть
                    if comment_data['user_username']:
                        sender_name += f" (@{comment_data['user_username']})"
                else:
                    comment_data['user_id'] = None
                    comment_data['user_first_name'] = None
                    comment_data['user_username'] = None
                    comment_data['user_title'] = message.sender.title if hasattr(message.sender, 'title') else 'Unknown'
                    sender_name = comment_data['user_title']
                    # Для каналов/чатов тоже может быть username
                    if hasattr(message.sender, 'username') and message.sender.username:
                        comment_data['user_username'] = message.sender.username
                        sender_name += f" (@{message.sender.username})"
                
                logger.info(f"  📝 {message.date} {sender_name}: {message.text[:50] if message.text else 'нет текста'}")
                
                # Формируем ссылку на комментарий
                try:
                    # Используем discussion_group_id если доступен, иначе chat_id из сообщения
                    if discussion_group_id:
                        chat_id_for_link = str(discussion_group_id)
                    elif comment_chat_id:
                        chat_id_for_link = str(comment_chat_id)
                    else:
                        # Fallback: используем username группы обсуждения или канала
                        chat_id_for_link = channel_username
                    
                    comment_data['comment_link'] = get_message_link(chat_id_for_link, message.id)
                    logger.info(f"    🔗 Ссылка: {comment_data['comment_link']}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось сформировать ссылку для комментария {message.id}: {e}")
                
                comments.append(comment_data)
            
            logger.info(f"✅ Telethon: Найдено {collected_count} комментариев, из них {filtered_count} отфильтровано по времени, {len(comments)} комментариев будет использовано для выбора победителей")
            
            # Сохраняем комментарии в файл (JSON Lines формат)
            # Каждый комментарий на новой строке как JSON, после каждого комментария пустая строка
            with open(file_path, 'w', encoding='utf-8') as f:
                for comment in comments:
                    json_line = json.dumps(comment, ensure_ascii=False)
                    f.write(json_line + '\n')
                    f.write('\n')  # Пустая строка после каждого комментария
            
            logger.info(f"✅ Сохранено {len(comments)} комментариев в файл {file_path}")
            
            return {
                'count': len(comments),
                'file_path': file_path,
                'comments': comments
            }
            
        finally:
            if client.is_connected():
                await client.disconnect()
    
    except Exception as e:
        logger.error(f"❌ Ошибка при сборе комментариев через Telethon: {e}", exc_info=True)
        raise


def read_comments_from_file(file_path: str) -> List[Dict]:
    """
    Читает комментарии из файла (JSON Lines формат)
    
    Args:
        file_path: Путь к файлу
    
    Returns:
        Список словарей с комментариями
    """
    if not os.path.exists(file_path):
        logger.warning(f"⚠️ Файл {file_path} не найден")
        return []
    
    comments = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            current_comment = []
            
            for line in f:
                line = line.strip()
                
                # Пропускаем пустые строки (разделители)
                if not line:
                    if current_comment:
                        # Объединяем накопленные строки в один JSON
                        json_str = ''.join(current_comment)
                        try:
                            comment = json.loads(json_str)
                            comments.append(comment)
                        except json.JSONDecodeError as e:
                            logger.warning(f"⚠️ Ошибка парсинга JSON: {e}")
                        current_comment = []
                    continue
                
                # Накапливаем строки JSON (на случай многострочного JSON)
                current_comment.append(line)
            
            # Обрабатываем последний комментарий, если файл не заканчивается пустой строкой
            if current_comment:
                json_str = ''.join(current_comment)
                try:
                    comment = json.loads(json_str)
                    comments.append(comment)
                except json.JSONDecodeError as e:
                    logger.warning(f"⚠️ Ошибка парсинга JSON: {e}")
        
        logger.info(f"✅ Прочитано {len(comments)} комментариев из файла {file_path}")
        return comments
    
    except Exception as e:
        logger.error(f"❌ Ошибка при чтении файла {file_path}: {e}", exc_info=True)
        return []


def pick_random_winners_from_file(file_path: str, winners_count: int) -> List[Dict]:
    """
    Выбирает случайных победителей из файла с комментариями
    
    Args:
        file_path: Путь к файлу с комментариями
        winners_count: Количество победителей
    
    Returns:
        Список словарей с данными победителей
    """
    from randomizer import pick_random_winners
    
    comments = read_comments_from_file(file_path)
    
    if not comments:
        raise ValueError(f"В файле {file_path} нет комментариев")
    
    # Извлекаем ссылки на комментарии
    comment_links = []
    for comment in comments:
        if comment.get('comment_link'):
            comment_links.append(comment['comment_link'])
        elif comment.get('message_id'):
            # Если нет ссылки, используем message_id
            comment_links.append(f"comment_{comment['message_id']}")
    
    if not comment_links:
        raise ValueError("Не найдено ни одной ссылки на комментарий в файле")
    
    # Выбираем победителей через randomizer
    winner_links = pick_random_winners(comment_links, winners_count)
    
    # Находим полные данные победителей
    winners = []
    for winner_link in winner_links:
        # Ищем комментарий по ссылке или message_id
        winner_comment = None
        for comment in comments:
            if comment.get('comment_link') == winner_link:
                winner_comment = comment
                break
        
        if winner_comment:
            winners.append(winner_comment)
        else:
            # Если не нашли полные данные, создаем минимальный объект
            winners.append({
                'comment_link': winner_link,
                'message_id': None,
                'text': '',
                'user_first_name': None,
                'user_username': None
            })
    
    return winners

