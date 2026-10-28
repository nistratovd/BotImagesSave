# Telegram → локальный архив на сервере

Бот для Ubuntu 24.04 LTS / Python 3.12. После `/start` отвечает «Привет!» (текст настраивается). Фото и видео сохраняются **на диске вашего сервера**, в каталоге `/var/lib/botimagessave/media/<Telegram user ID>/`. Новые загрузки добавляются в ту же папку, в том числе после перезапуска. Google Drive, Google API и OAuth больше не используются.

Бот работает только в личных чатах. Альбом сохраняется как несколько отдельных файлов. Документы принимаются только с MIME image/video и распознаваемым заголовком JPEG, PNG, WebP, MP4/MOV, WebM/MKV или AVI. Другие файлы и сообщения игнорируются. Файлы не перекодируются; MOV и MKV получают расширения MP4 и WebM соответственно. Подписи не сохраняются. Имена генерирует бот, а не отправитель. Для несжатого фото отправляйте его как файл.

## 1. Создание Telegram-бота

1. Откройте проверенного **@BotFather** в Telegram.
2. Выполните `/newbot`, задайте имя и уникальный username с окончанием `bot`.
3. Сохраните выданный токен как пароль. При утечке отзовите его через `/revoke`.
4. Вставьте токен в конфигурацию на сервере. Не добавляйте реальную конфигурацию в Git, не присылайте токен в чат.

Используется long polling, входящие порты не нужны. Не запускайте два экземпляра с одним токеном. Если бот ранее работал с webhook, удалите webhook через Telegram Bot API `deleteWebhook` безопасным инструментом, не оставляя токен в истории команд или браузера.

## 2. Установка на Ubuntu 24.04

Команды выполняются по SSH на сервере. Сначала проверьте, что имена пользователя `botimagessave` и каталогов `/opt/botimagessave`, `/etc/botimagessave`, `/var/lib/botimagessave` не заняты другим приложением.

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv ca-certificates
git clone https://github.com/nistratovd/BotImagesSave.git
cd BotImagesSave
sudo bash scripts/manage.sh install
sudoedit /etc/botimagessave/config.toml
```

Заполните конфигурацию:

```toml
[telegram]
token = "ТОКЕН_ОТ_BOTFATHER"
greeting = "Привет!"
# Пустой список разрешает всем; для личного архива задайте свои числовые ID.
allowed_user_ids = [123456789]

