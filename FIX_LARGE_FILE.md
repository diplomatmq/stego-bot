# 🔧 Исправление: Удаление большого файла из истории Git

## Проблема
Файл `GitHubDesktopSetup-x64.exe` был добавлен в коммит, и даже после удаления он остается в истории Git.

## Решение

### Вариант 1: Удалить из последнего коммита (если файл в последнем коммите)

```cmd
git rm --cached GitHubDesktopSetup-x64.exe
git commit --amend -m "Initial commit: Telegram bot for giveaways"
git push -u origin main --force
```

### Вариант 2: Полностью переписать историю (если файл в нескольких коммитах)

```cmd
git filter-branch --force --index-filter "git rm --cached --ignore-unmatch GitHubDesktopSetup-x64.exe" --prune-empty --tag-name-filter cat -- --all
git push -u origin main --force
```

### Вариант 3: Самый простой - начать заново (если коммитов мало)

```cmd
# Удалите .git папку
rmdir /s /q .git

# Начните заново
git init
git add .
git commit -m "Initial commit: Telegram bot for giveaways"
git remote add origin https://github.com/diplomatmq/stego-bot.git
git branch -M main
git push -u origin main --force
```

**⚠️ ВАЖНО**: Используйте `--force` только если вы единственный, кто работает с репозиторием!

