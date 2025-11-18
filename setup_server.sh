#!/bin/bash

# Скрипт для автоматической настройки сервера
# Использование: ./setup_server.sh
# Или на сервере: bash <(curl -s https://raw.githubusercontent.com/ваш_репозиторий/setup_server.sh)

set -e

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🚀 Начинаем настройку сервера для Telegram бота...${NC}"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Ошибка: Запустите скрипт от root или с sudo${NC}"
    exit 1
fi

# Обновление системы
echo -e "${YELLOW}📦 Обновление системы...${NC}"
apt update
apt upgrade -y

# Установка Python 3.13
echo -e "${YELLOW}🐍 Установка Python 3.13...${NC}"
apt install software-properties-common -y
add-apt-repository ppa:deadsnakes/ppa -y
apt update
apt install python3.13 python3.13-venv python3.13-dev python3-pip -y

# Установка дополнительных пакетов
echo -e "${YELLOW}📦 Установка дополнительных пакетов...${NC}"
apt install git curl build-essential openssl nginx ufw -y

# Создание пользователя botuser (если не существует)
if ! id "botuser" &>/dev/null; then
    echo -e "${YELLOW}👤 Создание пользователя botuser...${NC}"
    adduser --disabled-password --gecos "" botuser
    usermod -aG sudo botuser
    echo -e "${GREEN}✅ Пользователь botuser создан${NC}"
    echo -e "${YELLOW}⚠️  Не забудьте установить пароль: passwd botuser${NC}"
else
    echo -e "${GREEN}✅ Пользователь botuser уже существует${NC}"
fi

# Настройка firewall
echo -e "${YELLOW}🔥 Настройка firewall...${NC}"
ufw allow 22/tcp
ufw allow 8000/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
echo -e "${GREEN}✅ Firewall настроен${NC}"

# Создание директории для бота
echo -e "${YELLOW}📁 Создание директории для бота...${NC}"
mkdir -p /home/botuser/stego-bot
chown -R botuser:botuser /home/botuser/stego-bot
echo -e "${GREEN}✅ Директория создана${NC}"

echo -e "${GREEN}✅ Настройка сервера завершена!${NC}"
echo ""
echo -e "${YELLOW}📝 Следующие шаги:${NC}"
echo "1. Переключитесь на пользователя botuser: su - botuser"
echo "2. Загрузите файлы бота в ~/stego-bot/"
echo "3. Создайте виртуальное окружение: python3.13 -m venv venv"
echo "4. Установите зависимости: pip install -r requirements.txt"
echo "5. Настройте .env файл"
echo "6. Создайте SSL сертификат: python generate_ssl.py"
echo "7. Запустите бота: python bot.py"
echo ""
echo -e "${GREEN}📚 Подробные инструкции в SETUP_SERVER.md${NC}"

