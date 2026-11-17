# Инструкция по бесплатному развертыванию бота в Google Cloud Platform (GCP)

Для работы Telegram-бота 24/7 без домашнего ПК или платного хостинга лучше всего использовать **Google Cloud Compute Engine (Always Free Tier)** — виртуальную машину `e2-micro`, которая предоставляется Google **бесплатно навсегда**.

---

## Вариант 1. Запуск на бесплатном сервере GCP (Compute Engine e2-micro) — РЕКОМЕНДУЕМЫЙ ⭐️

Google предоставляет одну виртуальную машину `e2-micro` (1 ГБ RAM, 30 ГБ диск) в регионах США (`us-central1`, `us-east1`, `us-west1`) **бесплатно 24/7**.

### Шаг 1. Создание виртуальной машины в консоли Google Cloud
1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/).
2. В верхнем меню выберите или создайте проект (например, `football-bot`).
3. Откройте левое меню: **Compute Engine** ➔ **VM instances** (Экземпляры ВМ).
4. Нажмите **Create Instance** (Создать экземпляр):
   - **Name**: `laliga-bot`
   - **Region**: `us-central1` (Iowa) или `us-east1` (South Carolina) — *входят в Free Tier!*
   - **Machine configuration**: `E2` ➔ `e2-micro` (2 vCPU, 1 GB memory).
   - **Boot disk**: Нажмите *Change*, выберите **Ubuntu 22.04 LTS** или **Debian 12**, тип диска *Standard persistent disk*, размер `15-20 GB`.
   - **Firewall**: можно оставить по умолчанию.
5. Нажмите кнопку **Create**. Через 30 секунд сервер будет запущен.

---

### Шаг 2. Подключение к серверу и установка
1. В строке созданного сервера нажмите кнопку **SSH** (откроется окно терминала в браузере).
2. Выполните команду обновления и установки Python + Git:
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
```

3. Склонируйте ваш репозиторий с кодом (или загрузите проект):
```bash
git clone https://github.com/lojjk687/crispy-giggle.git football_bot
cd football_bot
```

4. Создайте виртуальное окружение и установите библиотеки:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

5. Создайте файл `.env` с вашими ключами:
```bash
cat << 'EOF' > .env
TELEGRAM_BOT_TOKEN=ВАШ_ТОКЕН_БОТА
GEMINI_API_KEY=ВАШ_КЛЮЧ_GEMINI
GROUP_CHAT_ID=-1004417705143
ADMIN_IDS=7173085717
DB_PATH=laliga_dynasty.db
EOF
```

---

### Шаг 3. Настройка автозапуска бота через systemd (работает 24/7)
Чтобы бот работал в фоновом режиме даже при закрытии браузера и автоматически перезапускался при сбоях:

1. Создайте файл службы:
```bash
sudo tee /etc/systemd/system/football_bot.service > /dev/null << 'EOF'
[Unit]
Description=La Liga AI Dynasty Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/football_bot
ExecStart=/home/ubuntu/football_bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```
*(Если имя вашего пользователя не `ubuntu`, замените `ubuntu` на вывод команды `whoami`)*.

2. Активируйте и запустите бота:
```bash
sudo systemctl daemon-reload
sudo systemctl enable football_bot
sudo systemctl start football_bot
```

3. Проверьте статус работы:
```bash
sudo systemctl status football_bot
```
Вы увидите: `Active: active (running)` — **бот запущен и работает в облаке Google Cloud!**

Для просмотра логов в реальном времени:
```bash
journalctl -u football_bot -f
```

---

## Вариант 2. Запуск через Cloud Shell (1 клик без создания ВМ)
Если вы хотите запустить бота прямо сейчас прямо из браузера:
1. Откройте значок **Cloud Shell** (терминал `>_` в правом верхнем углу [Google Cloud Console](https://console.cloud.google.com/)).
2. Загрузите файлы проекта и запустите:
```bash
git clone https://github.com/lojjk687/crispy-giggle.git football_bot
cd football_bot
pip install -r requirements.txt
python3 main.py
```
*(Примечание: Cloud Shell выключается при длительном отсутствии активности, поэтому для постоянной работы 24/7 используйте Вариант 1).*
