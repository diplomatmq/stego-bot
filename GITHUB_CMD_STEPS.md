# 📤 Создание репозитория на GitHub через CMD

## Пошаговая инструкция

### Шаг 1: Откройте CMD в папке stego

**Способ A (через проводник):**
1. Откройте папку `C:\Users\dip66332202244\PycharmProjects\stego` в проводнике
2. В адресной строке введите `cmd` и нажмите Enter

**Способ B (через Win+R):**
1. Нажмите Win+R
2. Введите `cmd` и нажмите Enter
3. Выполните:
   ```cmd
   cd C:\Users\dip66332202244\PycharmProjects\stego
   ```

### Шаг 2: Проверьте, установлен ли Git

```cmd
git --version
```

Если ошибка "git is not recognized":
- Скачайте Git: https://git-scm.com/download/win
- Установите с настройками по умолчанию
- Перезапустите CMD

### Шаг 3: Инициализируйте Git репозиторий

```cmd
git init
```

Вы увидите: `Initialized empty Git repository in C:\Users\dip66332202244\PycharmProjects\stego\.git`

### Шаг 4: Проверьте .gitignore

```cmd
type .gitignore
```

Убедитесь, что файл существует и содержит:
- `.env`
- `giveaway.db`
- `giveaway_session.session`
- `ssl/`
- `venv/`

### Шаг 5: Проверьте, какие файлы будут добавлены

```cmd
git status
```

**Должны быть:**
- ✅ Все `.py` файлы
- ✅ Все `.html`, `.css`, `.js` файлы
- ✅ Все `.md` файлы
- ✅ `requirements.txt`
- ✅ `.gitignore`

**НЕ должно быть:**
- ❌ `.env`
- ❌ `giveaway.db`
- ❌ `giveaway_session.session`
- ❌ `ssl/` (папка)
- ❌ `venv/` (папка)

Если видите `.env` или `.db` в списке - проверьте `.gitignore` еще раз.

### Шаг 6: Добавьте все файлы

```cmd
git add .
```

### Шаг 7: Сделайте первый коммит

```cmd
git commit -m "Initial commit: Telegram bot for giveaways"
```

Если видите ошибку про имя и email:
```cmd
git config --global user.name "Ваше Имя"
git config --global user.email "ваш@email.com"
```
Затем повторите `git commit`.

### Шаг 8: Создайте репозиторий на GitHub.com

1. Откройте https://github.com в браузере
2. Войдите в аккаунт (или создайте новый)
3. Нажмите **"+"** в правом верхнем углу
4. Выберите **"New repository"**
5. Заполните:
   - **Repository name**: `stego-bot` (или любое другое имя)
   - **Description**: "Telegram bot for giveaways" (опционально)
   - **Visibility**: 
     - ✅ **Private** (если хотите скрыть код)
     - ✅ **Public** (если хотите открытый код)
   - ❌ **НЕ** отмечайте "Add a README file" (у вас уже есть README.md)
   - ❌ **НЕ** отмечайте "Add .gitignore" (у вас уже есть .gitignore)
   - ❌ **НЕ** отмечайте "Choose a license"
6. Нажмите **"Create repository"**

### Шаг 9: Подключите локальный репозиторий к GitHub

GitHub покажет инструкции. Выполните в CMD (замените YOUR_USERNAME на ваш GitHub username):

```cmd
git remote add origin https://github.com/YOUR_USERNAME/stego-bot.git
```

Например, если ваш username `dip663322`, то:
```cmd
git remote add origin https://github.com/dip663322/stego-bot.git
```

### Шаг 10: Переименуйте ветку в main

```cmd
git branch -M main
```

### Шаг 11: Загрузите файлы на GitHub

```cmd
git push -u origin main
```

Вас попросят ввести:
- **Username**: ваш GitHub username
- **Password**: ваш GitHub пароль (или Personal Access Token)

**⚠️ ВАЖНО**: Если используете двухфакторную аутентификацию, нужен токен вместо пароля.

### Шаг 12: Создайте токен доступа (если нужен)

Если пароль не работает:

1. Перейдите: https://github.com/settings/tokens
2. Нажмите **"Generate new token"** → **"Generate new token (classic)"**
3. Заполните:
   - **Note**: "Local Git"
   - **Expiration**: выберите срок (например, 90 days)
   - **Scopes**: отметьте `repo` (полный доступ)
4. Нажмите **"Generate token"**
5. **Скопируйте токен** (показывается только один раз!)
6. Используйте токен вместо пароля при `git push`

---

## ✅ Готово!

После успешного `git push` вы увидите:
```
Enumerating objects: XX, done.
Counting objects: 100% (XX/XX), done.
...
To https://github.com/YOUR_USERNAME/stego-bot.git
 * [new branch]      main -> main
Branch 'main' set up to track remote branch 'main' from 'origin'.
```

Теперь ваш код на GitHub! 🎉

---

## 🔄 Обновление репозитория (после изменений)

```cmd
cd C:\Users\dip66332202244\PycharmProjects\stego
git add .
git commit -m "Описание изменений"
git push
```

---

## 🆘 Решение проблем

### Ошибка "remote origin already exists"

```cmd
git remote remove origin
git remote add origin https://github.com/YOUR_USERNAME/stego-bot.git
```

### Ошибка "Permission denied"

- Проверьте username и пароль/токен
- Создайте Personal Access Token (см. Шаг 12)

### Случайно добавили .env

```cmd
git rm --cached .env
git commit -m "Remove .env file"
git push
```

### Не видно файлов после push

- Проверьте на GitHub.com в вашем репозитории
- Обновите страницу (F5)

---

**Все команды в одном блоке для копирования:**

```cmd
cd C:\Users\dip66332202244\PycharmProjects\stego
git init
git status
git add .
git commit -m "Initial commit: Telegram bot for giveaways"
git remote add origin https://github.com/YOUR_USERNAME/stego-bot.git
git branch -M main
git push -u origin main
```

**Не забудьте заменить YOUR_USERNAME на ваш GitHub username!**

