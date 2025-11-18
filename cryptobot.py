"""
Модуль для работы с CryptoBot API
"""
import aiohttp
import logging
import json
from config import CRYPTOBOT_API_TOKEN, CRYPTOBOT_API_URL

logger = logging.getLogger(__name__)


async def create_invoice(amount: float, currency: str = "TON", description: str = "", user_id: int = None, payload: str = None) -> dict:
    """
    Создать счет на оплату через CryptoBot
    
    Args:
        amount: Сумма оплаты
        currency: Валюта (TON, BTC, ETH, USDT, USDC, BUSD)
        description: Описание платежа
        user_id: ID пользователя Telegram (опционально)
        payload: Дополнительные данные для отслеживания платежа (опционально)
    
    Returns:
        dict: Ответ от API с информацией о счете
    """
    url = f"{CRYPTOBOT_API_URL}/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN
    }
    data = {
        "amount": str(amount),
        "asset": currency,
        "description": description
    }
    
    if payload:
        data["payload"] = payload
    
    # Указываем, что счет должен быть оплачен конкретным пользователем
    # CryptoBot автоматически привязывает счет к пользователю, который его оплачивает
    if user_id:
        # Можно использовать paid_btn_name для указания действия после оплаты
        # Но главное - payload содержит user_id для проверки
        pass
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                result = await response.json()
                if result.get("ok"):
                    logger.info(f"✅ Счет создан: {result.get('result', {}).get('invoice_id')}")
                    return result.get("result", {})
                else:
                    error_info = result.get("error", {})
                    error_name = error_info.get("name", "Unknown error") if isinstance(error_info, dict) else str(error_info)
                    logger.error(f"❌ Ошибка создания счета: {error_name}")
                    return {"error": error_name}
    except Exception as e:
        logger.error(f"❌ Ошибка при создании счета: {e}", exc_info=True)
        return {"error": str(e)}


async def get_invoice_status(invoice_id: int) -> dict:
    """
    Получить статус счета
    
    Args:
        invoice_id: ID счета
    
    Returns:
        dict: Информация о счете
    """
    url = f"{CRYPTOBOT_API_URL}/getInvoices"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN
    }
    params = {
        "invoice_ids": invoice_id
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as response:
                result = await response.json()
                if result.get("ok"):
                    invoices = result.get("result", {}).get("items", [])
                    if invoices:
                        return invoices[0]
                    return {"error": "Invoice not found"}
                else:
                    logger.error(f"❌ Ошибка получения статуса счета: {result.get('error', {}).get('name')}")
                    return {"error": result.get("error", {}).get("name", "Unknown error")}
    except Exception as e:
        logger.error(f"❌ Ошибка при получении статуса счета: {e}", exc_info=True)
        return {"error": str(e)}


async def verify_payment(invoice_id: int) -> dict:
    """
    Проверить, оплачен ли счет и получить информацию о нем
    
    Args:
        invoice_id: ID счета
    
    Returns:
        dict: Информация о счете с полями:
            - paid: bool - оплачен ли счет
            - invoice: dict - полная информация о счете
            - payload: dict - payload из счета (если есть)
    """
    invoice = await get_invoice_status(invoice_id)
    if "error" in invoice:
        return {"paid": False, "invoice": None, "payload": None, "error": invoice.get("error")}
    
    status = invoice.get("status")
    is_paid = status == "paid"
    
    # Извлекаем payload из счета
    payload_str = invoice.get("payload")
    payload_data = None
    if payload_str:
        try:
            payload_data = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
        except:
            payload_data = None
    
    return {
        "paid": is_paid,
        "invoice": invoice,
        "payload": payload_data
    }


async def get_me() -> dict:
    """
    Получить информацию о боте CryptoBot
    
    Returns:
        dict: Информация о боте
    """
    url = f"{CRYPTOBOT_API_URL}/getMe"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                result = await response.json()
                if result.get("ok"):
                    return result.get("result", {})
                else:
                    logger.error(f"❌ Ошибка получения информации о боте: {result.get('error', {}).get('name')}")
                    return {"error": result.get("error", {}).get("name", "Unknown error")}
    except Exception as e:
        logger.error(f"❌ Ошибка при получении информации о боте: {e}", exc_info=True)
        return {"error": str(e)}


async def get_currencies() -> list:
    """
    Получить список доступных валют
    
    Returns:
        list: Список валют
    """
    url = f"{CRYPTOBOT_API_URL}/getCurrencies"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                result = await response.json()
                if result.get("ok"):
                    return result.get("result", [])
                else:
                    logger.error(f"❌ Ошибка получения валют: {result.get('error', {}).get('name')}")
                    return []
    except Exception as e:
        logger.error(f"❌ Ошибка при получении валют: {e}", exc_info=True)
        return []