[storage]
directory = "/var/lib/botimagessave/media"
max_file_mb = 20
min_free_mb = 1024
```

`allowed_user_ids` принимает числовые Telegram ID, а не username. `max_file_mb` — любое положительное целое число (например, 500 или 2000). Через облачный Telegram Bot API скачиваются только файлы до 20 MB; для больших файлов настройте локальный Bot API по разделу ниже. Без него бот объяснит ограничение отправителю, даже если в конфигурации указан больший лимит. `min_free_mb` — резерв свободного места; проверяется перед загрузкой и записью. Это не дисковая квота: другие процессы тоже могут расходовать место. При исчерпании резерва загрузка завершается ошибкой, существующие файлы автоматически не удаляются.

```bash
sudo bash scripts/manage.sh start
sudo bash scripts/manage.sh status
sudo bash scripts/manage.sh logs
```

`start` включает автозапуск. systemd создаёт `/var/lib/botimagessave` с владельцем `botimagessave` и правами 0700. Каталоги пользователей создаются с правами 0700, файлы — 0600. Реальный TOML доступен root и группе службы (0640). Для изменения приветствия, ID или параметров хранения отредактируйте TOML и выполните `restart`.

### Другой каталог хранения

Предпочтительно использовать путь внутри `/var/lib/botimagessave`: эта область доступна на запись в systemd sandbox. Для отдельного диска, например `/srv/bot-media`, создайте каталог с владельцем `botimagessave` и правами 0700, укажите абсолютный путь в `storage.directory`, затем выполните `sudo systemctl edit botimagessave`:

```ini
[Service]
ReadWritePaths=/srv/bot-media
```

Далее `sudo systemctl daemon-reload` и `sudo bash scripts/manage.sh restart`. Если каталог лежит на отдельном монтировании, добавьте в override `[Unit]` с `RequiresMountsFor=/srv/bot-media`, чтобы служба не писала на системный диск при отсутствии монтирования. Не используйте символьные ссылки, пути под `/home`, каталоги 3x-ui или общедоступную web-папку. При смене пути прежний архив автоматически не переносится; переносите его при остановленном боте с сохранением прав.

## 3. Обновление с прежней версии Google Drive

Из существующего клона:

```bash
sudo bash scripts/manage.sh stop
git pull --ff-only
sudo bash scripts/manage.sh reinstall
sudoedit /etc/botimagessave/config.toml
```

Удалите из TOML раздел `[google]`, добавьте параметры `[storage]`, показанные выше (не создавайте второй раздел `[storage]`, дополните существующий). Если путь и резерв не указать, применятся `/var/lib/botimagessave/media` и 1024 MB. Старый `[google]` игнорируется; Google credentials больше не нужны.

```bash
sudo bash scripts/manage.sh start
```

Прежние файлы в Google Drive не переносятся и не удаляются. При желании отдельно скачайте их в архив при остановленном боте. Старый `/etc/botimagessave/secrets/token.json` больше не используется: после проверки локальной версии удалите его вручную и отзовите OAuth-доступ приложения в настройках Google Account → Security → Third-party connections. В существующем venv могут остаться старые Google-пакеты, но код их не импортирует; новая установка их не устанавливает.

## 4. Проверка работы и просмотр файлов

1. Отправьте `/start`, проверьте приветствие.
2. Отправьте небольшое фото и видео. Дождитесь «Сохранено.».
3. Проверьте архив:

```bash
sudo find /var/lib/botimagessave/media -type f
sudo du -sh /var/lib/botimagessave/media
df -h /var/lib/botimagessave
```

4. Перезапустите бота и отправьте ещё фото: прежние файлы должны сохраниться, новый — добавиться в каталог вашего ID.
5. Отправьте PDF или текст: нового файла и ответа быть не должно. Проверьте второй аккаунт: отдельный каталог либо игнорирование при ограничении ID.

Файлы закрыты от других пользователей сервера. Для получения архива используйте SSH/SFTP с соответствующими правами; не открывайте доступ к файлам через публичный web-сервер. Организуйте резервное копирование каталога и TOML в защищённое место. Файлы на диске не шифруются приложением: для шифрования нужен зашифрованный диск/том.

При ошибке смотрите `logs`, права на каталог и свободное место. После исправления и достижения лимита перезапусков выполните `sudo systemctl reset-failed botimagessave`, затем `start`. Не публикуйте токены при диагностике.

## 5. Управление, удаление и переустановка

Из каталога клона:

```bash
sudo bash scripts/manage.sh stop       # остановить и выключить автозапуск
sudo bash scripts/manage.sh restart    # перезапустить
sudo bash scripts/manage.sh status
sudo bash scripts/manage.sh logs
```

Обновление:

```bash
sudo bash scripts/manage.sh stop
git pull --ff-only
sudo bash scripts/manage.sh reinstall
sudo bash scripts/manage.sh start
```

`install` и `reinstall` сохраняют TOML и архив, останавливают службу и не запускают её до явного `start`.

```bash
sudo bash scripts/manage.sh uninstall  # удалить службу и программу, сохранить архив и TOML
sudo bash scripts/manage.sh purge --confirm # после uninstall удалить также TOML
```

**Ни uninstall, ни purge не удаляют архив.** Системный пользователь сохраняется для сохранения владельца файлов. Каталог клона также остаётся. Для полной очистки сначала сохраните нужные файлы, затем вручную удалите выбранный каталог архива и системного пользователя. Учтите нестандартные пути хранения. Для переустановки повторите install/start; после purge заново заполните TOML.

## Безопасность и совместимость с 3x-ui

Бот не меняет 3x-ui, Xray, маршрутизацию, firewall, nginx и другие службы. Не слушает входящие порты. Требуется только исходящий HTTPS к `api.telegram.org`. Работает от отдельного непривилегированного пользователя с systemd sandbox, лимитами CPU 30%, RAM 384 MB и закрытыми правами на данные. Не используйте каталоги и credentials 3x-ui.

Временные файлы создаются на диске архива, удаляются после обработки; при аварийном завершении процесса могут остаться скрытые `.tg-media-*`/`.pending-*` — удаляйте их только при остановленном боте. Готовый файл появляется после полной записи через атомарное переименование. Проверка свободного места не заменяет мониторинг и отдельный раздел для архива: медиа могут расходовать общий диск и влиять на другие службы. Для небольшого личного сервера обязательно ограничьте список Telegram ID и следите за местом.

Медиа не запускаются. Проверка заголовков не является антивирусом; осторожно открывайте недоверенные файлы. Абсолютной гарантии безопасности и отсутствия влияния на общие ресурсы нет: обновляйте ОС и зависимости, контролируйте нагрузку. Автоматического удаления старых файлов нет. Повторная отправка создаёт новый файл. Нет гарантии exactly-once при сбоях; Telegram обычно хранит ожидающие обновления не более 24 часов.

## Разработка

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
BOT_CONFIG=/абсолютный/путь/config.toml .venv/bin/python bot.py
```

