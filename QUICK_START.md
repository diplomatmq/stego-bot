# ⚡ Быстрый старт - Публикация бота на сервер

## 🎯 Минимальные шаги для запуска

### 1. Подготовка на вашем компьютере

```bash
# Убедитесь, что у вас есть .env файл
cat .env
# Должны быть заполнены: BOT_TOKEN, CREATOR_ID, WEBAPP_URL
# WEBAPP_URL должен быть с HTTPS (например: https://90.156.211.211)
```

### 2. Загрузка файлов на сервер

**Вариант A: Автоматический (через скрипт)**
```bash
# Сделайте скрипт исполняемым
chmod +x deploy.sh

# Запустите деплой
./deploy.sh botuser@ваш_ip_адрес
```

**Вариант B: Вручную (через SCP)**
```bash
# Создайте папку на сервере
ssh botuser@ваш_ip "mkdir -p ~/stego-bot"

# Загрузите файлы
scp *.py *.html *.css *.js requirements.txt botuser@ваш_ip:~/stego-bot/
scp .env botuser@ваш_ip:~/stego-bot/
```

### 3. Настройка на сервере

```bash
# Подключитесь к серверу
ssh botuser@ваш_ip

# Перейдите в папку
cd ~/stego-bot

# Установите Python 3.13 (если еще не установлен)
sudo apt update
sudo apt install python3.13 python3.13-venv -y

# Создайте виртуальное окружение
python3.13 -m venv venv

# Активируйте его
source venv/bin/activate

# Установите зависимости
pip install --upgrade pip
pip install -r requirements.txt

# Проверьте .env файл
nano .env
# Убедитесь, что BOT_TOKEN, CREATOR_ID и WEBAPP_URL заполнены
# WEBAPP_URL должен быть с HTTPS (например: https://90.156.211.211)

# Создайте SSL сертификат (обязательно!)
sudo apt install openssl -y
python generate_ssl.py ваш_ip_адрес
# Или если IP уже в .env: python generate_ssl.py
```

### 4. Первый запуск

```bash
# Запустите бота
python bot.py
```

Если все работает, нажмите `Ctrl+C` и переходите к следующему шагу.

### 5. Настройка автозапуска

```bash
# Создайте systemd service
sudo nano /etc/systemd/system/stego-bot.service
```

Вставьте (замените `botuser` на ваше имя пользователя):
```ini
[Unit]
Description=Stego Telegram Bot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/stego-bot
Environment="PATH=/home/botuser/stego-bot/venv/bin"
ExecStart=/home/botuser/stego-bot/venv/bin/python /home/botuser/stego-bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# Активируйте сервис
sudo systemctl daemon-reload
sudo systemctl enable stego-bot.service
sudo systemctl start stego-bot.service

# Проверьте статус
sudo systemctl status stego-bot.service
```

### 6. Готово! ✅

Бот запущен и будет автоматически перезапускаться при перезагрузке сервера.

**Полезные команды:**
```bash
# Посмотреть логи
sudo journalctl -u stego-bot.service -f

# Перезапустить бота
sudo systemctl restart stego-bot.service

# Остановить бота
sudo systemctl stop stego-bot.service
```

---

## 📋 Чек-лист

- [ ] .env файл создан и заполнен (BOT_TOKEN, CREATOR_ID, WEBAPP_URL)
- [ ] Файлы загружены на сервер
- [ ] Python 3.13 установлен
- [ ] Виртуальное окружение создано
- [ ] Зависимости установлены
- [ ] SSL сертификат создан (generate_ssl.py)
- [ ] Бот запускается в тестовом режиме
- [ ] Systemd service создан
- [ ] Автозапуск включен

---

## 🆘 Проблемы?

**Бот не запускается:**
```bash
sudo journalctl -u stego-bot.service -n 50
```

**Ошибка "Module not found":**
```bash
source venv/bin/activate
pip install -r requirements.txt
```

**Бот не отвечает:**
- Проверьте токен в .env
- Проверьте статус: `sudo systemctl status stego-bot.service`

---

📚 **Подробная инструкция:** см. `DEPLOYMENT.md`

