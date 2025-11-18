"""
Продвинутый рандомайзер для выбора победителей из комментариев.
Использует криптографически стойкий генератор случайных чисел для максимальной случайности.
"""
import secrets
import hashlib
import time
from typing import List, Union, Dict, Any


def generate_entropy(seed_data: str = None) -> bytes:
    """
    Генерирует дополнительную энтропию для повышения случайности.
    Использует текущее время и seed_data для создания уникального хэша.
    """
    if seed_data is None:
        seed_data = ""
    
    # Комбинируем время, seed_data и системную энтропию
    entropy_data = f"{time.time_ns()}{seed_data}{secrets.token_hex(16)}"
    return hashlib.sha256(entropy_data.encode()).digest()


def fisher_yates_shuffle(sequence: List[Any], entropy: bytes = None) -> List[Any]:
    """
    Алгоритм Фишера-Йетса для равномерного перемешивания.
    Использует криптографически стойкий генератор вместо стандартного random.
    """
    if not sequence:
        return []
    
    # Создаем копию, чтобы не изменять исходный список
    shuffled = sequence.copy()
    n = len(shuffled)
    
    if entropy is None:
        entropy = generate_entropy()
    
    # Используем хэш энтропии для генерации случайных индексов
    entropy_index = 0
    
    for i in range(n - 1, 0, -1):
        # Генерируем криптографически стойкое случайное число
        # Используем байты из энтропии, если доступны
        if entropy_index + 4 <= len(entropy):
            # Берем 4 байта для создания числа
            random_bytes = entropy[entropy_index:entropy_index + 4]
            random_int = int.from_bytes(random_bytes, 'big')
            entropy_index += 4
        else:
            # Если энтропия закончилась, генерируем новую
            entropy = generate_entropy()
            random_bytes = entropy[:4]
            random_int = int.from_bytes(random_bytes, 'big')
            entropy_index = 4
        
        # Приводим к диапазону [0, i]
        j = random_int % (i + 1)
        
        # Меняем местами элементы
        shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
    
    return shuffled


def multiple_shuffle_pass(sequence: List[Any], passes: int = 3) -> List[Any]:
    """
    Выполняет несколько проходов перемешивания для увеличения случайности.
    Каждый проход использует новую энтропию.
    """
    if not sequence or passes < 1:
        return sequence.copy() if sequence else []
    
    result = sequence.copy()
    
    for _ in range(passes):
        # Генерируем новую энтропию для каждого прохода
        entropy = generate_entropy(str(time.time_ns()))
        result = fisher_yates_shuffle(result, entropy)
    
    return result


def remove_duplicates(comments: List[Union[str, Dict]]) -> List[Union[str, Dict]]:
    """
    Удаляет дубликаты комментариев.
    Для строк сравнивает значения, для словарей - извлекает уникальный идентификатор.
    """
    if not comments:
        return []
    
    seen = set()
    unique = []
    
    for comment in comments:
        if isinstance(comment, str):
            # Для строк используем саму строку как ключ
            key = comment
        elif isinstance(comment, dict):
            # Для словарей используем link или message_id, или комбинацию
            key = comment.get('link') or comment.get('message_id') or comment.get('user_id')
            if key is None:
                # Если нет уникального ключа, используем хэш всего словаря
                key = hash(str(sorted(comment.items())))
        else:
            # Для других типов используем строковое представление
            key = str(comment)
        
        if key and key not in seen:
            seen.add(key)
            unique.append(comment)
    
    return unique


