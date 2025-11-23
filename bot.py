from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text
from config import DATABASE_URL
import logging

# Базовый класс для моделей
Base = declarative_base()

# Создаём движок и сессию
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
IS_SQLITE = engine.url.get_backend_name().startswith("sqlite")


async def get_session():
    async with async_session() as session:
        yield session


# ✅ Добавляем функцию инициализации базы
async def init_db():
    """Создаёт все таблицы, если их ещё нет, и добавляет недостающие колонки"""
    # Импорт моделей здесь, чтобы они зарегистрировались в Base.metadata
    from models import User, Channel, Giveaway, Winner, History, Comment, Participant

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        if IS_SQLITE:
            # Migrate: add missing columns to existing users table
            try:
                result = await conn.execute(text("PRAGMA table_info(users)"))
                existing_user_columns = [row[1] for row in result.fetchall()] if result else []
                
                if 'username' not in existing_user_columns:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))
                    print("✅ Добавлена колонка users.username")
                
                if 'channel_link' not in existing_user_columns:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN channel_link VARCHAR"))
                    print("✅ Добавлена колонка users.channel_link")
                
                if 'chat_link' not in existing_user_columns:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN chat_link VARCHAR"))
                    print("✅ Добавлена колонка users.chat_link")
                
                if 'experience' not in existing_user_columns:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN experience INTEGER DEFAULT 0"))
                    print("✅ Добавлена колонка users.experience")
                
                if 'ton_wallet' not in existing_user_columns:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN ton_wallet VARCHAR"))
                    print("✅ Добавлена колонка users.ton_wallet")
                
                if 'purchased_items' not in existing_user_columns:
                    await conn.execute(text("ALTER TABLE users ADD COLUMN purchased_items TEXT"))
                    print("✅ Добавлена колонка users.purchased_items")
            except Exception as e:
                print(f"⚠️ Migration users error: {e}")
            
            # Migrate: add missing columns to existing giveaways table
            try:
                # Try to query table structure
                result = await conn.execute(text("PRAGMA table_info(giveaways)"))
                existing_giveaway_columns = [row[1] for row in result.fetchall()] if result else []
                
                if 'name' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN name VARCHAR"))
                    print("✅ Добавлена колонка giveaways.name")
                
                if 'prize' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN prize VARCHAR"))
                    print("✅ Добавлена колонка giveaways.prize")
                
                if 'end_date' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN end_date DATETIME"))
                    print("✅ Добавлена колонка giveaways.end_date")
                
                if 'conditions' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN conditions VARCHAR"))
                    print("✅ Добавлена колонка giveaways.conditions")
                
                if 'created_by' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN created_by INTEGER"))
                    print("✅ Добавлена колонка giveaways.created_by")
                
                if 'is_confirmed' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN is_confirmed BOOLEAN DEFAULT 0"))
                    print("✅ Добавлена колонка giveaways.is_confirmed")
                
                if 'winners_selected_at' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN winners_selected_at DATETIME"))
                    print("✅ Добавлена колонка giveaways.winners_selected_at")
                
                if 'discussion_group_link' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN discussion_group_link VARCHAR"))
                    print("✅ Добавлена колонка giveaways.discussion_group_link")
                
                if 'channel_link' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN channel_link VARCHAR"))
                    print("✅ Добавлена колонка giveaways.channel_link")
                
                if 'start_date' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN start_date DATETIME"))
                    print("✅ Добавлена колонка giveaways.start_date")
                
                if 'prize_links' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN prize_links TEXT"))
                    print("✅ Добавлена колонка giveaways.prize_links")
                
                if 'winners_count' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN winners_count INTEGER DEFAULT 1"))
                    print("✅ Добавлена колонка giveaways.winners_count")
            except Exception as e:
                print(f"⚠️ Migration giveaways error: {e}")
            
            # Migrate: create messages table if it doesn't exist
            try:
                result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"))
                messages_table_exists = result.fetchone() is not None
                
                if not messages_table_exists:
                    await conn.execute(text("""
                        CREATE TABLE messages (
                            id INTEGER PRIMARY KEY,
                            from_user_id INTEGER NOT NULL,
                            to_user_id INTEGER,
                            message_text VARCHAR NOT NULL,
                            status VARCHAR DEFAULT 'pending',
                            created_at DATETIME,
                            responded_at DATETIME
                        )
                    """))
                    print("✅ Создана таблица messages")
            except Exception as e:
                print(f"⚠️ Migration messages error: {e}")
            
            # Migrate: create comments table if it doesn't exist
            try:
                result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='comments'"))
                comments_table_exists = result.fetchone() is not None
                
                if not comments_table_exists:
                    await conn.execute(text("""
                        CREATE TABLE comments (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            chat_id VARCHAR NOT NULL,
                            post_message_id INTEGER NOT NULL,
                            comment_message_id INTEGER NOT NULL,
                            comment_chat_id VARCHAR NOT NULL,
                            comment_link VARCHAR NOT NULL,
                            user_id INTEGER,
                            username VARCHAR,
                            text VARCHAR,
                            created_at DATETIME
                        )
                    """))
                    # Создаём индексы для быстрого поиска
                    await conn.execute(text("CREATE INDEX idx_comments_chat_post ON comments(chat_id, post_message_id)"))
                    await conn.execute(text("CREATE INDEX idx_comments_comment_chat ON comments(comment_chat_id, post_message_id)"))
                    print("✅ Создана таблица comments с индексами")
            except Exception as e:
                print(f"⚠️ Migration comments error: {e}")
            
            # Создаем таблицу participants если её нет
            try:
                result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='participants'"))
                if not result.fetchone():
                    await conn.execute(text("""
                        CREATE TABLE participants (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            giveaway_id INTEGER NOT NULL,
                            user_id INTEGER NOT NULL,
                            username VARCHAR,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (giveaway_id) REFERENCES giveaways(id)
                        )
                    """))
                    # Создаем уникальный индекс для предотвращения дубликатов
                    await conn.execute(text("CREATE UNIQUE INDEX idx_participant_unique ON participants(giveaway_id, user_id)"))
                    print("✅ Создана таблица participants")
            except Exception as e:
                print(f"⚠️ Migration participants error: {e}")
            
            # Migrate: add missing columns to existing winners table
            try:
                result = await conn.execute(text("PRAGMA table_info(winners)"))
                existing_winner_columns = [row[1] for row in result.fetchall()] if result else []
                
                if 'user_id' not in existing_winner_columns:
                    await conn.execute(text("ALTER TABLE winners ADD COLUMN user_id INTEGER"))
                    print("✅ Добавлена колонка winners.user_id")
                
                if 'user_username' not in existing_winner_columns:
                    await conn.execute(text("ALTER TABLE winners ADD COLUMN user_username VARCHAR"))
                    print("✅ Добавлена колонка winners.user_username")
                
                if 'prize_link' not in existing_winner_columns:
                    await conn.execute(text("ALTER TABLE winners ADD COLUMN prize_link VARCHAR"))
                    print("✅ Добавлена колонка winners.prize_link")
                
                if 'place' not in existing_winner_columns:
                    await conn.execute(text("ALTER TABLE winners ADD COLUMN place INTEGER"))
                    print("✅ Добавлена колонка winners.place")
                
                # Делаем comment_link nullable (для конкурса рисунков)
                # SQLite не поддерживает изменение NOT NULL на NULL напрямую, но если колонка уже nullable, ничего не делаем
                # Если нужно изменить существующую колонку, это требует пересоздания таблицы
                # Но так как мы уже указали nullable=True в модели, новые таблицы будут созданы правильно
                # Для существующих таблиц нужно проверить, есть ли уже колонка photo_link
                
                if 'photo_link' not in existing_winner_columns:
                    await conn.execute(text("ALTER TABLE winners ADD COLUMN photo_link VARCHAR"))
                    print("✅ Добавлена колонка winners.photo_link")
                
                if 'photo_message_id' not in existing_winner_columns:
                    await conn.execute(text("ALTER TABLE winners ADD COLUMN photo_message_id INTEGER"))
                    print("✅ Добавлена колонка winners.photo_message_id")
            except Exception as e:
                print(f"⚠️ Migration winners error: {e}")
            
            # Migrate: add missing columns to existing participants table
            try:
                result = await conn.execute(text("PRAGMA table_info(participants)"))
                existing_participant_columns = [row[1] for row in result.fetchall()] if result else []
                
                if 'photo_link' not in existing_participant_columns:
                    await conn.execute(text("ALTER TABLE participants ADD COLUMN photo_link VARCHAR"))
                    print("✅ Добавлена колонка participants.photo_link")
                
                if 'photo_message_id' not in existing_participant_columns:
                    await conn.execute(text("ALTER TABLE participants ADD COLUMN photo_message_id INTEGER"))
                    print("✅ Добавлена колонка participants.photo_message_id")
                
                # Проверяем, существует ли уникальный индекс на (giveaway_id, user_id)
                try:
                    index_result = await conn.execute(text("""
                        SELECT name FROM sqlite_master 
                        WHERE type='index' 
                        AND name='idx_participant_unique'
                    """))
                    if not index_result.fetchone():
                        # Если индекс не существует, создаем его
                        await conn.execute(text("CREATE UNIQUE INDEX idx_participant_unique ON participants(giveaway_id, user_id)"))
                        print("✅ Создан уникальный индекс idx_participant_unique на participants(giveaway_id, user_id)")
                except Exception as e:
                    print(f"⚠️ Ошибка при создании уникального индекса: {e}")
            except Exception as e:
                print(f"⚠️ Migration participants error: {e}")
            
            # Migrate: add missing columns to existing giveaways table (contest_type, submission_end_date, jury)
            try:
                # Получаем список существующих колонок (разные запросы для SQLite и PostgreSQL)
                if IS_SQLITE:
                    result = await conn.execute(text("PRAGMA table_info(giveaways)"))
                    existing_giveaway_columns = [row[1] for row in result.fetchall()] if result else []
                else:
                    # Для PostgreSQL
                    result = await conn.execute(text("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'giveaways'
                    """))
                    existing_giveaway_columns = [row[0] for row in result.fetchall()] if result else []
                
                if 'contest_type' not in existing_giveaway_columns:
                    await conn.execute(text("ALTER TABLE giveaways ADD COLUMN contest_type VARCHAR DEFAULT 'random_comment'"))
                    print("✅ Добавлена колонка giveaways.contest_type")
                
                if 'submission_end_date' not in existing_giveaway_columns:
                    if IS_SQLITE:
                        await conn.execute(text("ALTER TABLE giveaways ADD COLUMN submission_end_date DATETIME"))
                    else:
                        await conn.execute(text("ALTER TABLE giveaways ADD COLUMN submission_end_date TIMESTAMP"))
                    print("✅ Добавлена колонка giveaways.submission_end_date")
                
                if 'jury' not in existing_giveaway_columns:
                    # Для SQLite JSON хранится как TEXT, для PostgreSQL как JSONB
                    if IS_SQLITE:
                        await conn.execute(text("ALTER TABLE giveaways ADD COLUMN jury TEXT"))
                    else:
                        await conn.execute(text("ALTER TABLE giveaways ADD COLUMN jury JSONB"))
                    print("✅ Добавлена колонка giveaways.jury")
            except Exception as e:
                print(f"⚠️ Migration giveaways (contest_type, submission_end_date, jury) error: {e}")
    
    print("✅ База данных инициализирована")
