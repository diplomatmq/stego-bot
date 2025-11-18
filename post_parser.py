"""Утилиты для парсинга ссылок на посты Telegram и получения комментариев"""
import re
from typing import Optional, Tuple


def parse_telegram_link(link: str) -> Optional[Tuple[str, int]]:
    """
    Парсит ссылку на пост Telegram и возвращает (chat_id, message_id)
    
    Поддерживаемые форматы:
    - https://t.me/channel/123
    - https://t.me/c/chat_id/message_id
    - t.me/channel/123
    """
    if not link:
        return None
    
    # Убираем пробелы и извлекаем чистую ссылку
    link = link.strip()
    
    # Формат: t.me/channel/message_id
    pattern1 = r't\.me/([a-zA-Z0-9_]+)/(\d+)'
    match = re.search(pattern1, link)
    if match:
        channel_username = match.group(1)
        message_id = int(match.group(2))
        return (f"@{channel_username}", message_id)
    
    # Формат: t.me/c/chat_id/message_id
    pattern2 = r't\.me/c/(-?\d+)/(\d+)'
    match = re.search(pattern2, link)
    if match:
        chat_id = int(match.group(1))
        message_id = int(match.group(2))
        return (str(chat_id), message_id)
    
    return None


def parse_telegram_chat_link(link: str) -> Optional[str]:
    """
    Парсит ссылку на группу/канал Telegram без message_id и возвращает chat_id или username
    
    Поддерживаемые форматы:
    - https://t.me/channel
    - t.me/channel
    - @channel
    """
    if not link:
        return None
    
    # Убираем пробелы и извлекаем чистую ссылку
    link = link.strip()
    
    # Если уже начинается с @, возвращаем как есть
    if link.startswith('@'):
        return link
    
    # Формат: t.me/channel или https://t.me/channel
    pattern = r'(?:https?://)?t\.me/([a-zA-Z0-9_]+)'
    match = re.search(pattern, link)
    if match:
        channel_username = match.group(1)
        return f"@{channel_username}"
    
    return None


def get_message_link(chat_id: str, message_id: int) -> str:
    """Создаёт ссылку на сообщение для сохранения"""
    if chat_id.startswith('@'):
        return f"https://t.me/{chat_id[1:]}/{message_id}"
    else:
        # Для приватных чатов используем формат с chat_id
        return f"https://t.me/c/{chat_id.replace('-100', '')}/{message_id}"











