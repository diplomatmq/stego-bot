from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String, nullable=True)  # Username пользователя
    role = Column(String, default="user")  # creator / admin / user
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    channel_link = Column(String, nullable=True)  # Ссылка на канал админа
    chat_link = Column(String, nullable=True)  # Ссылка на чат админа
    experience = Column(Integer, default=0)  # Опыт пользователя
    ton_wallet = Column(String, nullable=True)  # TON кошелек пользователя
    purchased_items = Column(JSON, nullable=True)  # Купленные товары: {"themes": ["kitty", "mario"], "avatarStars": [], "nftGifts": []}

class Channel(Base):
    __tablename__ = "channels"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    link = Column(String)
    admin_id = Column(Integer, ForeignKey("users.id"))
    admin = relationship("User", backref="channels")

class Giveaway(Base):
    __tablename__ = "giveaways"
    id = Column(Integer, primary_key=True)
    channel_id = Column(Integer, ForeignKey("channels.id"))
    post_link = Column(String, nullable=True)  # Ссылка на пост для выборки комментариев (только для рандом комментариев)
    discussion_group_link = Column(String, nullable=True)  # Ссылка на группу обсуждения (например, t.me/monkeys_gifts)
    channel_link = Column(String, nullable=True)  # Ссылка на канал конкурса (из активов админа или фиксированная для создателя)
    winners_count = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    start_date = Column(DateTime, nullable=True)  # Дата начала конкурса (МСК)
    end_date = Column(DateTime, nullable=False)  # Дата окончания конкурса (МСК)
    channel = relationship("Channel", backref="giveaways")
    name = Column(String, nullable=False)
    prize = Column(String, nullable=False)  # Описание приза
    prize_links = Column(JSON, nullable=True)  # Массив ссылок на NFT-подарки (количество = winners_count)
    conditions = Column(String)  # Условия участия в конкурсе
    created_by = Column(Integer, nullable=True)  # ID создателя конкурса (admin или creator) - telegram_id
    is_confirmed = Column(Boolean, default=False)  # Подтверждены ли победители
    winners_selected_at = Column(DateTime, nullable=True)  # Когда были выбраны победители (до подтверждения)
    contest_type = Column(String, default="random_comment")  # Тип конкурса: "random_comment" или "drawing"
    submission_end_date = Column(DateTime, nullable=True)  # Дата окончания приема работ (для конкурса рисунков, МСК)

class Winner(Base):
    __tablename__ = "winners"
    id = Column(Integer, primary_key=True)
    giveaway_id = Column(Integer, ForeignKey("giveaways.id", ondelete="CASCADE"))
    comment_link = Column(String, nullable=True)  # Ссылка на комментарий (для рандом комментариев, NULL для конкурса рисунков)
    photo_link = Column(String, nullable=True)  # Ссылка на фотографию (для конкурса рисунков, NULL для рандом комментариев)
    photo_message_id = Column(Integer, nullable=True)  # ID сообщения с фотографией в Telegram (для конкурса рисунков)
    user_id = Column(Integer, nullable=True)  # telegram_id победителя
    user_username = Column(String, nullable=True)  # username победителя
    prize_link = Column(String, nullable=True)  # Ссылка на приз, который выиграл пользователь
    place = Column(Integer, nullable=True)  # Место победителя (1, 2, 3 и т.д.)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class History(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True)
    action = Column(String)
    user_id = Column(Integer, ForeignKey("users.id"))
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    from_user_id = Column(Integer)  # telegram_id отправителя
    to_user_id = Column(Integer, nullable=True)  # telegram_id получателя (для creator = None)
    message_text = Column(String, nullable=False)
    status = Column(String, default="pending")  # pending, approved, rejected
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    responded_at = Column(DateTime, nullable=True)


class Comment(Base):
    """Модель для хранения комментариев из групп обсуждения"""
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, nullable=False)  # ID чата (канала или группы)
    post_message_id = Column(Integer, nullable=False)  # ID поста в канале
    comment_message_id = Column(Integer, nullable=False)  # ID комментария в группе обсуждения
    comment_chat_id = Column(String, nullable=False)  # ID группы обсуждения
    comment_link = Column(String, nullable=False)  # Ссылка на комментарий
    user_id = Column(Integer, nullable=True)  # ID пользователя, оставившего комментарий
    username = Column(String, nullable=True)  # Username пользователя
    text = Column(String, nullable=True)  # Текст комментария
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))  # Когда комментарий был сохранен в БД
    
    # Индексы для быстрого поиска
    __table_args__ = (
        {'sqlite_autoincrement': True},
    )

class Participant(Base):
    """Модель для хранения участников конкурсов"""
    __tablename__ = "participants"
    id = Column(Integer, primary_key=True)
    giveaway_id = Column(Integer, ForeignKey("giveaways.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, nullable=False)  # telegram_id участника
    username = Column(String, nullable=True)  # Username участника
    photo_link = Column(String, nullable=True)  # Ссылка на фотографию участника (для конкурса рисунков, NULL для рандом комментариев)
    photo_message_id = Column(Integer, nullable=True)  # ID сообщения с фотографией в Telegram (для конкурса рисунков)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Уникальный индекс: один пользователь может участвовать в конкурсе только один раз
    __table_args__ = (
        UniqueConstraint('giveaway_id', 'user_id', name='uq_participant_giveaway_user'),
        {'sqlite_autoincrement': True},
    )
