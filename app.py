import os, re, time, tempfile, threading, logging, json
from pathlib import Path
import requests
from flask import Flask, request, jsonify
import yt_dlp

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('youtube-telegram-bot')

BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
BASE_URL = (os.environ.get('RENDER_EXTERNAL_URL') or os.environ.get('PUBLIC_BASE_URL', '')).rstrip('/')
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')
YT_REFRESH_TOKEN = os.environ.get('YT_REFRESH_TOKEN', '')
MAX_PLAYLIST_ITEMS = int(os.environ.get('MAX_PLAYLIST_ITEMS', '20'))
MAX_DURATION = int(os.environ.get('MAX_DURATION_SECONDS', '1200'))
MAX_BYTES = int(os.environ.get('MAX_FILE_BYTES', str(49 * 1024 * 1024)))
YT_CLIENT_ID = os.environ.get('YT_CLIENT_ID', '861556708454-d6dlm3lh05idd8npek18k6be8ba3oc68.apps.googleusercontent.com')
YT_CLIENT_SECRET = os.environ.get('YT_CLIENT_SECRET', '')
YT_PLAYER_CLIENT = os.environ.get('YT_PLAYER_CLIENT', 'tv')
_token_cache = {'token': None, 'expires': 0}
TG = f'https://api.telegram.org/bot{BOT_TOKEN}'
app = Flask(__name__)
_webhook_ready = False

def ensure_webhook():
    global _webhook_ready
    if _webhook_ready or not BASE_URL:
        return
    payload = {'url': f'{BASE_URL}/telegram', 'allowed_updates': json.dumps(['message'])}
    if WEBHOOK_SECRET:
        payload['secret_token'] = WEBHOOK_SECRET
    r = requests.post(f'{TG}/setWebhook', data=payload, timeout=30)
    r.raise_for_status()
    _webhook_ready = True
    log.info('Telegram webhook set to %s/telegram', BASE_URL)

@app.before_request
def _ensure_webhook():
    ensure_webhook()

HELP = '''🎵 שלחו לי שם של שיר, קישור ל-YouTube או קישור לפלייליסט.

ברירת המחדל היא MP3.
לווידאו: כתבו /video ואז שם או קישור.
לבחירת איכות: /video360 או /video720 ואז שם או קישור.

דוגמאות:
עומר אדם שני משוגעים
/video https://youtu.be/...
/video720 נועה קירל פנתרה

בפלייליסט אשלח עד 20 פריטים, אחד אחרי השני.'''

def tg(method, **data):
    r = requests.post(f'{TG}/{method}', data=data, timeout=90)
    if not r.ok:
        raise RuntimeError(f'Telegram {method}: {r.status_code} {r.text[:300]}')
    return r.json()

def send_message(chat_id, text, **extra):
    return tg('sendMessage', chat_id=chat_id, text=text, **extra)

def safe_name(value):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', value).strip(' .')
    return value[:120] or 'YouTube'

