import asyncio
import logging
import os
import re
from typing import Tuple

import aiohttp

COMMIT_SHA = os.environ['COMMIT_SHA']

# commits for test alert builder
# COMMIT_SHA = '4015bd9c48b45910727569fff5e770000d85d207' # all clients + server and test server + web
# COMMIT_SHA = '9cc3f0fb7c390c8cb8b789e9377f10ed5e80a089' # web and web res together
# COMMIT_SHA = '4efaf918af43054ba3ff76068e83d135a9a2535d' # web
# COMMIT_SHA = 'e2d725c2b3813d7c170f50b0ab21424a71466f6d' # web res

TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
GITHUB_PAT = os.environ['GITHUB_PAT']

REPOSITORY = os.environ.get('REPOSITORY', 'CHUKEPC/telegram-crawler-public')
ROOT_TREE_DIR = os.environ.get('ROOT_TREE_DIR', 'data')

BASE_GITHUB_API = 'https://api.github.com/'
GITHUB_LAST_COMMITS = 'repos/{repo}/commits/{sha}'

BASE_TELEGRAM_API = 'https://api.telegram.org/bot{token}/'
TELEGRAM_SEND_MESSAGE = 'sendMessage'

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

STATUS_TO_EMOJI = {
    'added': '✅',
    'modified': '📝',
    'removed': '❌',
    'renamed': '🔄',
    'copied': '📋',
    'changed': '📝',
    'unchanged': '📝',
}

HASHTAGS_PATTERNS = {
    # Существующие паттерны
    'web_tr': os.path.join(ROOT_TREE_DIR, 'web_tr'),
    'web_res': os.path.join(ROOT_TREE_DIR, 'web_res'),
    'web': os.path.join(ROOT_TREE_DIR, 'web'),
    'server': os.path.join(ROOT_TREE_DIR, 'server'),
    'test_server': os.path.join(ROOT_TREE_DIR, 'server', 'test'),
    'client': os.path.join(ROOT_TREE_DIR, 'client'),
    'ios': os.path.join(ROOT_TREE_DIR, 'client', 'ios-beta'),
    'macos': os.path.join(ROOT_TREE_DIR, 'client', 'macos-beta'),
    'android': os.path.join(ROOT_TREE_DIR, 'client', 'android-beta'),
    'android_dl': os.path.join(ROOT_TREE_DIR, 'client', 'android-stable-dl'),
    'mini_app': os.path.join(ROOT_TREE_DIR, 'mini_app'),
    'wallet': os.path.join(ROOT_TREE_DIR, 'mini_app', 'wallet'),
    
    # Новые категории для топиков
    'stable': [
        os.path.join(ROOT_TREE_DIR, 'client', 'android-stable-dl'),
        # добавьте другие стабильные версии
    ],
    'beta': [
        os.path.join(ROOT_TREE_DIR, 'client', 'android-beta'),
        os.path.join(ROOT_TREE_DIR, 'client', 'ios-beta'),
        os.path.join(ROOT_TREE_DIR, 'client', 'macos-beta'),
    ],
    'desktop': [
        # os.path.join(ROOT_TREE_DIR, 'client', 'macos-beta'),
        # # добавьте пути для desktop версий
    ],
    'translations': [
        os.path.join(ROOT_TREE_DIR, 'web_tr')
    ],
    'other': []
}

# order is important!
PATHS_TO_REMOVE_FROM_ALERT = [
    os.path.join(ROOT_TREE_DIR, 'web_tr'),
    os.path.join(ROOT_TREE_DIR, 'web_res'),
    os.path.join(ROOT_TREE_DIR, 'web'),
    os.path.join(ROOT_TREE_DIR, 'server'),
    os.path.join(ROOT_TREE_DIR, 'client'),
    os.path.join(ROOT_TREE_DIR, 'mini_app'),
]

FORUM_CHAT_ID = '-1003131892289'
HASHTAG_TO_TOPIC = {
    'stable': 26,
    'beta': 25, 
    'desktop': 7,
    'android': 6,
    'macos': 3,
    'ios': 4,
    'web': 17,
    'translations': 36,
    'server': 31,
    'wallet': 32,
    'other': 29
}

GITHUB_API_LIMIT_PER_HOUR = 5_000
COUNT_OF_RUNNING_WORKFLOW_AT_SAME_TIME = 5  # just random number ;d

ROW_PER_STATUS = 5

LAST_PAGE_NUMBER_REGEX = r'page=(\d+)>; rel="last"'


async def send_req_until_success(session: aiohttp.ClientSession, **kwargs) -> Tuple[dict, int]:
    delay = 5  # in sec
    count_of_retries = int(GITHUB_API_LIMIT_PER_HOUR / COUNT_OF_RUNNING_WORKFLOW_AT_SAME_TIME / delay)

    last_page_number = 1
    retry_number = 1
    while retry_number <= count_of_retries:
        retry_number += 1

        res = await session.get(**kwargs)
        if res.status != 200:
            await asyncio.sleep(delay)
            continue

        json = await res.json()

        pagination_data = res.headers.get('Link', '')
        matches = re.findall(LAST_PAGE_NUMBER_REGEX, pagination_data)
        if matches:
            last_page_number = int(matches[0])

        return json, last_page_number

    raise RuntimeError('Surprise. Time is over')