def pick_random_winners(comments: Union[List[str], List[Dict]], count: int) -> List[Union[str, Dict]]:
    """
    Выбирает случайных победителей из списка комментариев.
    
    Использует криптографически стойкий генератор случайных чисел (secrets модуль)
    вместо стандартного random для максимальной случайности и непредсказуемости.
    
    Особенности реализации:
    1. Множественные проходы перемешивания (5 проходов алгоритма Fisher-Yates)
    2. Использование системной энтропии (время, токены, SHA256 хэширование)
    3. Криптографически стойкий выбор каждого победителя
    4. Дополнительное перемешивание результата для финальной случайности
    5. Один пользователь может выиграть несколько раз (если оставил несколько комментариев)
    
    Технические детали:
    - Использует secrets.token_hex() для генерации энтропии
    - Алгоритм Fisher-Yates для равномерного распределения
    - SHA256 для хэширования энтропийных данных
    - time.time_ns() для наносекундной точности временных меток
    
    Args:
        comments: Список комментариев. Может быть:
            - List[str]: список ссылок на комментарии (например, ["https://t.me/...", ...])
            - List[Dict]: список словарей с информацией о комментариях
                (например, [{"link": "...", "user_id": 123}, ...])
        count: Количество победителей для выбора (должно быть положительным числом)
    
    Returns:
        Список выбранных комментариев в том же формате, что и входные данные.
        Если count >= len(comments), возвращаются все комментарии (перемешанные).
        Если comments пуст, возвращается пустой список.
    
    Raises:
        Нет исключений - функция обрабатывает все крайние случаи безопасно.
    
    Examples:
        >>> # Работа с простыми строками (ссылки на комментарии)
        >>> comments = ["https://t.me/channel/123", "https://t.me/channel/456", "https://t.me/channel/789"]
        >>> winners = pick_random_winners(comments, 2)
        >>> len(winners)
        2
        >>> all(w in comments for w in winners)
        True
        
        >>> # Работа со словарями
        >>> comments = [
        ...     {"link": "https://t.me/channel/1", "user_id": 123},
        ...     {"link": "https://t.me/channel/2", "user_id": 456},
        ...     {"link": "https://t.me/channel/3", "user_id": 789}
        ... ]
        >>> winners = pick_random_winners(comments, 1)
        >>> len(winners)
        1
        >>> isinstance(winners[0], dict)
        True
        
        >>> # Разрешение дубликатов (один пользователь может выиграть несколько раз)
        >>> comments = ["link1", "link2", "link1", "link3"]
        >>> winners = pick_random_winners(comments, 3)
        >>> len(winners)
        3
        >>> # Могут быть дубликаты, если пользователь оставил несколько комментариев
    """
    if not comments:
        return []
    
    if count <= 0:
        return []
    
    # Шаг 1: Если запрашивается больше победителей, чем есть комментариев
    if count >= len(comments):
        # Возвращаем все комментарии, но перемешанные
        return multiple_shuffle_pass(comments, passes=5)
    
    # Шаг 2: Множественные проходы перемешивания для максимальной случайности
    # НЕ удаляем дубликаты - один пользователь может выиграть несколько раз
    shuffled = multiple_shuffle_pass(comments, passes=5)
    
    # Шаг 3: Выбираем победителей используя криптографически стойкий выбор
    # Используем secrets.choice для каждого элемента вместо random.sample
    winners = []
    remaining = shuffled.copy()
    
    for _ in range(count):
        if not remaining:
            break
        
        # Генерируем криптографически стойкий случайный индекс
        entropy = generate_entropy(str(time.time_ns()))
        random_bytes = entropy[:4]
        random_int = int.from_bytes(random_bytes, 'big')
        
        # Выбираем элемент
        index = random_int % len(remaining)
        winner = remaining.pop(index)
        winners.append(winner)
        
        # Дополнительное перемешивание оставшихся элементов для следующей итерации
        if remaining:
            entropy = generate_entropy(str(time.time_ns()))
            remaining = fisher_yates_shuffle(remaining, entropy)
    
    # Шаг 4: Финальное перемешивание результата для дополнительной случайности
    if len(winners) > 1:
        entropy = generate_entropy(str(time.time_ns()))
        winners = fisher_yates_shuffle(winners, entropy)
    
    return winners