def youtube_headers():
    if not YT_REFRESH_TOKEN or not YT_CLIENT_SECRET:
        return {}
    if _token_cache['token'] and time.time() < _token_cache['expires'] - 120:
        return {'Authorization': f"Bearer {_token_cache['token']}"}
    r = requests.post('https://www.youtube.com/o/oauth2/token', data={
        'client_id': YT_CLIENT_ID, 'client_secret': YT_CLIENT_SECRET,
        'grant_type': 'refresh_token', 'refresh_token': YT_REFRESH_TOKEN,
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    _token_cache['token'] = data['access_token']
    _token_cache['expires'] = time.time() + int(data.get('expires_in', 3600))
    return {'Authorization': f"Bearer {_token_cache['token']}"}

def opts_for(mode, outdir):
    common = {
        'outtmpl': str(Path(outdir) / '%(playlist_index&{} - |)s%(title).120s [%(id)s].%(ext)s'),
        'quiet': True, 'no_warnings': True,
        'noplaylist': False, 'playlistend': MAX_PLAYLIST_ITEMS,
        'max_filesize': MAX_BYTES,
        'match_filter': yt_dlp.utils.match_filter_func(f'duration <=? {MAX_DURATION}'),
        'retries': 3, 'fragment_retries': 3,
        'concurrent_fragment_downloads': 2,
        'remote_components': ['ejs:github'],
    }
    if mode == 'audio':
        common.update({
            'format': 'bestaudio[filesize<49M]/bestaudio/best[filesize<49M]',
            'postprocessors': [
                {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
                {'key': 'FFmpegMetadata', 'add_metadata': True},
            ],
        })
    else:
        height = '360' if mode == 'video360' else '720'
        common.update({
            'format': f'bestvideo[height<={height}][filesize<45M]+bestaudio[filesize<8M]/best[height<={height}][filesize<49M]/best[filesize<49M]',
            'merge_output_format': 'mp4',
        })
    if YT_REFRESH_TOKEN:
        # Reuse the OAuth session already authorized for Ariel's Yemot YouTube service.
        common['http_headers'] = youtube_headers()
        common['extractor_args'] = {'youtube': {'player_client': [YT_PLAYER_CLIENT]}}
    return common

def parse_request(text):
    text = (text or '').strip()
    mode = 'audio'
    for command, selected in [('/video720', 'video720'), ('/video360', 'video360'), ('/video', 'video720'), ('/audio', 'audio'), ('/mp3', 'audio')]:
        if text.lower().startswith(command):
            mode = selected
            text = text[len(command):].strip()
            break
    if not re.match(r'https?://', text):
        text = f'ytsearch1:{text}'
    return mode, text

def upload_file(chat_id, path, mode):
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_BYTES:
        send_message(chat_id, f'⚠️ הקובץ {path.name} גדול מדי לשליחה בטלגרם.')
        return
    method = 'sendAudio' if mode == 'audio' and path.suffix.lower() == '.mp3' else 'sendVideo'
    field = 'audio' if method == 'sendAudio' else 'video'
    with path.open('rb') as fh:
        r = requests.post(f'{TG}/{method}', data={'chat_id': chat_id, 'caption': path.stem[:900], 'supports_streaming': 'true'}, files={field: (path.name, fh)}, timeout=600)
    if not r.ok:
        raise RuntimeError(f'Telegram upload: {r.status_code} {r.text[:300]}')

def process(chat_id, text, status_id=None):
    try:
        mode, target = parse_request(text)
        with tempfile.TemporaryDirectory(prefix='ytbot-') as tmp:
            send_message(chat_id, '⏳ מחפש ומוריד... זה יכול לקחת דקה או שתיים.')
            with yt_dlp.YoutubeDL(opts_for(mode, tmp)) as ydl:
                ydl.download([target])
            files = sorted(p for p in Path(tmp).iterdir() if p.is_file() and p.suffix.lower() in {'.mp3', '.mp4', '.m4a', '.webm', '.mkv'})
            if not files:
                raise RuntimeError('לא נוצר קובץ מתאים. ייתכן שהסרטון ארוך מדי, חסום או גדול מדי.')
            for i, path in enumerate(files, 1):
                if len(files) > 1:
                    send_message(chat_id, f'📤 שולח {i}/{len(files)}: {path.stem[:80]}')
                upload_file(chat_id, path, mode)
            send_message(chat_id, '✅ מוכן. אפשר לשלוח עוד שם או קישור.')
    except yt_dlp.utils.DownloadError as e:
        log.exception('download failed')
        send_message(chat_id, '❌ לא הצלחתי להוריד את הסרטון. נסו קישור אחר, איכות נמוכה יותר או שם מדויק יותר.')
    except Exception as e:
        log.exception('job failed')
        send_message(chat_id, f'❌ משהו השתבש: {str(e)[:300]}')

@app.get('/')
def home():
    return jsonify(ok=True, service='youtube-telegram-bot')

@app.get('/health')
def health():
    return jsonify(ok=True, webhook=_webhook_ready)

@app.get('/bot-info')
def bot_info():
    r = requests.get(f'{TG}/getMe', timeout=30)
    data = r.json()
    result = data.get('result') or {}
    return jsonify(ok=data.get('ok', False), username=result.get('username'), first_name=result.get('first_name'))

@app.post('/telegram')
def telegram_webhook():
    if WEBHOOK_SECRET and request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
        return ('forbidden', 403)
    update = request.get_json(silent=True) or {}
    msg = update.get('message') or {}
    chat_id = (msg.get('chat') or {}).get('id')
    text = msg.get('text') or ''
    if not chat_id:
        return jsonify(ok=True)
    if text.startswith('/start') or text.startswith('/help') or text.startswith('/quality'):
        send_message(chat_id, HELP)
    elif not text:
        send_message(chat_id, 'שלחו שם של שיר או קישור ל-YouTube.')
    else:
        threading.Thread(target=process, args=(chat_id, text), daemon=True).start()
    return jsonify(ok=True)

@app.post('/setup-webhook')
def setup_webhook():
    auth = request.headers.get('Authorization', '')
    if not WEBHOOK_SECRET or auth != f'Bearer {WEBHOOK_SECRET}':
        return ('forbidden', 403)
    if not BASE_URL:
        return ('PUBLIC_BASE_URL missing', 400)
    result = tg('setWebhook', url=f'{BASE_URL}/telegram', secret_token=WEBHOOK_SECRET, allowed_updates=json.dumps(['message']))
    return jsonify(result)
