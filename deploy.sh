#!/bin/bash

# Скрипт для быстрого деплоя бота на сервер
# Использование: ./deploy.sh user@server_ip

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Проверка аргументов
if [ -z "$1" ]; then
    echo -e "${RED}Ошибка: Укажите пользователя и IP сервера${NC}"
    echo "Использование: ./deploy.sh user@server_ip"
    exit 1
fi

SERVER=$1
REMOTE_DIR="~/stego-bot"

echo -e "${GREEN}🚀 Начинаем деплой бота на сервер...${NC}"

# Проверка наличия .env файла
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️  Внимание: .env файл не найден!${NC}"
    echo "Создайте .env файл перед деплоем."
    read -p "Продолжить? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Список файлов для загрузки
FILES=(
    "bot.py"
    "web_server.py"
    "db.py"
    "models.py"
    "config.py"
    "helpers.py"
    "giveaway.py"
    "creator.py"
    "cryptobot.py"
    "collection.py"
    "picture.py"
    "post_parser.py"
    "randomizer.py"
    "telethon_comments.py"
    "setup_telethon_session.py"
    "requirements.txt"
)

HTML_FILES=(
    "prob.html"
    "user.html"
    "admin.html"
    "creator.html"
    "index.html"
    "style.css"
    "script.js"
)

echo -e "${GREEN}📦 Создаем папку на сервере...${NC}"
ssh $SERVER "mkdir -p $REMOTE_DIR"

echo -e "${GREEN}📤 Загружаем Python файлы...${NC}"
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        scp "$file" "$SERVER:$REMOTE_DIR/"
        echo "  ✓ $file"
    else
        echo -e "  ${YELLOW}⚠ $file не найден${NC}"
    fi
done

echo -e "${GREEN}📤 Загружаем HTML/CSS/JS файлы...${NC}"
for file in "${HTML_FILES[@]}"; do
    if [ -f "$file" ]; then
        scp "$file" "$SERVER:$REMOTE_DIR/"
        echo "  ✓ $file"
    else
        echo -e "  ${YELLOW}⚠ $file не найден${NC}"
    fi
done

# Загружаем .env если существует
if [ -f ".env" ]; then
    echo -e "${GREEN}📤 Загружаем .env файл...${NC}"
    scp .env "$SERVER:$REMOTE_DIR/"
    echo "  ✓ .env"
fi

# Загружаем изображения если есть
if [ -f "AoT.jpg" ]; then
    echo -e "${GREEN}📤 Загружаем изображения...${NC}"
    scp AoT.jpg "$SERVER:$REMOTE_DIR/"
    echo "  ✓ AoT.jpg"
fi

# Загружаем скрипт генерации SSL
if [ -f "generate_ssl.py" ]; then
    echo -e "${GREEN}📤 Загружаем скрипт генерации SSL...${NC}"
    scp generate_ssl.py "$SERVER:$REMOTE_DIR/"
    echo "  ✓ generate_ssl.py"
fi

# Загружаем папку drawing_uploads если существует
if [ -d "drawing_uploads" ]; then
    echo -e "${GREEN}📤 Загружаем папку drawing_uploads...${NC}"
    scp -r drawing_uploads "$SERVER:$REMOTE_DIR/"
    echo "  ✓ drawing_uploads/"
fi

# Загружаем базу данных если существует (опционально)
if [ -f "giveaway.db" ]; then
    echo -e "${YELLOW}⚠️  Найден файл базы данных giveaway.db${NC}"
    read -p "Загрузить базу данных на сервер? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        scp giveaway.db "$SERVER:$REMOTE_DIR/"
        echo "  ✓ giveaway.db"
    fi
fi

# Загружаем сессию Telethon если существует (опционально)
if [ -f "giveaway_session.session" ]; then
    echo -e "${YELLOW}⚠️  Найден файл сессии Telethon${NC}"
    read -p "Загрузить сессию на сервер? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        scp giveaway_session.session "$SERVER:$REMOTE_DIR/"
        echo "  ✓ giveaway_session.session"
    fi
fi

echo -e "${GREEN}✅ Файлы загружены!${NC}"
echo ""
echo -e "${YELLOW}📝 Следующие шаги на сервере:${NC}"
echo "1. Подключитесь к серверу: ssh $SERVER"
echo "2. Перейдите в папку: cd $REMOTE_DIR"
echo "3. Создайте виртуальное окружение: python3.13 -m venv venv"
echo "4. Активируйте его: source venv/bin/activate"
echo "5. Установите зависимости: pip install -r requirements.txt"
echo "6. Настройте .env файл (BOT_TOKEN, CREATOR_ID, WEBAPP_URL)"
echo "7. Создайте SSL сертификат: python generate_ssl.py ваш_ip"
echo "8. Запустите бота: python bot.py"
echo ""
echo -e "${GREEN}📚 Подробные инструкции в файле DEPLOYMENT.md${NC}"