async def send_telegram_alert(session: aiohttp.ClientSession, text: str, thread_id: int) -> aiohttp.ClientResponse:
    params = {
        'chat_id': FORUM_CHAT_ID,
        'parse_mode': 'HTML',
        'text': text,
        'disable_web_page_preview': 1,
        'message_thread_id': thread_id
    }

    return await session.get(
        url=f'{BASE_TELEGRAM_API}{TELEGRAM_SEND_MESSAGE}'.format(token=TELEGRAM_BOT_TOKEN), params=params
    )

def get_file_topics(filename):
    """Определяет к каким топикам относится файл"""
    topics = set()
    
    # Проверяем все категории кроме 'other'
    for topic_name, patterns in HASHTAGS_PATTERNS.items():
        if topic_name == 'other':
            continue
            
        if isinstance(patterns, list):
            for pattern in patterns:
                if pattern and pattern in filename:
                    topics.add(topic_name)
        else:
            if patterns and patterns in filename:
                topics.add(topic_name)
    
    # Если файл не попал ни в одну категорию, добавляем в 'other'
    if not topics:
        topics.add('other')
        
    return topics

def build_topic_alert(files, commit_hash, html_url, topic_name):
    """Строит сообщение для конкретного топика"""
    topic_titles = {
        'stable': 'Telegram Stable Updates',
        'beta': 'Telegram Beta Updates', 
        'desktop': 'Desktop Updates',
        'android': 'Android Updates',
        'macos': 'macOS Updates',
        'ios': 'iOS & iPadOS Updates', 
        'web': 'Web Updates',
        'translations': 'Translations Updates',
        'server': 'Server Updates',
        'wallet': 'Wallet Updates',
        'other': 'Other Updates'  # Новая тема
    }
    
    title = topic_titles.get(topic_name, 'Telegram Updates')
    
    alert_text = f'<b>{title}</b>\n\n'
    
    changes = {k: [] for k in STATUS_TO_EMOJI.keys()}
    for file in files:
        changed_url = file['filename'].replace('.html', '')
        for path_to_remove in PATHS_TO_REMOVE_FROM_ALERT:
            if changed_url.startswith(path_to_remove):
                changed_url = changed_url[len(path_to_remove) + 1:]
                break

        status = STATUS_TO_EMOJI[file['status']]
        changes[file['status']].append(f'{status} <code>{changed_url}</code>')

    for status, text_list in changes.items():
        if not text_list:
            continue

        alert_text += '\n'.join(text_list[:ROW_PER_STATUS]) + '\n'
        if len(text_list) > ROW_PER_STATUS:
            count = len(text_list) - ROW_PER_STATUS
            alert_text += f'And <b>{count}</b> {status} actions more..\n'
        alert_text += '\n'

    link_text = f'GitHub · CHUKEPC/telegram-crawler-public@{commit_hash}'
    alert_text += f'<a href="{html_url}">{link_text}</a>'
    
    return alert_text

async def main() -> None:
    async with aiohttp.ClientSession() as session:
        commit_data, last_page = await send_req_until_success(
            session=session,
            url=f'{BASE_GITHUB_API}{GITHUB_LAST_COMMITS}'.format(repo=REPOSITORY, sha=COMMIT_SHA),
            headers={
                'Authorization': f'token {GITHUB_PAT}'
            }
        )
        commit_files = commit_data['files']

        coroutine_list = list()
        for current_page in range(2, last_page + 1):
            coroutine_list.append(send_req_until_success(
                session=session,
                url=f'{BASE_GITHUB_API}{GITHUB_LAST_COMMITS}?page={current_page}'.format(
                    repo=REPOSITORY, sha=COMMIT_SHA
                ),
                headers={
                    'Authorization': f'token {GITHUB_PAT}'
                }
            ))

        paginated_responses = await asyncio.gather(*coroutine_list)
        for json_response, _ in paginated_responses:
            commit_files.extend(json_response['files'])

        commit_files = [file for file in commit_files if 'translations.telegram.org/' not in file['filename']]
        if not commit_files:
            return

        commit_hash = commit_data['sha'][:7]
        html_url = commit_data['html_url']

        sent_to_topics = set()
        topic_files = {}

        # Группируем файлы по топикам
        for file in commit_files:
            file_topics = get_file_topics(file['filename'])
            for topic in file_topics:
                if topic not in topic_files:
                    topic_files[topic] = []
                topic_files[topic].append(file)

        # Отправляем в каждый релевантный топик
        for topic, files_in_topic in topic_files.items():
            if topic in HASHTAG_TO_TOPIC and files_in_topic:
                topic_alert_text = build_topic_alert(files_in_topic, commit_hash, html_url, topic)
                thread_id = HASHTAG_TO_TOPIC[topic]
                logger.info(f'Sending alert to topic: {topic} (ID: {thread_id})')
                telegram_response = await send_telegram_alert(session, topic_alert_text, thread_id)
                sent_to_topics.add(topic)
                logger.debug(await telegram_response.read())


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