Для запуска без systemd задайте доступный абсолютный `storage.directory` и держите TOML закрытым. Тесты не требуют Google или Telegram credentials; реальная проверка Telegram описана выше.


## Файлы больше 20 MB: локальный Telegram Bot API

Установка большего `max_file_mb` снимает ограничение **бота**, но не ограничение облачного API Telegram. Для фактического скачивания больших файлов нужен официальный [telegram-bot-api](https://github.com/tdlib/telegram-bot-api) с `--local` **на том же сервере**. Этот режим позволяет скачивать без ограничения размера со стороны Bot API; остаются ограничения отправки Telegram, ваш лимит и свободное место. Большие фото отправляйте как файл, чтобы Telegram не сжимал их.

Это дополнительная служба: она слушает только `127.0.0.1:8081`, не требует открытия firewall и не меняет 3x-ui. Сначала проверьте `sudo ss -ltnp`, что порт 8081 свободен. Если занят, выберите другой в unit и TOML. Локальный API не имеет отдельного пароля: не публикуйте его порт и запускайте на доверенном сервере. Бот и API используют одного системного пользователя, чтобы читать скачанные файлы. Для API нужны дополнительные CPU, RAM и дисковое место; на малом VPS проверьте запас ресурсов. Лимит RAM службы API в примере 768 MB, бота — 384 MB.

### Установка API (один раз)

1. Получите **api_id и api_hash** на https://my.telegram.org → API development tools, войдя своим Telegram-аккаунтом. Это отдельные параметры, не токен BotFather. Не присылайте их в чат и не сохраняйте в Git.
2. Установите бот через `manage.sh install`, если он ещё не установлен (это создаёт пользователя). Соберите официальный API. Сборка требует ресурсов: лучше выполнить её на отдельной Ubuntu 24.04 той же архитектуры, если VPS занят 3x-ui. Ниже ограничен параллелизм; firewall и Docker не меняются.

```bash
sudo apt-get install -y git cmake g++ gperf libssl-dev zlib1g-dev
# В отдельном рабочем каталоге, где ещё нет папки telegram-bot-api:
git clone --recursive https://github.com/tdlib/telegram-bot-api.git
cd telegram-bot-api
# Зафиксируйте SHA проверенной версии для последующих обновлений:
git rev-parse HEAD
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/telegram-bot-api
cmake --build build -j1
sudo cmake --install build
/opt/telegram-bot-api/bin/telegram-bot-api --help
```

Если upstream меняет требования сборки, используйте официальный build guide из его README. Проверьте, что бинарник установлен именно в `/opt/telegram-bot-api/bin/telegram-bot-api`.

3. Вернитесь в клон **BotImagesSave**. Создайте закрытый файл настроек:

```bash
sudo install -o root -g root -m 600 /dev/null /etc/botimagessave/telegram-api.env
sudoedit /etc/botimagessave/telegram-api.env
```

Содержимое (подставьте свои значения):

```ini
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash
```

При повторной настройке редактируйте существующий файл; команда `install /dev/null` обнуляет его.

### Переключение бота

Остановите бота и **один раз** выйдите из облачного API. Helper читает токен из защищённого TOML и не выводит его:

```bash
sudo bash scripts/manage.sh stop
sudo /opt/botimagessave/.venv/bin/python scripts/telegram_logout.py
```

Продолжайте только после `Cloud logOut succeeded`. Согласно Telegram, после этого нельзя войти обратно в облачный API 10 минут; локальный сервер можно запускать сразу. Не выполняйте этот helper при каждом рестарте.

```bash
sudo install -m 644 scripts/telegram-bot-api.service /etc/systemd/system/telegram-bot-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot-api
sudo systemctl status telegram-bot-api --no-pager
sudoedit /etc/botimagessave/config.toml
```

Добавьте параметры в **существующие** разделы TOML, не создавая повторные:

```toml
[telegram]
# Сохраните здесь token, greeting и allowed_user_ids.
local_api_url = "http://127.0.0.1:8081"
local_file_root = "/var/lib/telegram-bot-api"

[storage]
# Сохраните directory и min_free_mb.
max_file_mb = 500
```

`local_file_root` — каталог данных API, не каталог архива. Бот принимает локальные пути только внутри него. API должен работать с `--local`; HTTP-сервер в другом режиме не подходит. Внешние адреса в `local_api_url` намеренно запрещены.

Чтобы API запускался перед ботом, выполните `sudo systemctl edit botimagessave` и добавьте:

```ini
[Unit]
Requires=telegram-bot-api.service
After=telegram-bot-api.service
```

```bash
sudo systemctl daemon-reload
sudo bash scripts/manage.sh reinstall
sudo systemctl reset-failed botimagessave
sudo bash scripts/manage.sh start
```

Отправьте тестовое видео размером, например, 30 MB (меньше заданного лимита). Проверьте «Сохранено.» и фактический файл в архиве. Полная проверка требует реальных Telegram credentials; модульные тесты проверяют маршрутизацию запросов и чтение локального файла без Telegram.

### Место на диске, отключение и удаление API

API хранит собственные скачанные данные в `/var/lib/telegram-bot-api`, отдельно от постоянного архива. При сохранении бот использует ещё временную и конечную копию. Планируйте место под API-кэш, архив, две копии обрабатываемого файла и резерв. Большие локальные файлы копируются по частям без загрузки целиком в RAM. Резерв `min_free_mb` проверяет диск архива, не отдельный диск API. Следите за обоими. Не удаляйте файлы API во время работы; не настраивайте бездумное удаление всего каталога — там же состояние и авторизация.

`manage.sh stop/uninstall` управляет только ботом. Для остановки также API:

```bash
sudo systemctl disable --now telegram-bot-api
```

Для полного удаления API после остановки бота: удалите его unit `/etc/systemd/system/telegram-bot-api.service`, выполните `sudo systemctl daemon-reload`, удалите бинарники `/opt/telegram-bot-api`, закрытый файл `telegram-api.env` и, если не нужны, данные `/var/lib/telegram-bot-api`. Архив `/var/lib/botimagessave/media` удалять не требуется. Уберите override `Requires/After` для API, прежде чем возвращаться в облачный режим.

Для возврата в облако остановите бота, вызовите метод Telegram `logOut` **на локальном API** безопасным инструментом, остановите API, очистите `local_api_url` в TOML и установите `max_file_mb = 20`. Helper выше предназначен только для выхода из облака. Не запускайте облачный и локальный экземпляры одновременно.


## Форматированное приветствие BETBOOM

В `config.example.toml` подготовлен полный текст приветствия с абзацами и жирными заголовками для Telegram. В существующем `/etc/botimagessave/config.toml` **замените** старый `greeting` многострочным значением из примера и добавьте `greeting_parse_mode = "HTML"` внутри `[telegram]`. Не копируйте весь пример поверх рабочего конфига: сохраните токен, ID пользователей и настройки локального API. `reinstall` намеренно не перезаписывает рабочий TOML.

HTML применяется только к приветствию: `<b>текст</b>` отображается жирным, переводы строк сохраняются. Для обычного текста задайте `greeting_parse_mode = ""` или уберите параметр. Старые конфигурации продолжают отправлять приветствие без разметки. Для буквальных символов `<`, `>` и `&` в HTML-тексте используйте `&lt;`, `&gt;` и `&amp;`; тройные кавычки относятся к синтаксису TOML, а не к сообщению.

После изменения кода выполните `git pull --ff-only` и `sudo bash scripts/manage.sh reinstall`, после изменения конфигурации — `sudo bash scripts/manage.sh restart`. Отправьте `/start` для проверки. Заявление о видео до 500 МБ соответствует работе только при `max_file_mb = 500` (или больше) и настроенном локальном Telegram Bot API. Общая ёмкость архива ограничена свободным местом, квоты на число файлов в коде нет.
