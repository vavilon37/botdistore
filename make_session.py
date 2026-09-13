"""Разовая генерация строки сессии для channel_source.

Запускать НА СВОЁМ КОМПЬЮТЕРЕ, не на хостинге — нужен ввод кода из Telegram.

    pip install telethon
    python make_session.py

Спросит api_id и api_hash (берутся на https://my.telegram.org → API development
tools), номер телефона, код из Telegram и облачный пароль, если он включён.
На выходе — длинная строка: её значение кладётся в переменную TG_SESSION
в панели хостинга.

Строка даёт полный доступ к аккаунту. Не коммитить, не пересылать, не
показывать. Если утекла — отзовите сессию в Telegram:
Настройки → Устройства → завершить сеанс.
"""
# telethon.sync превращает методы клиента в синхронные — без него
# get_me() вернёт корутину, а не пользователя.
from telethon.sync import TelegramClient
from telethon.sessions import StringSession


def main():
    print("Данные берутся на https://my.telegram.org → API development tools\n")
    api_id = input("api_id: ").strip()
    api_hash = input("api_hash: ").strip()

    if not api_id.isdigit():
        print("api_id должен быть числом.")
        return

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        # Печатаем строку сразу после входа: если что-то упадёт дальше,
        # сессия уже создана на стороне Telegram и потерять её нельзя.
        print("\nСтрока сессии — скопируйте целиком в переменную TG_SESSION:\n")
        print(client.session.save())

        try:
            me = client.get_me()
            print(f"\nВошли как @{me.username or me.id}")
        except Exception as e:
            print(f"\n(имя аккаунта получить не удалось: {e} — на сессию не влияет)")

        print("\nНе коммитьте её и никому не показывайте.")


if __name__ == "__main__":
    main()
