import os
import logging
import time
from dotenv import load_dotenv
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

VK_TOKEN = os.getenv('VK_TOKEN')
GROUP_ID = int(os.getenv('GROUP_ID', 0))
OWNER_PROFILE_LINK = os.getenv('OWNER_PROFILE_LINK', 'https://vk.com/id123456789')

if not VK_TOKEN or not GROUP_ID:
    raise ValueError('Заполните VK_TOKEN и GROUP_ID в файле .env')

vk_session = vk_api.VkApi(token=VK_TOKEN, api_version='5.131')
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, GROUP_ID)

# Приоритет размеров фото ВК: w > z > y > x > m > s
SIZE_PRIORITY = {'w': 0, 'z': 1, 'y': 2, 'x': 3, 'm': 4, 's': 5}
IMAGE_DOC_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'}

# Лимит длины одного сообщения (символов)
MAX_MESSAGE_LENGTH = 3500


def get_main_keyboard():
    """Основная клавиатура с кнопками."""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button('Помощь', color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button('Как это работает?', color=VkKeyboardColor.SECONDARY)
    keyboard.add_line()
    keyboard.add_button('Пинг', color=VkKeyboardColor.SECONDARY)
    return keyboard.get_keyboard()


def get_help_keyboard():
    """Клавиатура для раздела Помощь с кнопкой-ссылкой на владельца."""
    keyboard = VkKeyboard(one_time=True)
    keyboard.add_openlink_button('Написать владельцу', OWNER_PROFILE_LINK)
    return keyboard.get_keyboard()


def send_message(peer_id, text, keyboard=None):
    """Отправка сообщения с клавиатурой."""
    try:
        vk.messages.send(
            peer_id=peer_id,
            message=text,
            random_id=get_random_id(),
            keyboard=keyboard if keyboard else get_main_keyboard()
        )
    except Exception as e:
        logger.error(f'Не удалось отправить сообщение: {e}')


def shorten_url(url):
    """
    Сокращает ссылку через метод utils.getShortLink.
    Если не получилось – возвращает исходную.
    """
    try:
        response = vk.utils.getShortLink(url=url)
        short_url = response.get('short_url')
        if short_url:
            return short_url
    except Exception as e:
        logger.warning(f'Не удалось сократить ссылку {url}: {e}')
    return url


def get_best_photo_url(photo):
    """Выбирает самую большую версию фото и возвращает прямую ссылку."""
    sizes = photo.get('sizes', [])
    if not sizes:
        return None

    best = max(
        sizes,
        key=lambda s: (
            SIZE_PRIORITY.get(s.get('type'), 99),
            s.get('width', 0) * s.get('height', 0)
        )
    )
    return best.get('url')


def build_all_links(attachments):
    """
    Собирает список прямых ссылок для всех фото/изображений.
    Возвращает список строк вида "📷 Фото N: короткая_ссылка".
    """
    links = []
    media_count = 0

    for att in attachments:
        att_type = att.get('type')

        if att_type == 'photo':
            media_count += 1
            photo = att.get('photo', {})
            direct_url = get_best_photo_url(photo)
            if direct_url:
                short = shorten_url(direct_url)
                links.append(f'📷 Фото {media_count}: {short}')
            else:
                logger.warning(f'Фото {media_count}: не удалось получить прямую ссылку')

        elif att_type == 'doc':
            doc = att.get('doc', {})
            ext = doc.get('ext', '').lower()
            if ext in IMAGE_DOC_EXTS:
                media_count += 1
                direct_url = doc.get('url')
                if direct_url:
                    short = shorten_url(direct_url)
                    links.append(f'🖼️ Изображение {media_count}: {short}')

    logger.info(f'Найдено медиа-вложений: {media_count}, собрано ссылок: {len(links)}')
    return links


def send_long_message(peer_id, text, keyboard=None):
    """
    Отправляет длинное сообщение, разбивая на части по лимиту ВК.
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        send_message(peer_id, text, keyboard)
    else:
        parts = []
        current = ''
        for line in text.split('\n'):
            if len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
                if current:
                    parts.append(current)
                current = line
            else:
                current = (current + '\n' + line) if current else line
        if current:
            parts.append(current)

        for i, part in enumerate(parts):
            if i == 0:
                send_message(peer_id, part, keyboard)
            else:
                send_message(peer_id, part)
            time.sleep(0.3)


def get_full_message_via_api(message_id):
    """
    Получает полное сообщение через messages.getById.
    Это нужно, потому что Long Poll иногда не передаёт все вложения.
    """
    try:
        response = vk.messages.getById(message_ids=message_id)
        if response.get('items'):
            return response['items'][0]
    except Exception as e:
        logger.warning(f'Не удалось получить сообщение через API: {e}')
    return None


def handle_message_new(event):
    message = event.obj.message
    peer_id = message.get('peer_id')
    message_id = message.get('id')
    text = message.get('text', '').strip().lower()

    # Сначала берём вложения из события Long Poll
    attachments = message.get('attachments', [])

    # Затем пытаемся получить полное сообщение через API
    full_msg = get_full_message_via_api(message_id)
    if full_msg:
        text = full_msg.get('text', text).strip().lower()
        api_attachments = full_msg.get('attachments', [])
        if api_attachments:
            attachments = api_attachments  # используем более полный список

    logger.info(f'Сообщение от {peer_id}: текст={text!r}, вложений={len(attachments)}')
    for i, att in enumerate(attachments):
        logger.info(f'  Вложение {i+1}: type={att.get("type")}')

    # Команда Помощь
    if text in ['помощь', 'help', '/help']:
        help_text = (
            '🆘 Помощь\n\n'
            'Если у вас появился вопрос или жалоба по поводу бота, '
            'напишите владельцу:\n'
            f'{OWNER_PROFILE_LINK}\n\n'
            'Нажмите кнопку ниже, чтобы открыть профиль владельца.'
        )
        send_message(peer_id, help_text, keyboard=get_help_keyboard())
        return

    # Команда "Как это работает?"
    if text in ['как это работает?', 'как это работает', 'как работает', '/how']:
        how_text = (
            'ℹ️ Как это работает:\n\n'
            '1. Отправьте мне одну или несколько фотографий как вложение.\n'
            '2. Я соберу для каждой фотографии прямую ссылку.\n'
            '3. Вы получите столько ссылок, сколько фотографий отправили.\n\n'
            'Просто отправьте фото — и всё готово!'
        )
        send_message(peer_id, how_text)
        return

    # Пинг
    if text in ['пинг', 'ping', '/ping']:
        send_message(peer_id, '🏓 Понг! Бот работает.')
        return

    # Стартовая команда
    if text in ['начать', 'старт', '/start']:
        send_message(
            peer_id,
            '👋 Привет! Я умею превращать фотографии в прямые ссылки.\n\n'
            'Отправьте мне одну или несколько фотографий как вложение, '
            'и я пришлю прямые ссылки на каждую из них.\n\n'
            'Если нужна помощь, нажмите кнопку «Помощь».'
        )
        return

    # Если есть вложения – собираем все прямые ссылки
    if attachments:
        all_links = build_all_links(attachments)
        if all_links:
            reply = f'✅ Готово! Я обработал {len(all_links)} изображений.\n\n' + '\n'.join(all_links)
            send_long_message(peer_id, reply)
        else:
            send_message(
                peer_id,
                '🤔 Я не нашёл фотографии в вашем сообщении.\n'
                'Пожалуйста, отправьте фото как вложение.'
            )
        return

    # Обычное сообщение без фото
    send_message(
        peer_id,
        '👋 Привет! Я умею превращать фотографии в прямые ссылки.\n\n'
        'Просто отправьте мне одну или несколько фотографий как вложение, '
        'и я пришлю прямые ссылки на каждую из них.\n\n'
        'Если нужна помощь, нажмите кнопку «Помощь».'
    )


def main():
    logger.info('Бот запущен')
    try:
        for event in longpoll.listen():
            if event.type == VkBotEventType.MESSAGE_NEW:
                try:
                    handle_message_new(event)
                except Exception as e:
                    logger.exception(f'Ошибка обработки события: {e}')
                    peer_id = event.obj.message.get('peer_id')
                    if peer_id:
                        send_message(peer_id, '⚠️ Произошла ошибка при обработке сообщения. Попробуйте ещё раз.')
    except KeyboardInterrupt:
        logger.info('Бот остановлен')
    except Exception as e:
        logger.exception(f'Критическая ошибка: {e}')


if __name__ == '__main__':
    main()
