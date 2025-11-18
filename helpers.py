from config import CREATOR_ID
from models import User, History

def is_creator(user_id: int) -> bool:
    return user_id == CREATOR_ID

async def get_user(session, telegram_id: int):
    return await session.scalar(
        session.query(User).filter(User.telegram_id == telegram_id)
    )

async def is_admin(session, telegram_id: int) -> bool:
    user = await get_user(session, telegram_id)
    return user and user.role == "admin"

async def log_action(session, user_id: int, action: str):
    session.add(History(user_id=user_id, action=action))
    await session.commit()
