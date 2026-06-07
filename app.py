import os, sys, json, time, threading, shutil, zipfile, urllib.request, re
import hashlib, secrets
from datetime import timedelta
from flask import Flask, request, jsonify, send_file, render_template, session, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
FFMPEG_DIR = os.path.join(BASE_DIR, 'ffmpeg_bin')

def _resolve_ffmpeg_dir():
    import shutil
    # Windows 번들 바이너리 (Linux에서는 .exe 무시)
    bundled = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
    if os.path.exists(os.path.join(FFMPEG_DIR, bundled)):
        return FFMPEG_DIR
    # 시스템 ffmpeg
    ff = shutil.which('ffmpeg')
    return os.path.dirname(ff) if ff else None
DATA_DIR = os.path.join(BASE_DIR, 'data')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')

for d in [DOWNLOAD_DIR, FFMPEG_DIR, DATA_DIR]:
    os.makedirs(d, exist_ok=True)

job_store = {}
OAUTH2_TOKEN = os.path.expanduser('~/.cache/yt-dlp/youtube-oauth2.token.json')
oauth2_logs = []

def _oauth2_is_authorized():
    return os.path.exists(OAUTH2_TOKEN)

oauth2_state = {
    'status': 'authorized' if os.path.exists(OAUTH2_TOKEN) else 'idle',
    'auth_url': None, 'user_code': None, 'error': None
}

class _OAuth2Logger:
    def _check(self, msg):
        if not msg: return
        oauth2_logs.append(msg)
        if len(oauth2_logs) > 30: oauth2_logs.pop(0)
        print(f'[oauth2-log] {msg}')
        if 'google.com/device' in msg or 'accounts.google.com' in msg:
            url = re.search(r'https://[^\s\)"\']+', msg)
            if url: oauth2_state['auth_url'] = url.group().rstrip('.,')
            oauth2_state['status'] = 'waiting'
        # 코드 형식: ABCD-EFGH 또는 ABCDEFGH
        m = re.search(r'\b([A-Z0-9]{4}-[A-Z0-9]{4})\b', msg) or \
            re.search(r'code[:\s]+([A-Z0-9]{4}-[A-Z0-9]{4})', msg, re.I)
        if m: oauth2_state['user_code'] = m.group(1)
    def debug(self, msg): self._check(msg)
    def info(self, msg): self._check(msg)
    def warning(self, msg): self._check(msg)
    def error(self, msg):
        print(f'[oauth2-err] {msg}')
        if oauth2_state['status'] not in ('waiting', 'authorized'):
            oauth2_state['status'] = 'error'; oauth2_state['error'] = msg

def _run_oauth2_init():
    import yt_dlp
    oauth2_state.update({'status': 'starting', 'auth_url': None, 'user_code': None, 'error': None})
    try:
        with yt_dlp.YoutubeDL({
            'username': 'oauth2', 'password': '',
            'logger': _OAuth2Logger(), 'quiet': True,
        }) as ydl:
            try:
                ydl.extract_info('https://www.youtube.com/watch?v=jNQXAC9IVRw', download=False)
            except Exception:
                pass  # 인증 후 영상 접근 오류는 무시
        # 토큰 파일 생성 여부로 인증 성공 판단
        if _oauth2_is_authorized():
            oauth2_state['status'] = 'authorized'
        elif oauth2_state['status'] not in ('waiting',):
            oauth2_state['status'] = 'error'
            oauth2_state['error'] = '토큰 파일이 생성되지 않았습니다'
    except Exception as e:
        if oauth2_state['status'] not in ('waiting', 'authorized'):
            oauth2_state['status'] = 'error'
            oauth2_state['error'] = str(e)

# ── ffmpeg 자동 설치 ──────────────────────────────────────────
def setup_ffmpeg():
    exe = os.path.join(FFMPEG_DIR, 'ffmpeg.exe')
    probe = os.path.join(FFMPEG_DIR, 'ffprobe.exe')
    if os.path.exists(exe) and os.path.exists(probe):
        print('[ffmpeg] 준비됨')
        return True
    print('[ffmpeg] 다운로드 중... (최초 1회)')
    try:
        url = 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
        zp = os.path.join(FFMPEG_DIR, 'ffmpeg.zip')
        urllib.request.urlretrieve(url, zp)
        with zipfile.ZipFile(zp, 'r') as z:
            for m in z.namelist():
                fn = os.path.basename(m)
                if fn in ('ffmpeg.exe', 'ffprobe.exe'):
                    with z.open(m) as src, open(os.path.join(FFMPEG_DIR, fn), 'wb') as dst:
                        shutil.copyfileobj(src, dst)
        os.remove(zp)
        print('[ffmpeg] 설치 완료')
        return True
    except Exception as e:
        print(f'[ffmpeg] 실패: {e}')
        return False

setup_ffmpeg()

# ── 설정 ──────────────────────────────────────────────────────
def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                s = json.load(f)
            if s.get('anthropic_key'):
                os.environ['ANTHROPIC_API_KEY'] = s['anthropic_key']
            return s
    except: pass
    return {}

def save_settings(data):
    existing = {}
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                existing = json.load(f)
    except: pass
    existing.update(data)
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(existing, f)
    if data.get('anthropic_key'):
        os.environ['ANTHROPIC_API_KEY'] = data['anthropic_key']

load_settings()

# ── 인증 ─────────────────────────────────────────────────────
def _hash_pw(pw):
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

def _init_auth():
    s = load_settings()
    changed = {}
    if not s.get('secret_key'):
        changed['secret_key'] = secrets.token_hex(32)
    if not s.get('auth_user') or not s.get('auth_pass_hash'):
        default_pw = 'studiohub2024'
        changed['auth_user'] = 'admin'
        changed['auth_pass_hash'] = _hash_pw(default_pw)
        print(f'[AUTH] 기본 계정 생성: admin / {default_pw}  ← 설정에서 변경하세요')
    if changed:
        save_settings(changed)
        s.update(changed)
    return s

_auth_cfg = _init_auth()
app.secret_key = _auth_cfg['secret_key']
app.permanent_session_lifetime = timedelta(days=30)

@app.before_request
def require_login():
    pub = {'login', 'logout', 'static', 'yt_oauth2_status', 'yt_debug'}
    if request.endpoint in pub:
        return
    if not session.get('logged_in'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
        return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        user = request.form.get('username', '').strip()
        pw   = request.form.get('password', '')
        s = load_settings()
        if user == s.get('auth_user') and _hash_pw(pw) == s.get('auth_pass_hash', ''):
            session.permanent = True
            session['logged_in'] = True
            session['username'] = user
            return redirect('/')
        error = '아이디 또는 비밀번호가 올바르지 않습니다.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/api/settings/auth', methods=['POST'])
def api_change_auth():
    data = request.json or {}
    new_user = data.get('username', '').strip()
    new_pass = data.get('password', '').strip()
    if not new_user or not new_pass:
        return jsonify({'success': False, 'error': '아이디와 비밀번호를 입력하세요'}), 400
    save_settings({'auth_user': new_user, 'auth_pass_hash': _hash_pw(new_pass)})
    return jsonify({'success': True})

@app.route('/api/settings', methods=['POST'])
def api_settings():
    save_settings(request.json or {})
    return jsonify({'success': True})

@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                s = json.load(f)
            return jsonify({'has_key': bool(s.get('groq_key'))})
    except: pass
    return jsonify({'has_key': False})

# ── Groq API 헬퍼 (텍스트 생성) ───────────────────────────────
GROQ_MODEL = 'llama-3.3-70b-versatile'
GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'

def _groq_key():
    key = (load_settings() or {}).get('groq_key', '').strip()
    if not key:
        raise Exception('Groq API 키 미설정 — 설정에서 입력해주세요')
    return key

def call_groq(system, user, max_tokens=2000, output_schema=None, **_):
    key = _groq_key()
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    data = {
        'model': GROQ_MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'max_tokens': max_tokens,
    }
    if output_schema is not None:
        data['response_format'] = {'type': 'json_object'}
    waits = [8, 15, 25, 40]
    last_err = None
    for attempt in range(5):
        try:
            import requests as _req
            resp = _req.post(GROQ_API_URL, headers=headers, json=data, timeout=60)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            last_err = e
            err_str = str(e)
            is_daily = any(x in err_str.lower() for x in ['daily', 'per_day', 'perday'])
            is_temp = not is_daily and any(x in err_str for x in ['429', '500', '502', '503', 'timeout'])
            if is_temp and attempt < 4:
                wait = waits[attempt]
                print(f'[Groq] {attempt+1}/4 재시도, {wait}초 대기... ({err_str[:80]})')
                time.sleep(wait)
            else:
                raise
    raise last_err

# 하위 호환 별칭
call_claude = call_groq
call_gemini = call_groq

def call_groq_json(system, user, schema, max_tokens=2000, **_):
    # 배열 항목까지 포함한 최소 예시 힌트 — 타입 정보 없이 구조만 전달
    props = (schema or {}).get('properties', {})
    arr_key = next((k for k, v in props.items() if isinstance(v, dict) and v.get('type') == 'array'), None)
    if arr_key:
        item_props = props[arr_key].get('items', {}).get('properties', {})
        item_ex = {k: '...' for k in item_props} if item_props else {'item': '...'}
        fmt = json.dumps({arr_key: [item_ex]}, ensure_ascii=False)
    else:
        fmt = json.dumps({k: '...' for k in props}, ensure_ascii=False) if props else '{...}'
    system_json = (system
        + f'\n\nRespond with ONLY valid JSON in this format: {fmt}'
        + '\nNo markdown, no explanation, no extra text.')
    text = call_groq(system_json, user, max_tokens=max_tokens, output_schema=schema)
    return json.loads(text)

call_claude_json = call_groq_json
call_gemini_json = call_groq_json

# ── Gemini 오디오 분석용 헬퍼 (트랙 분석 전용) ──────────────────
def _gemini_audio_client():
    key = (load_settings() or {}).get('gemini_key', '').strip()
    if not key:
        raise Exception('Gemini API 키 미설정 — 트랙 분석 기능에 필요합니다')
    from google import genai as ggenai
    return ggenai.Client(api_key=key)

def _strip_additional_props(schema):
    if not isinstance(schema, dict):
        return schema
    result = {k: v for k, v in schema.items() if k != 'additionalProperties'}
    for k, v in result.items():
        if isinstance(v, dict):
            result[k] = _strip_additional_props(v)
        elif isinstance(v, list):
            result[k] = [_strip_additional_props(i) for i in v]
    return result

# ── 공통 JSON 파싱 ──────────────────────────────────────────
def extract_json(text):
    import json as _json
    clean = text.strip()

    # 1. ```json 블록 추출
    if '```' in clean:
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', clean)
        if m: clean = m.group(1).strip()

    # 2. 중괄호 균형으로 첫 JSON 객체만 추출
    start = clean.find('{')
    if start != -1:
        depth, end = 0, -1
        for i, c in enumerate(clean[start:], start):
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0: end = i; break
        if end != -1:
            clean = clean[start:end+1]

    # 3. 직접 파싱 시도
    try:
        return _json.loads(clean)
    except _json.JSONDecodeError:
        pass

    # 4. 줄바꿈/제어문자 정리 후 재시도
    # 문자열 값 안의 리터럴 줄바꿈을 \n으로 치환
    def fix_json_strings(s):
        result = []
        in_str = False
        i = 0
        while i < len(s):
            c = s[i]
            if c == '"' and (i == 0 or s[i-1] != '\\'):
                in_str = not in_str
                result.append(c)
            elif in_str and c == '\n':
                result.append('\\n')
            elif in_str and c == '\r':
                result.append('\\r')
            elif in_str and c == '\t':
                result.append('\\t')
            else:
                result.append(c)
            i += 1
        return ''.join(result)

    try:
        return _json.loads(fix_json_strings(clean))
    except _json.JSONDecodeError:
        pass

    # 5. 마지막 수단: ast.literal_eval 시도 후 실패시 빈 dict
    raise ValueError(f'JSON parse failed: {clean[:100]}')


# ── Google Trends ───────────────────────────────────────────
@app.route('/api/trends', methods=['POST'])
def trends():
    import requests as _req
    import urllib.parse
    import xml.etree.ElementTree as ET

    data = request.json
    keywords = data.get('keywords', [])[:5]
    geo = data.get('geo', 'KR')
    timeframe = data.get('timeframe', 'now 7-d')
    cat = int(data.get('cat', 0))
    want_interest = data.get('want_interest', True)
    want_related = data.get('want_related', True)
    want_region = data.get('want_region', True)
    want_realtime = data.get('want_realtime', False)

    if not keywords:
        return jsonify({'success':False, 'error':'키워드 필요'}), 400

    result = {'success': True}
    sess = _req.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120',
        'Accept-Language': 'ko-KR,ko;q=0.9',
    })

    def strip_prefix(text):
        if text.startswith(")]}'"):
            text = text[4:].lstrip('\n')
        return text

    def get_widgets():
        comps = [{"keyword": kw, "geo": geo, "time": timeframe} for kw in keywords]
        params = urllib.parse.urlencode({
            'hl': 'ko', 'tz': '-540',
            'req': json.dumps({"comparisonItem": comps, "category": cat, "property": ""}),
        })
        url = 'https://trends.google.com/trends/api/explore?' + params
        r = sess.get(url, timeout=25)
        r.raise_for_status()
        return json.loads(strip_prefix(r.text)).get('widgets', [])

    def req_widget(endpoint, widget):
        params = urllib.parse.urlencode({
            'hl': 'ko', 'tz': '-540',
            'req': json.dumps(widget.get('request', {})),
            'token': widget.get('token', ''),
        })
        url = f'https://trends.google.com/trends/api/widgetdata/{endpoint}?' + params
        r = sess.get(url, timeout=25)
        r.raise_for_status()
        return json.loads(strip_prefix(r.text))

    try:
        # 실시간 트렌드
        if want_realtime:
            try:
                r = sess.get(
                    f'https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo or "KR"}',
                    timeout=15
                )
                root = ET.fromstring(r.text)
                items = []
                for item in root.findall('.//item')[:20]:
                    title = item.findtext('title', '')
                    traffic = item.findtext(
                        '{https://trends.google.com/trends/trendingsearches/daily}approx_traffic', ''
                    )
                    if title:
                        items.append({'title': title, 'traffic': traffic})
                result['realtime'] = items
            except Exception as e:
                result['realtime_error'] = str(e)

        if want_interest or want_related or want_region:
            widgets = get_widgets()
            print(f'[trends] widgets: {[w.get("title") for w in widgets]}')

            w_map = {}
            for w in widgets:
                t = w.get('title', '').lower()
                wid = w.get('id', '').lower()
                w_map[wid] = w
                w_map[t] = w

            # 관심도 추이
            if want_interest:
                try:
                    wkey = next((k for k in w_map if 'timeseries' in k or 'interest over time' in k), None)
                    if wkey:
                        d = req_widget('multiline', w_map[wkey])
                        rows = d.get('default', {}).get('timelineData', [])
                        dates = [r.get('formattedAxisTime', '') for r in rows]
                        values = {kw: [] for kw in keywords}
                        for row in rows:
                            vals = row.get('value', [])
                            for i, kw in enumerate(keywords):
                                values[kw].append(vals[i] if i < len(vals) else 0)
                        result['interest'] = {'dates': dates, 'values': values}
                    else:
                        result['interest_error'] = '위젯 없음'
                except Exception as e:
                    result['interest_error'] = str(e)

            # 연관 검색어
            if want_related:
                try:
                    wkey = next((k for k in w_map if 'related_queries' in k or 'relatedsearches' in k or 'related queries' in k), None)
                    if wkey:
                        d = req_widget('relatedsearches', w_map[wkey])
                        ranked = d.get('default', {}).get('rankedList', [])
                        top_list = ranked[0].get('rankedKeyword', []) if len(ranked) > 0 else []
                        rising_list = ranked[1].get('rankedKeyword', []) if len(ranked) > 1 else []
                        # 키워드별로 묶기
                        related = {}
                        for kw in keywords:
                            related[kw] = {
                                'top': [{'query': i.get('query',''), 'value': str(i.get('value',''))} for i in top_list[:8]],
                                'rising': [{'query': i.get('query',''), 'value': str(i.get('value',''))} for i in rising_list[:8]],
                            }
                        result['related'] = related
                    else:
                        result['related_error'] = '위젯 없음'
                except Exception as e:
                    result['related_error'] = str(e)

            # 지역별 인기도
            if want_region:
                try:
                    wkey = next((k for k in w_map if 'geo_chart' in k or 'interest by region' in k or 'geomap' in k), None)
                    if wkey:
                        d = req_widget('comparedgeo', w_map[wkey])
                        rows = d.get('default', {}).get('geoMapData', [])
                        region_result = {kw: [] for kw in keywords}
                        for row in rows:
                            name = row.get('geoName', '')
                            vals = row.get('value', [])
                            for i, kw in enumerate(keywords):
                                v = vals[i] if i < len(vals) else 0
                                if v > 0:
                                    region_result[kw].append({'geoName': name, 'region': name, 'value': v})
                        for kw in keywords:
                            region_result[kw] = sorted(region_result[kw], key=lambda x: x['value'], reverse=True)[:10]
                        result['region'] = {k: v for k, v in region_result.items() if v}
                    else:
                        result['region_error'] = '위젯 없음'
                except Exception as e:
                    result['region_error'] = str(e)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f'[trends ERROR]\n{tb}')
        return jsonify({'success': False, 'error': f'Google Trends 오류: {str(e)}\n{tb}'}), 500

    return jsonify(result)





# ── 트랙 분석 ────────────────────────────────────────────────
@app.route('/api/analyze/track', methods=['POST'])
def analyze_track():
    import tempfile, os, base64

    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '파일 없음'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'error': '파일명 없음'}), 400

    result = {'success': True, 'meta': {}, 'audio': {}, 'analysis': {}}

    suffix = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        # 1. 메타데이터 추출
        try:
            from mutagen import File as MutagenFile
            audio_file = MutagenFile(tmp_path, easy=True)
            if audio_file:
                tags = audio_file.tags or {}
                result['meta'] = {
                    'title':  str(tags.get('title',  [''])[0]) if tags.get('title')  else '',
                    'artist': str(tags.get('artist', [''])[0]) if tags.get('artist') else '',
                    'genre':  str(tags.get('genre',  [''])[0]) if tags.get('genre')  else '',
                    'bpm':    str(tags.get('bpm',    [''])[0]) if tags.get('bpm')    else '',
                }
                if hasattr(audio_file.info, 'length'):
                    result['meta']['duration'] = round(audio_file.info.length)
        except Exception as e:
            result['meta_error'] = str(e)

        # 2. librosa BPM/키 분석
        try:
            import librosa, numpy as np
            y, sr = librosa.load(tmp_path, duration=60, mono=True)
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            result['audio']['bpm'] = round(float(tempo), 1)
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
            keys = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
            result['audio']['key'] = keys[int(np.argmax(chroma.mean(axis=1)))]
            result['audio']['energy'] = round(float(np.mean(librosa.feature.rms(y=y))) * 1000, 2)
        except Exception as e:
            result['audio']['librosa_error'] = str(e)

        # 3. Gemini로 오디오 직접 분석
        bpm = result['audio'].get('bpm') or result['meta'].get('bpm', '')
        key = result['audio'].get('key') or ''
        title = result['meta'].get('title', os.path.splitext(file.filename)[0])
        artist = result['meta'].get('artist', '')
        genre_tag = result['meta'].get('genre', '')
        energy = result['audio'].get('energy', 0)

        # 실제 옵션 값 목록
        BPM_OPTIONS = [
            "slow hip-hop (70-85 BPM)",
            "mid-tempo hip-hop (86-100 BPM)",
            "standard hip-hop (101-115 BPM)",
            "fast hip-hop (116-130 BPM)",
            "trap tempo (130-145 BPM)",
            "drill tempo (140-150 BPM)",
        ]
        LYRIC_STYLE_OPTIONS = [
            "straight rap — steady consistent tempo bar 1 to end, no melodic singing, pure rhythmic flow locked to BPM",
            "boom bap — classic 4/4 bars, punchy on 2 and 4, gritty East Coast delivery",
            "conscious rap — storytelling bars, poetic imagery, spoken word, thoughtful message-driven",
            "jazz rap — laid-back swinging flow, jazzy cadence, sophisticated wordplay",
            "trap flow — triplet hi-hat cadence, ad-libs scattered throughout, sliding pitch variations",
            "drill flow — dark aggressive, sliding melodic inflections on every bar, menacing tone",
            "UK drill flow — distinctive British cadence, staccato delivery, dark minor key feel",
            "phonk rap — dark memphis-style, slow menacing delivery, heavy 808 emphasis, eerie",
            "rage rap — chaotic frantic energy, screamed sections, aggressive unpredictable flow",
            "melodic rap — emotional sung hooks and chorus, rap verses, heartfelt bridge",
            "emo rap — vulnerable emotional delivery, confessional lyrics, blending singing and rapping",
            "cloud rap — hazy dreamy delivery, atmospheric whispered tone, slow drifting flow",
            "lo-fi rap — relaxed conversational delivery, loose timing, intimate feel",
            "double-time rap — twice the syllable density, rapid-fire delivery at double tempo",
            "aggressive rap — hard-hitting explosive bars, relentless non-stop energy throughout",
            "chopper style — extremely fast technical delivery, maximum syllable density",
            "mumble rap — melody-focused hazy delivery, words blend together, vibe over clarity",
            "gangsta rap — street narrative, hard-nosed delivery, West Coast influenced flow",
        ]
        INSTRUMENT_OPTIONS = [
            "boom bap drums (vinyl samples, punchy kick and snare, classic hip-hop groove)",
            "jazz rap (live jazz samples, upright bass, brushed snare, sophisticated)",
            "soul samples (chopped soul vocals, warm organ, funky groove drums)",
            "funk-influenced hip-hop (live bass, guitar licks, drum breaks, groovy)",
            "gospel hip-hop (choir samples, organ stabs, uplifting drums)",
            "trap 808 bass (rolling hi-hats, deep 808 sub-bass, snappy snare)",
            "drill beats (dark minor key melody, sliding 808 bass, aggressive ominous drums)",
            "UK drill (dark orchestral stabs, rolling hi-hats, heavy 808 UK style)",
            "minimal trap (sparse hi-hats, deep sub-bass, lots of empty space)",
            "rage beat (distorted 808, chaotic dark synth, explosive energy)",
            "phonk (memphis choir stabs, heavy distorted 808, eerie slow atmosphere)",
            "lo-fi hip-hop (dusty jazz samples, mellow drums, vinyl crackle, cozy)",
            "cloud rap beats (dreamy synths, reverb-drenched samples, hazy atmosphere)",
            "emo rap production (guitar-based, emotional piano, atmospheric pads)",
            "dark ambient hip-hop (atmospheric textures, minor key pads, eerie samples)",
            "orchestral hip-hop (strings, brass, cinematic grand scale production)",
            "afrobeats hip-hop fusion (afro percussion, melodic synths, bouncy groove)",
            "alternative hip-hop (experimental sounds, unconventional structure, art-focused)",
        ]
        SOUND_OPTIONS = [
            "dry close-mic, no reverb, raw intimate punchy sound",
            "heavy reverb, spacious atmospheric room sound",
            "lo-fi warm vinyl crackle, dusty nostalgic texture",
            "hazy dreamy washed-out atmospheric, cloud-like",
            "crisp clear polished radio-ready mix",
            "dark and gloomy, minor key, ominous heavy atmosphere",
            "bright energetic punchy, club-ready high-energy",
            "melancholic emotional, bittersweet mood, minor key warmth",
            "aggressive hard industrial, dark mechanical energy",
            "eerie unsettling, creepy phonk-like atmosphere",
            "uplifting anthemic, triumphant feel-good energy",
            "trap-style heavy 808 bass dominating the mix",
            "rage chaotic distorted overdriven heavy production",
            "cinematic orchestral epic grand scale production",
            "smooth R&B-influenced warm polished glossy production",
            "minimalist sparse production, space and silence emphasized",
            "vintage golden era hip-hop production aesthetic",
        ]
        DENSITY_OPTIONS = [
            "sparse (few words per bar, breathing room, spaced delivery)",
            "standard bar density (typical hip-hop word count per bar)",
            "dense (many words per bar, fast delivery)",
            "multisyllabic (complex internal rhymes, rapid-fire syllables)",
        ]
        VOCAL_TONE_OPTIONS = [
            "deep / bass-heavy", "warm baritone", "chest voice / resonant",
            "gravelly / gritty", "raspy / husky", "aggressive / hard-hitting",
            "dark / menacing", "intense / desperate", "commanding / dominant",
            "cold / detached", "smooth / velvety", "melodic / singing-inflected",
            "emotional / vulnerable", "soulful / R&B-influenced", "whispery / intimate",
            "nasal / NY-style", "airy / breathless", "high-pitched / piercing",
            "monotone / deadpan", "laid-back / slurred", "energetic / hype",
            "southern drawl", "british / UK-accent", "auto-tune heavy",
            "auto-tune subtle / light", "vocoder / robotic", "distorted / overdriven",
            "pitched down / chopped", "reverb-drenched / washed", "double-tracked / layered",
        ]
        GENRE_OPTIONS = [
            'boom bap','trap','drill','UK drill','phonk','cloud rap','rage rap',
            'conscious rap','gangsta rap','old school hip hop','east coast hip hop',
            'west coast hip hop','southern hip hop','mumble rap','emo rap','melodic rap',
            'chopper','lo-fi hip hop','jazz rap','soul rap','abstract hip hop',
            'alternative hip hop','crunk','jersey club','hyper pop',
            'R&B','neo soul','soul','funk','afrobeats','afro trap','grime','reggaeton','pop rap',
        ]
        MOOD_OPTIONS = [
            'dark','aggressive','menacing','cold','ominous','gritty','raw','intense',
            'desperate','chaotic','ruthless','melancholic','emotional','sad','lonely',
            'heartbroken','nostalgic','bittersweet','vulnerable','reflective','somber',
            'energetic','hype','explosive','triumphant','confident','powerful','uplifting',
            'anthemic','motivational','defiant','dreamy','hazy','mysterious','atmospheric',
            'hypnotic','ethereal','chill','laid-back','peaceful','warm','smooth',
            'romantic','passionate','moody','tense','hopeful','sweet',
        ]

        # Gemini로 오디오 파일 직접 분석
        try:
            import mimetypes
            from google.genai import types as gtypes
            client = _gemini_audio_client()

            with open(tmp_path, 'rb') as af:
                audio_data = af.read()

            mime_type = mimetypes.guess_type(tmp_path)[0] or 'audio/mpeg'

            BPM_OPTIONS = ["slow hip-hop (70-85 BPM)","mid-tempo hip-hop (86-100 BPM)","standard hip-hop (101-115 BPM)","fast hip-hop (116-130 BPM)","trap tempo (130-145 BPM)","drill tempo (140-150 BPM)"]
            LYRIC_STYLE_OPTIONS = ["straight rap — steady consistent tempo bar 1 to end, no melodic singing, pure rhythmic flow locked to BPM","boom bap — classic 4/4 bars, punchy on 2 and 4, gritty East Coast delivery","conscious rap — storytelling bars, poetic imagery, spoken word, thoughtful message-driven","jazz rap — laid-back swinging flow, jazzy cadence, sophisticated wordplay","trap flow — triplet hi-hat cadence, ad-libs scattered throughout, sliding pitch variations","drill flow — dark aggressive, sliding melodic inflections on every bar, menacing tone","UK drill flow — distinctive British cadence, staccato delivery, dark minor key feel","phonk rap — dark memphis-style, slow menacing delivery, heavy 808 emphasis, eerie","rage rap — chaotic frantic energy, screamed sections, aggressive unpredictable flow","melodic rap — emotional sung hooks and chorus, rap verses, heartfelt bridge","emo rap — vulnerable emotional delivery, confessional lyrics, blending singing and rapping","cloud rap — hazy dreamy delivery, atmospheric whispered tone, slow drifting flow","lo-fi rap — relaxed conversational delivery, loose timing, intimate feel","double-time rap — twice the syllable density, rapid-fire delivery at double tempo","aggressive rap — hard-hitting explosive bars, relentless non-stop energy throughout","chopper style — extremely fast technical delivery, maximum syllable density","mumble rap — melody-focused hazy delivery, words blend together, vibe over clarity","gangsta rap — street narrative, hard-nosed delivery, West Coast influenced flow"]
            INSTRUMENT_OPTIONS = ["boom bap drums (vinyl samples, punchy kick and snare, classic hip-hop groove)","jazz rap (live jazz samples, upright bass, brushed snare, sophisticated)","soul samples (chopped soul vocals, warm organ, funky groove drums)","funk-influenced hip-hop (live bass, guitar licks, drum breaks, groovy)","gospel hip-hop (choir samples, organ stabs, uplifting drums)","trap 808 bass (rolling hi-hats, deep 808 sub-bass, snappy snare)","drill beats (dark minor key melody, sliding 808 bass, aggressive ominous drums)","UK drill (dark orchestral stabs, rolling hi-hats, heavy 808 UK style)","minimal trap (sparse hi-hats, deep sub-bass, lots of empty space)","rage beat (distorted 808, chaotic dark synth, explosive energy)","phonk (memphis choir stabs, heavy distorted 808, eerie slow atmosphere)","lo-fi hip-hop (dusty jazz samples, mellow drums, vinyl crackle, cozy)","cloud rap beats (dreamy synths, reverb-drenched samples, hazy atmosphere)","emo rap production (guitar-based, emotional piano, atmospheric pads)","dark ambient hip-hop (atmospheric textures, minor key pads, eerie samples)","orchestral hip-hop (strings, brass, cinematic grand scale production)","afrobeats hip-hop fusion (afro percussion, melodic synths, bouncy groove)","alternative hip-hop (experimental sounds, unconventional structure, art-focused)"]
            SOUND_OPTIONS = ["dry close-mic, no reverb, raw intimate punchy sound","heavy reverb, spacious atmospheric room sound","lo-fi warm vinyl crackle, dusty nostalgic texture","hazy dreamy washed-out atmospheric, cloud-like","crisp clear polished radio-ready mix","dark and gloomy, minor key, ominous heavy atmosphere","bright energetic punchy, club-ready high-energy","melancholic emotional, bittersweet mood, minor key warmth","aggressive hard industrial, dark mechanical energy","eerie unsettling, creepy phonk-like atmosphere","uplifting anthemic, triumphant feel-good energy","trap-style heavy 808 bass dominating the mix","rage chaotic distorted overdriven heavy production","cinematic orchestral epic grand scale production","smooth R&B-influenced warm polished glossy production","minimalist sparse production, space and silence emphasized","vintage golden era hip-hop production aesthetic"]
            VOCAL_TONE_OPTIONS = ["deep / bass-heavy","warm baritone","chest voice / resonant","gravelly / gritty","raspy / husky","aggressive / hard-hitting","dark / menacing","intense / desperate","commanding / dominant","cold / detached","smooth / velvety","melodic / singing-inflected","emotional / vulnerable","soulful / R&B-influenced","whispery / intimate","nasal / NY-style","airy / breathless","high-pitched / piercing","monotone / deadpan","laid-back / slurred","energetic / hype","southern drawl","british / UK-accent","auto-tune heavy","auto-tune subtle / light","vocoder / robotic","distorted / overdriven","pitched down / chopped","reverb-drenched / washed","double-tracked / layered"]
            GENRE_OPTIONS = ["boom bap","trap","drill","UK drill","phonk","cloud rap","rage rap","conscious rap","gangsta rap","old school hip hop","east coast hip hop","west coast hip hop","southern hip hop","mumble rap","emo rap","melodic rap","chopper","lo-fi hip hop","jazz rap","soul rap","R&B","neo soul","soul","funk","afrobeats","afro trap","grime","reggaeton","pop rap","K-hip hop"]
            MOOD_OPTIONS = ["dark","aggressive","menacing","cold","ominous","gritty","raw","intense","desperate","chaotic","ruthless","melancholic","emotional","sad","lonely","heartbroken","nostalgic","bittersweet","vulnerable","reflective","somber","energetic","hype","explosive","triumphant","confident","powerful","uplifting","anthemic","motivational","defiant","dreamy","hazy","mysterious","atmospheric","hypnotic","ethereal","chill","laid-back","peaceful","warm","smooth","romantic","passionate","moody","tense","hopeful","sweet"]
            VOCALIST_OPTIONS = ["male rapper, aggressive hard-hitting delivery","male rapper, smooth laid-back delivery","male rapper, fast technical rapid-fire","male rapper, deep baritone authoritative","male rapper, raspy gritty voice","female rapper, assertive dominant","female rapper, melodic rap hybrid","female rapper, fast aggressive","rapper who self-sings hooks with auto-tune","mumble rapper, melodic hazy delivery"]

            prompt_text = (
                "이 오디오를 직접 들으면서 분석해주세요.\n\n"
                f"곡 정보: 제목={title}, 아티스트={artist}, 태그BPM={bpm}, 태그키={key}\n\n"
                "오디오를 실제로 들으면서 파악해주세요:\n"
                "1. 실제 BPM과 템포감\n"
                "2. 보컬 구성 (래퍼만인지 싱어와 함께인지, 성별, 훅 구조)\n"
                "3. 실제 사용된 악기와 프로덕션 스타일\n"
                "4. 전체적인 분위기와 에너지\n\n"
                "아래 목록에서 정확한 값을 골라 JSON만 반환하세요 (마크다운 없이):\n\n"
                f"BPM_OPTIONS: {BPM_OPTIONS}\n"
                f"LYRIC_STYLE_OPTIONS: {LYRIC_STYLE_OPTIONS}\n"
                f"INSTRUMENT_OPTIONS: {INSTRUMENT_OPTIONS}\n"
                f"SOUND_OPTIONS: {SOUND_OPTIONS}\n"
                f"VOCAL_TONE_OPTIONS: {VOCAL_TONE_OPTIONS}\n"
                f"GENRE_OPTIONS: {GENRE_OPTIONS}\n"
                f"MOOD_OPTIONS: {MOOD_OPTIONS}\n"
                f"VOCALIST_OPTIONS: {VOCALIST_OPTIONS}\n\n"
                '{"genre_chips":["GENRE_OPTIONS에서 2-4개"],'
                '"mood_chips":["MOOD_OPTIONS에서 2-4개"],'
                '"bpm_option":"BPM_OPTIONS 중 하나",'
                '"lyric_style":"LYRIC_STYLE_OPTIONS 중 하나",'
                '"instrument":"INSTRUMENT_OPTIONS 중 하나",'
                '"vocal_tone_chips":["VOCAL_TONE_OPTIONS에서 2-3개"],'
                '"sound":"SOUND_OPTIONS 중 하나",'
                '"lyric_density":"sparse/standard/dense/multisyllabic 중 하나",'
                '"vocal_type":"single 또는 dual",'
                '"vocalist":"VOCALIST_OPTIONS 중 하나",'
                '"theme_suggestion":"어울리는 테마 한국어",'
                '"analysis_summary":"실제 오디오 기반 분석 3-4문장 한국어"}'
            )

            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(
                    client.models.generate_content,
                    model='gemini-2.5-flash',
                    contents=[
                        gtypes.Part(inline_data=gtypes.Blob(mime_type=mime_type, data=audio_data)),
                        prompt_text,
                    ]
                )
                try:
                    response = _fut.result(timeout=30)
                except _cf.TimeoutError:
                    raise Exception('Gemini 분석 시간 초과 (30s) — librosa 결과만 사용됩니다')
            print(f'[Gemini 응답] {response.text[:500]}')
            analysis = extract_json(response.text)
            print(f'[Gemini 파싱] {analysis}')
            if analysis:
                result['analysis'] = analysis
                result['gemini_used'] = True
            else:
                result['gemini_raw'] = response.text[:1000]

        except ImportError as e:
            print(f'[Gemini ImportError] {e}')
            result['gemini_error'] = f'google-generativeai 미설치: {e}'
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f'[Gemini ERROR]\n{tb}')
            result['gemini_error'] = str(e)

        if result.get('analysis'):
            pass  # already set

    except Exception as e:
        import traceback
        result['success'] = False
        result['error'] = str(e)
        print(f'[analyze_track ERROR]\n{traceback.format_exc()}')
    finally:
        try:
            os.unlink(tmp_path)
        except:
            pass

    return jsonify(result)


# ── 채널 분석 ────────────────────────────────────────────────
@app.route('/api/channel/analyze', methods=['POST'])
def channel_analyze():
    import re
    data = request.json
    my_channel = data.get('my_channel', '').strip()
    similar_channels = data.get('similar_channels', [])  # 직접 입력
    auto_search = data.get('auto_search', True)  # 자동 서칭
    keywords = data.get('keywords', '')  # 자동 서칭 키워드

    s = load_settings() or {}
    yt_key = s.get('yt_api_key', '')
    if not yt_key:
        return jsonify({'success': False, 'error': 'YouTube API 키가 없습니다. 설정에서 입력해주세요.'}), 400
    if not my_channel:
        return jsonify({'success': False, 'error': '내 채널 ID/URL을 입력해주세요.'}), 400

    import requests as req

    def extract_channel_id(text):
        """URL에서 채널 ID 추출"""
        patterns = [
            r'channel/([UC][\w-]{22})',
            r'@([\w.-]+)',
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                val = m.group(1)
                if val.startswith('UC'):
                    return val, None
                return None, val
        if text.startswith('UC') and len(text) == 24:
            return text, None
        return None, text.lstrip('@')

    def get_channel_id_from_handle(handle, api_key):
        r = req.get('https://www.googleapis.com/youtube/v3/search', params={
            'part': 'snippet', 'q': handle, 'type': 'channel',
            'maxResults': 1, 'key': api_key
        })
        items = r.json().get('items', [])
        if items:
            return items[0]['snippet']['channelId']
        return None

    def get_channel_info(channel_id, api_key):
        """채널 기본 정보"""
        r = req.get('https://www.googleapis.com/youtube/v3/channels', params={
            'part': 'snippet,statistics,brandingSettings',
            'id': channel_id, 'key': api_key
        })
        items = r.json().get('items', [])
        if not items: return None
        item = items[0]
        stats = item.get('statistics', {})
        snippet = item.get('snippet', {})
        return {
            'id': channel_id,
            'title': snippet.get('title', ''),
            'description': snippet.get('description', '')[:500],
            'subscribers': stats.get('subscriberCount', '0'),
            'total_views': stats.get('viewCount', '0'),
            'video_count': stats.get('videoCount', '0'),
            'country': snippet.get('country', ''),
            'published_at': snippet.get('publishedAt', ''),
        }

    def get_channel_videos(channel_id, api_key, max_results=30):
        """채널 영상 목록"""
        # uploads playlist ID 가져오기
        r = req.get('https://www.googleapis.com/youtube/v3/channels', params={
            'part': 'contentDetails', 'id': channel_id, 'key': api_key
        })
        items = r.json().get('items', [])
        if not items: return []
        uploads_id = items[0]['contentDetails']['relatedPlaylists']['uploads']

        # 영상 목록
        videos = []
        next_token = None
        while len(videos) < max_results:
            params = {
                'part': 'snippet,contentDetails',
                'playlistId': uploads_id,
                'maxResults': min(50, max_results - len(videos)),
                'key': api_key
            }
            if next_token:
                params['pageToken'] = next_token
            r = req.get('https://www.googleapis.com/youtube/v3/playlistItems', params=params)
            data = r.json()
            items = data.get('items', [])
            if not items: break
            video_ids = [i['contentDetails']['videoId'] for i in items]

            # 영상 상세 정보
            vr = req.get('https://www.googleapis.com/youtube/v3/videos', params={
                'part': 'statistics,snippet,contentDetails',
                'id': ','.join(video_ids),
                'key': api_key
            })
            for v in vr.json().get('items', []):
                stats = v.get('statistics', {})
                snippet = v.get('snippet', {})
                videos.append({
                    'id': v['id'],
                    'title': snippet.get('title', ''),
                    'published_at': snippet.get('publishedAt', ''),
                    'views': int(stats.get('viewCount', 0)),
                    'likes': int(stats.get('likeCount', 0)),
                    'comments': int(stats.get('commentCount', 0)),
                    'duration': v.get('contentDetails', {}).get('duration', ''),
                    'tags': snippet.get('tags', [])[:10],
                    'description_len': len(snippet.get('description', '')),
                })
            next_token = data.get('nextPageToken')
            if not next_token: break
        return videos

    def search_similar_channels(keywords, api_key, max_results=5):
        """키워드로 유사 채널 자동 서칭"""
        r = req.get('https://www.googleapis.com/youtube/v3/search', params={
            'part': 'snippet', 'q': keywords, 'type': 'channel',
            'maxResults': max_results, 'key': api_key,
            'order': 'relevance'
        })
        channels = []
        for item in r.json().get('items', []):
            channels.append({
                'id': item['snippet']['channelId'],
                'title': item['snippet']['title'],
            })
        return channels

    result = {'success': True}

    try:
        # 내 채널 ID 파싱
        ch_id, handle = extract_channel_id(my_channel)
        if not ch_id and handle:
            ch_id = get_channel_id_from_handle(handle, yt_key)
        if not ch_id:
            return jsonify({'success': False, 'error': '채널을 찾을 수 없습니다.'}), 400

        # 내 채널 데이터 수집
        my_info = get_channel_info(ch_id, yt_key)
        my_videos = get_channel_videos(ch_id, yt_key, max_results=30)
        result['my_channel'] = {'info': my_info, 'videos': my_videos}

        # 유사 채널 수집
        similar_data = []

        # 직접 입력한 채널들
        for ch_url in similar_channels:
            ch_url = ch_url.strip()
            if not ch_url: continue
            s_id, s_handle = extract_channel_id(ch_url)
            if not s_id and s_handle:
                s_id = get_channel_id_from_handle(s_handle, yt_key)
            if s_id:
                s_info = get_channel_info(s_id, yt_key)
                s_videos = get_channel_videos(s_id, yt_key, max_results=20)
                if s_info:
                    similar_data.append({'info': s_info, 'videos': s_videos})

        # 자동 서칭
        if auto_search and keywords:
            auto_channels = search_similar_channels(keywords, yt_key, max_results=3)
            for ch in auto_channels:
                if ch['id'] == ch_id: continue  # 내 채널 제외
                if any(d['info']['id'] == ch['id'] for d in similar_data): continue
                s_info = get_channel_info(ch['id'], yt_key)
                s_videos = get_channel_videos(ch['id'], yt_key, max_results=20)
                if s_info:
                    similar_data.append({'info': s_info, 'videos': s_videos})

        result['similar_channels'] = similar_data

        # Claude로 분석 및 피드백
        def summarize_videos(videos):
            if not videos: return "영상 없음"
            avg_views = sum(v['views'] for v in videos) / len(videos)
            avg_likes = sum(v['likes'] for v in videos) / len(videos)
            avg_comments = sum(v['comments'] for v in videos) / len(videos)
            top3 = sorted(videos, key=lambda x: x['views'], reverse=True)[:3]
            return {
                'total': len(videos),
                'avg_views': round(avg_views),
                'avg_likes': round(avg_likes),
                'avg_comments': round(avg_comments),
                'top3': [{'title': v['title'], 'views': v['views']} for v in top3],
                'recent5': [{'title': v['title'], 'views': v['views'], 'published': v['published_at'][:10]} for v in videos[:5]],
            }

        analysis_data = {
            'my': {
                'info': my_info,
                'video_summary': summarize_videos(my_videos)
            },
            'similar': [
                {
                    'info': d['info'],
                    'video_summary': summarize_videos(d['videos'])
                }
                for d in similar_data
            ]
        }

        feedback_prompt = f"""아래 YouTube 채널 데이터를 분석해서 채널 운영 피드백을 제공해주세요.

내 채널:
{json.dumps(analysis_data['my'], ensure_ascii=False, indent=2)}

유사/경쟁 채널들:
{json.dumps(analysis_data['similar'], ensure_ascii=False, indent=2)}

아래 항목들에 대해 한국어로 상세히 분석해주세요:

1. **내 채널 현황 분석** — 조회수, 참여율, 성장세 평가
2. **유사 채널과 비교** — 어떤 점이 다른지, 잘 하는 점과 부족한 점
3. **인기 영상 패턴** — 조회수 높은 영상들의 공통점
4. **업로드 전략** — 주제, 빈도, 제목 스타일 등
5. **즉시 적용할 개선점** — 구체적인 실행 가능한 팁 5가지
6. **중장기 운영 방향** — 채널 성장을 위한 전략"""

        feedback = call_claude(
            '당신은 YouTube 채널 분석 전문가입니다. 데이터 기반으로 구체적인 피드백을 제공합니다.',
            feedback_prompt,
            max_tokens=3000
        )
        result['feedback'] = feedback

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify(result)

# ── 공통 Suno 태그 생성 ────────────────────────────────────────
def build_suno_tags(genre='', mood='', theme='', bpm='', length='',
                    lyric_style='', instrument='', vocal='', sound='', structure=''):
    parts = []
    if genre: parts.append(f'Genre: {genre}')
    if mood: parts.append(f'Mood/Feel: {mood}')
    if theme: parts.append(f'Theme: {theme}')
    if bpm: parts.append(f'BPM/Tempo: {bpm}')
    if length: parts.append(f'Song length: {length}')
    if lyric_style: parts.append(f'Lyric/section style: {lyric_style}')
    if instrument: parts.append(f'Main instrument: {instrument}')
    if vocal: parts.append(f'Vocal style: {vocal}')
    if sound: parts.append(f'Sound/Atmosphere: {sound}')
    if structure: parts.append(f'Song structure: {structure}')

    system = (
        'You are a Suno AI expert. Your job is to generate a precise, comprehensive style tag string '
        'that will be pasted directly into the Suno style field. '
        'RULES:\n'
        '1. Output ONLY a comma-separated list of 15-25 tags. No explanation, no JSON, no markdown, no numbering.\n'
        '2. EVERY provided option must be reflected in the tags — do not skip any.\n'
        '3. Include ALL of these categories that apply: genre, subgenre, mood/emotion, '
        'tempo/bpm descriptor, main instruments, vocal type & tone, sound quality/texture, '
        'production style, energy level.\n'
        '4. Use Suno-compatible English tag words. Short descriptors work best (e.g. "lo-fi", "dreamy reverb", '
        '"male falsetto", "slow 70bpm", "acoustic guitar", "cinematic strings").\n'
        '5. Make tags specific and varied — avoid vague generic words like "good" or "nice".'
    )
    user = '\n'.join(parts) + '\n\nGenerate comprehensive Suno style tags covering ALL the options above.'

    try:
        tags = call_gemini(system, user, max_tokens=300).strip().strip('`').strip()
        # 마크다운이나 설명이 붙으면 첫 줄만 사용
        first_line = tags.split('\n')[0].strip()
        if ',' in first_line:
            return first_line
        return tags
    except Exception as e:
        # 폴백: 옵션들 직접 조합
        fallback = [x for x in [genre, mood, lyric_style, instrument, vocal, sound, bpm] if x]
        return ', '.join(fallback)


# ── YT → MP3 ─────────────────────────────────────────────────
def fmt_time(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def sanitize(name):
    return "".join(c for c in name if c.isalnum() or c in (' ','-','_')).strip()

def _cobalt_download(url, video_id, job_id):
    """cobalt.tools API를 통해 YouTube 오디오 다운로드 (Oracle IP 차단 우회)"""
    import requests as req
    job_store[job_id]['percent'] = 10

    # 메타데이터: YouTube oEmbed (인증 불필요)
    title, author = video_id, ''
    try:
        m = req.get(f'https://www.youtube.com/oembed?url={url}&format=json', timeout=10)
        d = m.json()
        title = d.get('title', video_id)
        author = d.get('author_name', '')
    except Exception: pass

    job_store[job_id]['percent'] = 20

    # cobalt.tools API로 오디오 URL 요청
    cobalt_key = os.environ.get('COBALT_API_KEY', '').strip()
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    if cobalt_key:
        headers['Authorization'] = f'Api-Key {cobalt_key}'
    c = req.post('https://api.cobalt.tools/',
                 json={'url': url, 'downloadMode': 'audio', 'audioFormat': 'mp3', 'audioBitrate': '192'},
                 headers=headers,
                 timeout=30)
    cd = c.json()
    if cd.get('status') not in ('tunnel', 'redirect', 'stream'):
        err_code = cd.get('error', {}).get('code', str(cd))
        if 'auth' in err_code:
            raise Exception(f"cobalt 인증 필요: {err_code}. cobalt.tools에서 API 키 발급 후 COBALT_API_KEY 환경변수에 설정하세요")
        raise Exception(f"cobalt: {err_code}")

    job_store[job_id]['percent'] = 40

    # 오디오 파일 다운로드
    out_path = os.path.join(DOWNLOAD_DIR, f'{video_id}.mp3')
    ar = req.get(cd['url'], stream=True, timeout=180,
                 headers={'User-Agent': 'Mozilla/5.0'})
    ar.raise_for_status()
    size = 0
    with open(out_path, 'wb') as f:
        for chunk in ar.iter_content(65536):
            f.write(chunk); size += len(chunk)
            job_store[job_id]['percent'] = min(90, 40 + size // (1024 * 150))

    job_store[job_id]['percent'] = 95

    # ffprobe로 길이 확인
    duration = 0
    try:
        import subprocess
        fp = os.path.join(FFMPEG_DIR, 'ffprobe')
        if not os.path.exists(fp): fp = 'ffprobe'
        r = subprocess.run([fp, '-v', 'quiet', '-print_format', 'json', '-show_format', out_path],
                           capture_output=True, text=True, timeout=10)
        duration = float(json.loads(r.stdout).get('format', {}).get('duration', 0))
    except Exception: pass

    return [{'title': cleanTitle(title), 'artist': cleanArtist(author),
             'duration': duration, 'duration_fmt': fmt_time(duration),
             'timestamp': '00:00:00', 'file': f'{video_id}.mp3'}], title


# Invidious: local=true 파라미터로 강제 프록시 요청
# 반환된 URL이 googlevideo.com이면 해당 인스턴스는 프록시 미지원으로 건너뜀

def download_audio(url, job_id):
    import yt_dlp
    job_store[job_id] = {'status': 'downloading', 'percent': 0}

    # cobalt.tools (API 키 있을 때만)
    vid_m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    if vid_m and os.environ.get('COBALT_API_KEY', '').strip():
        try:
            tracks, pl_title = _cobalt_download(url, vid_m.group(1), job_id)
            job_store[job_id] = {'status': 'done', 'percent': 100, 'tracks': tracks,
                                 'playlist_title': pl_title, 'total_duration': tracks[0]['duration_fmt']}
            return
        except Exception as ce:
            print(f'[cobalt] failed: {ce}')

    # yt-dlp fallback — WARP 프록시(Cloudflare) 우선
    def hook(d):
        if d['status'] == 'downloading':
            try: job_store[job_id]['percent'] = float(d.get('_percent_str','0%').strip().replace('%',''))
            except: pass
        elif d['status'] == 'finished':
            job_store[job_id]['percent'] = 90

    COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    base = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'postprocessors': [{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}],
        'ffmpeg_location': _resolve_ffmpeg_dir(),
        'progress_hooks': [hook],
        'quiet': True, 'no_warnings': True,
    }
    cookie_extra = {'cookiefile': COOKIES_FILE} if os.path.exists(COOKIES_FILE) else {}

    def _run_download(extra={}):
        with yt_dlp.YoutubeDL({**base, **extra}) as ydl:
            return ydl.extract_info(url, download=True)

    try:
        info = None; last_err = None
        attempts = [
            {**cookie_extra, 'proxy': 'socks5://127.0.0.1:40000'},  # WARP
            {**cookie_extra, 'proxy': 'socks5://127.0.0.1:9050'},   # Tor
            {**cookie_extra},                                         # 직접
        ]
        for extra in attempts:
            try: info = _run_download(extra); break
            except Exception as e: last_err = e; print(f'[yt] {extra} err={e}')

        if info is None: raise last_err
        entries = info.get('entries') if 'entries' in info else [info]
        tracks, cum = [], 0
        for i, e in enumerate(entries):
            if not e: continue
            title = e.get('title', f'Track {i+1}')
            duration = e.get('duration', 0) or 0
            vid = e.get('id', '')
            actual = f"{vid}.mp3" if vid else None
            if actual and not os.path.exists(os.path.join(DOWNLOAD_DIR, actual)):
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.endswith('.mp3') and vid and vid in f:
                        actual = f; break
            tracks.append({'title': cleanTitle(title), 'artist': cleanArtist(e.get('uploader', e.get('channel',''))),
                           'duration': duration, 'duration_fmt': fmt_time(duration),
                           'timestamp': fmt_time(cum), 'file': actual or f"{vid or i+1}.mp3"})
            cum += duration
        job_store[job_id] = {'status': 'done', 'percent': 100, 'tracks': tracks,
                             'playlist_title': info.get('title', info.get('playlist_title','')),
                             'total_duration': fmt_time(cum)}
    except Exception as e:
        job_store[job_id] = {'status': 'error', 'error': str(e)}
        print(f'[yt error] {e}')

def cleanTitle(t):
    import re
    t = re.sub(r'\.(mp3|wav|flac|m4a|ogg|wmv)$', '', t, flags=re.I)
    t = re.sub(r'^\d+[-_.\s]+', '', t)
    return t.strip()

def cleanArtist(a):
    if not a or a.strip().lower() == 'unknown': return ''
    return a.strip()

@app.route('/api/yt/upload-cookies', methods=['POST'])
def yt_upload_cookies():
    f = request.files.get('cookies')
    if not f: return jsonify({'error':'파일 없음'}), 400
    cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    f.save(cookies_path)
    return jsonify({'ok': True})

@app.route('/api/yt/oauth2-init', methods=['POST'])
def yt_oauth2_init():
    if oauth2_state['status'] in ('starting', 'waiting'):
        return jsonify(oauth2_state)
    t = threading.Thread(target=_run_oauth2_init)
    t.daemon = True; t.start()
    return jsonify({'status': 'starting'})

@app.route('/api/yt/oauth2-status')
def yt_oauth2_status():
    if _oauth2_is_authorized() and oauth2_state['status'] != 'authorized':
        oauth2_state['status'] = 'authorized'
    return jsonify({**oauth2_state, 'logs': oauth2_logs[-15:]})

@app.route('/api/yt/cookies-status')
def yt_cookies_status():
    cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    return jsonify({'exists': os.path.exists(cookies_path)})

@app.route('/api/yt/debug')
def yt_debug():
    import socket
    result = {}
    # yt-dlp 버전
    try:
        import yt_dlp
        result['yt_dlp_version'] = yt_dlp.version.__version__
    except: result['yt_dlp_version'] = 'unknown'
    # Tor 연결 확인
    try:
        s = socket.create_connection(('127.0.0.1', 9050), timeout=2)
        s.close()
        result['tor'] = 'running'
    except: result['tor'] = 'not running'
    # 쿠키 파일 확인
    cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    if os.path.exists(cookies_path):
        size = os.path.getsize(cookies_path)
        try:
            with open(cookies_path) as f:
                first_line = f.readline().strip()
        except: first_line = '(read error)'
        result['cookies'] = {'size': size, 'first_line': first_line}
    else:
        result['cookies'] = None
    # WARP 연결 확인
    try:
        s = socket.create_connection(('127.0.0.1', 40000), timeout=2)
        s.close()
        result['warp'] = 'running'
    except: result['warp'] = 'not running'
    return jsonify(result)

@app.route('/api/yt/download', methods=['POST'])
def yt_download():
    url = request.json.get('url','').strip()
    if not url: return jsonify({'error':'URL 필요'}), 400
    job_id = str(int(time.time()*1000))
    t = threading.Thread(target=download_audio, args=(url, job_id))
    t.daemon = True; t.start()
    return jsonify({'job_id': job_id})

@app.route('/api/yt/clear-downloads', methods=['POST'])
def yt_clear_downloads():
    """다운로드 폴더 비우기 (사용자 명시 호출)"""
    removed = 0
    for f in os.listdir(DOWNLOAD_DIR):
        if f.endswith('.mp3'):
            try:
                os.remove(os.path.join(DOWNLOAD_DIR, f))
                removed += 1
            except: pass
    return jsonify({'removed': removed})

@app.route('/api/yt/progress/<job_id>')
def yt_progress(job_id):
    return jsonify(job_store.get(job_id, {'status':'unknown'}))

@app.route('/api/yt/download-file/<filename>')
def yt_file(filename):
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path): return jsonify({'error':'없음'}), 404
    return send_file(path, as_attachment=True, download_name=filename)

@app.route('/api/yt/upload-mp3', methods=['POST'])
def upload_mp3():
    import subprocess
    files = request.files.getlist('files')
    saved = []
    for f in files:
        if not f.filename.lower().endswith('.mp3'): continue
        path = os.path.join(DOWNLOAD_DIR, f.filename)
        f.save(path)
        duration = 0
        try:
            probe = os.path.join(FFMPEG_DIR, 'ffprobe.exe')
            r = subprocess.run([probe,'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',path], capture_output=True, text=True)
            duration = float(r.stdout.strip())
        except: pass
        saved.append({'title': cleanTitle(os.path.splitext(f.filename)[0]), 'artist':'', 'duration':duration, 'duration_fmt':fmt_time(duration), 'file':f.filename, 'source':'upload'})
    cum = 0
    for t in saved:
        t['timestamp'] = fmt_time(cum); cum += t['duration']
    return jsonify({'tracks': saved, 'total_duration': fmt_time(cum)})

# ── 가사 생성 ─────────────────────────────────────────────────
LANG_NAMES = {
    'ko':'한국어','en':'English','ja':'日本語','zh':'中文(繁體)',
    'pt':'Português','es':'Español','vi':'Tiếng Việt',
    'de':'Deutsch','it':'Italiano','nl':'Nederlands','sv':'Svenska'
}

LANG_INSTRUCTIONS = {
    'ko': '자연스러운 한국어 노래 가사로 작성하세요. 한국 대중음악 감성으로, 실제 K-pop이나 발라드에서 쓰이는 표현을 사용하세요.',
    'en': 'Write in natural English lyrics. Singable, emotionally resonant. Every line must express the given theme authentically.',
    'ja': '自然で歌いやすい日本語の歌詞。J-POPスタイルで感情豊かに。内容を変えないでください。',
    'zh': '用自然、可唱的繁體中文寫歌詞。符合華語流行音樂風格。保持原意不變。',
    'pt': 'Escreva letras em português natural e cantável. Estilo pop brasileiro. Mantenha o significado exato.',
    'es': 'Letras en español natural y cantable. Estilo pop latinoamericano. Mantén el significado exacto.',
    'vi': 'Lời bài hát tiếng Việt tự nhiên, dễ hát. Giữ nguyên ý nghĩa và cảm xúc. Không thêm hoặc bỏ nội dung.',
    'de': 'Natürliche, singbare deutsche Songtexte. Stil deutscher Pop-Musik. Bedeutung exakt beibehalten.',
    'it': 'Testi italiani naturali e cantabili. Stile pop italiano. Mantieni il significato esatto.',
    'nl': 'Natuurlijke, zingbare Nederlandse songteksten. Nederlandse popstijl. Betekenis exact behouden.',
    'sv': 'Naturliga, sjungbara svenska låttexter. Svensk popstil. Behåll den exakta innebörden.',
}

@app.route('/api/lyrics/generate', methods=['POST'])
def generate_lyrics():
    data = request.json
    genre = data.get('genre','')
    mood = data.get('mood','')
    theme = data.get('theme','')
    languages = data.get('languages', ['ko','en'])
    structure = data.get('structure', '[Verse 1], [Chorus], [Verse 2], [Chorus], [Bridge], [Chorus]')
    count = min(int(data.get('count',1)), 5)
    bpm = data.get('bpm','')
    length = data.get('length','')
    lyric_style = data.get('lyric_style','')
    avoid = data.get('avoid','')
    instrument = data.get('instrument','')
    vocal = data.get('vocal','')
    sound = data.get('sound','')

    job_id = str(int(time.time()*1000))
    job_store[job_id] = {'status':'processing', 'progress':5}

    def run():
        try:
            results = []

            # 세부 옵션 문자열 구성
            detail_parts = []
            if bpm: detail_parts.append(f'BPM/Tempo: {bpm}')
            if length: detail_parts.append(f'Song length: {length}')
            if lyric_style: detail_parts.append(f'Lyric style: {lyric_style}')
            if instrument: detail_parts.append(f'Main instrument: {instrument}')
            if vocal: detail_parts.append(f'Vocal style: {vocal}')
            if sound: detail_parts.append(f'Sound/Atmosphere: {sound}')
            detail_str = '\n'.join(detail_parts)

            def parse_lyrics(text):
                parsed = None
                clean = text.strip()
                if '```' in clean:
                    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', clean, re.DOTALL)
                    if m:
                        try: parsed = json.loads(m.group(1))
                        except: pass
                    if not parsed:
                        for p in clean.split('```'):
                            p = p.strip()
                            if p.lower().startswith('json'): p = p[4:].strip()
                            if p.startswith('{'):
                                try: parsed = json.loads(p); break
                                except: pass
                if not parsed:
                    try: parsed = json.loads(clean)
                    except: pass
                if not parsed:
                    s, e = clean.find('{'), clean.rfind('}')
                    if s != -1 and e != -1:
                        try: parsed = json.loads(clean[s:e+1])
                        except: pass
                if not parsed:
                    fallback = re.sub(r'```(?:json)?', '', clean).replace('```', '').strip()
                    return [{'title': 'Song 1', 'lyrics': fallback}]
                raw = parsed.get('songs', [])
                songs = []
                for rs in raw:
                    title = (rs.get('title') or rs.get('titulo') or rs.get('titre') or
                             rs.get('タイトル') or rs.get('tiêu đề') or rs.get('ชื่อเพลง') or
                             rs.get('judul') or '')
                    lyrics = (rs.get('lyrics') or rs.get('letra') or rs.get('paroles') or
                              rs.get('歌詞') or rs.get('lời bài hát') or rs.get('เนื้อเพลง') or
                              rs.get('lirik') or rs.get('text') or '')
                    songs.append({'title': str(title).strip(), 'lyrics': str(lyrics).strip()})
                return songs

            _FOREIGN_RE = re.compile('[^가-힣ᄀ-ᇿ㄰-㆏\x00-\x7f]')

            def has_foreign_chars(songs):
                return any(_FOREIGN_RE.search(s.get('lyrics', '')) for s in songs)

            def purge_foreign_words(songs):
                for s in songs:
                    lines = s['lyrics'].split('\n')
                    cleaned = []
                    for line in lines:
                        if re.match(r'^\[.+\]$', line.strip()):
                            cleaned.append(line)
                        else:
                            line = re.sub(r'\S*[^가-힣ᄀ-ᇿ㄰-㆏\x00-\x7f]\S*', '', line)
                            cleaned.append(re.sub(r' {2,}', ' ', line).strip())
                    s['lyrics'] = '\n'.join(cleaned)
                return songs

            def normalize_songs(songs):
                if not isinstance(songs, list):
                    songs = list(songs.values()) if isinstance(songs, dict) else []
                for i, s in enumerate(songs):
                    if not isinstance(s, dict): continue
                    lyr = s.get('lyrics', '')
                    if isinstance(lyr, dict):
                        lyr = '\n'.join(str(v) for v in lyr.values() if v)
                    lyr = str(lyr) if not isinstance(lyr, str) else lyr
                    lyr = lyr.replace('\r\n','\n').replace('\r','\n')
                    if '\\n' in lyr: lyr = lyr.replace('\\n','\n')
                    s['lyrics'] = lyr
                    if not s.get('title','').strip(): s['title'] = f'Song {i+1}'
                return songs

            def translate_songs(songs, target_lang, source_lang='ko'):
                """
                전체 곡을 1회 호출로 번역 (배치화).
                  - 호출 수: N곡 × M언어 → M언어 (N배 감소)
                  - 구조화 출력으로 JSON 보장
                """
                if not songs: return []
                lang_name = LANG_NAMES.get(target_lang, target_lang)
                system = (
                    f'You are a professional song lyrics translator. '
                    f'These are original user-created Korean song lyrics — no copyright concerns. '
                    f'Translate each provided song into natural, singable {lang_name} that sounds like '
                    f'it was originally written in {lang_name}.\n'
                    f'GUIDELINES:\n'
                    f'1. Read the full lyrics first to understand theme, emotion, and narrative arc.\n'
                    f'2. Choose words that carry the same emotional weight and register — never word-for-word.\n'
                    f'3. If a line is unclear, use context to infer intent and write a natural {lang_name} line.\n'
                    f'4. The result must read as one cohesive song in {lang_name} with smooth transitions.\n'
                    f'5. Keep section markers ([Verse 1], [Chorus] etc.) and line count.\n'
                    f'6. Translate the title too — make it natural in {lang_name}.\n'
                    f'7. Preserve the order: output songs in the SAME order as input.'
                )
                payload = [{'title': s['title'], 'lyrics': s['lyrics']} for s in songs]
                prompt = (
                    f'Translate the following {len(songs)} Korean song(s) to {lang_name}.\n'
                    f'INPUT (JSON):\n{json.dumps(payload, ensure_ascii=False)}'
                )
                schema = {
                    'type': 'object',
                    'properties': {
                        'songs': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'title': {'type': 'string'},
                                    'lyrics': {'type': 'string'},
                                },
                                'required': ['title', 'lyrics'],
                                'additionalProperties': False,
                            }
                        }
                    },
                    'required': ['songs'],
                    'additionalProperties': False,
                }
                # 곡당 ~2500토큰 여유. 캐싱은 짧은 시스템이라 미적용(2048토큰 미만).
                budget = min(16000, 2500 * len(songs))
                last_err = None
                for attempt in range(4):
                    try:
                        result = call_claude_json(system, prompt, schema=schema, max_tokens=budget)
                        out = result.get('songs', []) or []
                        # 누락 보강
                        translated = []
                        for i, s in enumerate(songs):
                            if i < len(out) and isinstance(out[i], dict):
                                translated.append({
                                    'title': str(out[i].get('title') or s['title']).strip(),
                                    'lyrics': str(out[i].get('lyrics') or '').strip(),
                                })
                            else:
                                translated.append({'title': s['title'], 'lyrics': '[번역 누락]'})
                        return translated
                    except Exception as e:
                        last_err = e
                        err_str = str(e)
                        if '529' in err_str or 'overloaded' in err_str.lower() or '500' in err_str:
                            wait = 5 * (attempt + 1)
                            print(f'[translate] 재시도 {attempt+1}/4, {wait}초 대기...')
                            time.sleep(wait)
                        else:
                            break
                return [{'title': s['title'], 'lyrics': f'[번역 오류: {last_err}]'} for s in songs]

            # ── STEP 1: 한국어 가사 생성 ──────────────────────
            job_store[job_id]['current'] = '한국어 가사 생성 중...'
            job_store[job_id]['progress'] = 10

            ko_system = (
                'You are a professional Korean hip-hop lyricist. '
                'You write like top-tier Korean rap artists — sharp, specific, rhythmic, street-authentic. '
                f'Generate {count} Korean hip-hop song(s). '
                '!!! LANGUAGE RULE — ABSOLUTE, NO EXCEPTIONS !!!\n'
                'Use ONLY Korean Hangul (한글) characters.\n'
                'FORBIDDEN — any of these will FAIL the task:\n'
                '- Chinese characters (全, 部, 答 etc.)\n'
                '- Japanese (kanji, hiragana, katakana)\n'
                '- Spanish, English, or ANY foreign words (no "corazón", no "amor", no "baby")\n'
                '- Any non-Hangul script whatsoever\n'
                'If a concept has no Korean word, describe it in Korean instead. '
                'Every single character must be Korean Hangul or Korean punctuation only.'
            )
            ko_user = (
                f'Genre/Style: {genre}\nMood: {mood}\nTheme: {theme}\n'
                f'Song structure: {structure}\n'
                + (f'Rap flow: {lyric_style}\n' if lyric_style else '')
                + (f'Beat: {instrument}\n' if instrument else '')
                + (f'BPM: {bpm}\n' if bpm else '')
                + (f'Lyric density: {length}\n' if length else '')
                + (f'Vocal: {vocal}\n' if vocal else '')
                + (f'Sound: {sound}\n' if sound else '')
                + '\nHIP-HOP LYRIC RULES:\n'
                '1. BAR STRUCTURE: Each line = one bar. Verse = 8-16 bars. '
                'Hook = 4-8 bars, short and punchy, designed to repeat. '
                'Never run sentences across bars.\n'
                '2. RHYME: Consistent end-rhymes (AABB or ABAB) in Korean phonetics. '
                'Add internal rhymes and syllabic echo where natural.\n'
                '3. SYLLABLE DENSITY: Match to flow. Sparse = 6-10 syl/bar. '
                'Standard = 10-14. Dense/multisyllabic = 14-20. Stay consistent per section.\n'
                '4. SPECIFICITY: Concrete images over abstract emotions. '
                'Show the scene, not the feeling. Street-level language. '
                'Instead of "난 외로워" → describe the empty room at 3am, the phone screen, the silence.\n'
                '5. HOOK ≠ VERSE: Hook must be catchier, simpler, more repetitive. '
                'Hook = 1-2 punchlines that stick. Verse = storytelling/flow showcase.\n'
                '6. FLOW LOCK: If straight rap or boom bap is selected — NO singing anywhere. '
                'If melodic rap — rap in verses, sing in hooks only. No exceptions.\n'
                '7. NO FILLER: No "baby", "oh oh oh", "yeah yeah". '
                'Every bar must deliver — image, rhyme, punch, or story.\n'
                '8. VARIETY: If generating multiple songs, completely different vocab, metaphors, flow patterns.\n'
                + (f'FORBIDDEN WORDS/PHRASES: {avoid}\n' if avoid else '')
                + f'\nGenerate {count} unique Korean hip-hop song(s) now.'
            )
            ko_schema = {
                'type': 'object',
                'properties': {
                    'songs': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'title': {'type': 'string'},
                                'lyrics': {'type': 'string'},
                            },
                            'required': ['title', 'lyrics'],
                            'additionalProperties': False,
                        }
                    }
                },
                'required': ['songs'],
                'additionalProperties': False,
            }
            ko_budget = min(16000, 2500 * max(1, count))
            for _attempt in range(3):
                ko_result = call_claude_json(ko_system, ko_user, schema=ko_schema, max_tokens=ko_budget)
                ko_songs = normalize_songs(ko_result.get('songs', []))
                if not has_foreign_chars(ko_songs):
                    break
            if has_foreign_chars(ko_songs):
                ko_songs = purge_foreign_words(ko_songs)
            results.append({'lang': 'ko', 'lang_name': '한국어', 'songs': ko_songs})
            job_store[job_id]['progress'] = 25

            # ── STEP 2: 나머지 언어 (한국어 기반 직접 번역) ──
            other_langs = [l for l in languages if l != 'ko']
            total = len(other_langs)
            for i, lang in enumerate(other_langs):
                job_store[job_id]['current'] = f'{LANG_NAMES.get(lang,lang)} 번역 중...'
                job_store[job_id]['progress'] = 25 + int((i+1)/max(total,1) * 65)
                translated = translate_songs(ko_songs, lang)
                results.append({'lang': lang, 'lang_name': LANG_NAMES.get(lang,lang), 'songs': translated})

            # ── STEP 4: Suno 태그 생성 ────────────────────────
            job_store[job_id]['current'] = 'Suno 태그 생성 중...'
            suno_tags = build_suno_tags(
                genre=genre, mood=mood, theme=theme,
                bpm=bpm, length=length, lyric_style=lyric_style,
                instrument=instrument, vocal=vocal, sound=sound, structure=structure
            )

            if results:
                results[0]['suno_tags'] = suno_tags

            job_store[job_id] = {'status':'done', 'progress':100, 'results': results}
        except Exception as e:
            job_store[job_id] = {'status':'error', 'error':str(e)}
            print(f'[lyrics error] {e}')

    t = threading.Thread(target=run); t.daemon=True; t.start()
    return jsonify({'job_id': job_id})

# ── Suno 태그 재생성 ─────────────────────────────────────────
@app.route('/api/suno/tags', methods=['POST'])
def suno_tags():
    data = request.json
    genre = data.get('genre','')
    mood = data.get('mood','')
    theme = data.get('theme','')
    bpm = data.get('bpm','')
    length = data.get('length','')
    lyric_style = data.get('lyric_style','')
    instrument = data.get('instrument','')
    vocal = data.get('vocal','')
    sound = data.get('sound','')

    detail_parts = []
    if bpm: detail_parts.append(f'BPM: {bpm}')
    if length: detail_parts.append(f'Length: {length}')
    if lyric_style: detail_parts.append(f'Style: {lyric_style}')
    if instrument: detail_parts.append(f'Instrument: {instrument}')
    if vocal: detail_parts.append(f'Vocal: {vocal}')
    if sound: detail_parts.append(f'Sound: {sound}')

    try:
        tags = build_suno_tags(
            genre=genre, mood=mood, theme=theme,
            bpm=bpm, length=length, lyric_style=lyric_style,
            instrument=instrument, vocal=vocal, sound=sound
        )
        return jsonify({'success': True, 'tags': tags})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── Suno 프롬프트 생성기 ──────────────────────────────────────
@app.route('/api/suno/prompt', methods=['POST'])
def suno_prompt():
    data = request.json
    genre = data.get('genre','')
    mood = data.get('mood','')
    theme = data.get('theme','')
    bpm = data.get('bpm','')
    lyric_style = data.get('lyric_style','')
    instrument = data.get('instrument','')
    vocal = data.get('vocal','')
    sound = data.get('sound','')
    structure = data.get('structure','')
    count = min(int(data.get('count', 3)), 10)

    detail_parts = []
    if bpm: detail_parts.append(f'BPM: {bpm}')
    if lyric_style: detail_parts.append(f'Lyric style: {lyric_style}')
    if instrument: detail_parts.append(f'Instrument: {instrument}')
    if vocal: detail_parts.append(f'Vocal: {vocal}')
    if sound: detail_parts.append(f'Sound: {sound}')
    if structure: detail_parts.append(f'Song structure: {structure}')

    # 옵션 요약 문자열
    opts_summary = '\n'.join(detail_parts)

    system = 'You are a Suno AI music expert. Generate song prompts for Suno AI.'
    user = (
        f'Base options (ALL must be reflected in every tags field):\n'
        f'Genre: {genre}\nMood: {mood}\nTheme: {theme}\n'
        + (opts_summary + '\n' if opts_summary else '') +
        f'\nGenerate {count} different song prompt sets.\n'
        f'For each set:\n'
        f'- title: a creative song title matching the vibe\n'
        f'- tags: 20-25 comprehensive Suno-compatible tags covering genre, mood, instruments, '
        f'vocal style, tempo, sound texture, energy — ALL base options must appear\n'
        f'- description: one sentence describing the song feel\n'
        f'Each set should vary in creative direction while keeping the base options.'
    )
    schema = {
        'type': 'object',
        'properties': {
            'prompts': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'title': {'type': 'string'},
                        'tags': {'type': 'string'},
                        'description': {'type': 'string'},
                    },
                    'required': ['title', 'tags', 'description'],
                    'additionalProperties': False,
                }
            }
        },
        'required': ['prompts'],
        'additionalProperties': False,
    }
    try:
        parsed = call_claude_json(system, user, schema=schema, max_tokens=1200)
        prompts = parsed.get('prompts', [])
        return jsonify({'success': True, 'prompts': prompts})
    except Exception as e:
        err = str(e)
        if '529' in err or 'overloaded' in err.lower():
            msg = 'Anthropic 서버가 현재 과부하 상태입니다. 잠시 후 다시 시도해주세요. (529 Overloaded)'
        else:
            msg = err
        return jsonify({'success': False, 'error': msg}), 500


# ── 영어 가사 다듬기 ─────────────────────────────────────────
@app.route('/api/polish/lyrics', methods=['POST'])
def polish_lyrics():
    data = request.json
    text = data.get('text','').strip()
    if not text: return jsonify({'error':'텍스트 필요'}), 400

    system = (
        'You are a professional English songwriter and lyric editor. '
        'Your job is to polish the given English song lyrics — fix awkward phrasing, improve word choices, '
        'and make the lyrics flow naturally as a song — while preserving the original meaning, emotion, and narrative. '
        '\nGUIDELINES:\n'
        '1. Keep the original intent and story intact. Do not add new themes or change what the song is about.\n'
        '2. Fix lines that are grammatically broken, overly literal, or do not read naturally as song lyrics.\n'
        '3. Improve word choices where they feel clunky or unnatural (e.g. replace overly simplistic words with more evocative ones).\n'
        '4. Ensure smooth transitions between lines so the song flows as a cohesive whole.\n'
        '5. Keep section markers ([Verse 1], [Chorus] etc.) and line count exactly.\n'
        '6. If a line is already well-written, leave it as-is.\n'
        'Return ONLY the polished lyrics. No explanations, no notes.'
    )
    try:
        polished = call_claude(system, f'Polish these song lyrics:\n\n{text}', max_tokens=3000)
        return jsonify({'success':True, 'polished':polished})
    except Exception as e:
        return jsonify({'success':False, 'error':str(e)}), 500


# ── 번역 ──────────────────────────────────────────────────────
@app.route('/api/translate', methods=['POST'])
def translate():
    data = request.json
    text = data.get('text','').strip()
    target_lang = data.get('target_lang','en')
    if not text: return jsonify({'error':'텍스트 필요'}), 400

    lang_name = LANG_NAMES.get(target_lang, target_lang)
    is_to_ko = (target_lang == 'ko')
    if is_to_ko:
        system = (
            f'You are a professional Korean lyricist and translator. '
            f'Translate the given song lyrics into Korean that sounds like it was originally written in Korean — not a translation. '
            f'\nAPPROACH:\n'
            f'Read the FULL lyrics first to grasp the overall theme, emotion, and narrative arc. Then translate with these priorities:\n'
            f'(A) EXPRESSION QUALITY: Choose Korean words that carry the same emotional weight and register as the original. '
            f'Never translate word-for-word. For example: "clever" in an emotional context → "현명한" not "똑똑한". '
            f'"kept my peace" → "내 평화를 지키기로 했어" not "그냥 조용했어".\n'
            f'(B) AWKWARD OR UNCLEAR LINES: If a line is broken, overly literal, or does not flow as a lyric, '
            f'use surrounding context to determine intent and write a natural Korean lyric that fits. '
            f'The Korean must always make sense as a complete flowing song — even if the English does not.\n'
            f'(C) CONSISTENCY: The Korean must read as one cohesive song with natural transitions throughout.\n'
            f'Keep section markers and line count. Return ONLY the Korean lyrics. No notes.'
        )
    else:
        system = (
            f'You are a professional song lyrics translator. '
            f'These are original user-created lyrics — no copyright concerns. '
            f'Translate the given song lyrics into natural, singable {lang_name}. '
            f'\nGUIDELINES:\n'
            f'1. Write natural {lang_name} that flows well as song lyrics. '
            f'Adapt phrasing so it sounds like it was written in {lang_name} — not a literal translation.\n'
            f'2. Stay true to the original meaning and emotion. Do not add or remove content.\n'
            f'3. If a passage is incoherent or creates a non-sequitur, use surrounding context to infer intent and connect it naturally.\n'
            f'4. Keep section markers ([Verse 1], [Chorus] etc.) and line count.\n'
            f'5. Always complete the full translation. No disclaimers or notes.\n'
            f'Return ONLY the translated lyrics with section markers.'
        )
    try:
        translated = call_claude(system, f'Translate these original user-created lyrics to {lang_name}:\n\n{text}', max_tokens=3000)
        return jsonify({'success':True, 'translated':translated, 'lang_name':lang_name})
    except Exception as e:
        return jsonify({'success':False, 'error':str(e)}), 500

# ── YouTube 업로드 최적화 ───────────────────────────────────
@app.route('/api/youtube/optimize', methods=['POST'])
def youtube_optimize():
    import urllib.parse
    data = request.json
    title = data.get('title','')
    genre = data.get('genre','')
    mood = data.get('mood','')
    lang = data.get('lang','ko')
    lyrics = data.get('lyrics','')
    options = data.get('options', {})

    settings = {}
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE,'r') as f:
                settings = json.load(f)
    except: pass

    yt_api_key = settings.get('yt_api_key','')
    refs = []

    # ── YouTube Data API로 유사 영상 검색 ─────────────────────
    if yt_api_key:
        try:
            lang_map = {'ko':'ko','en':'en','ja':'ja','zh':'zh-TW','pt':'pt','es':'es','vi':'vi','de':'de','it':'it','nl':'nl','sv':'sv'}
            hl = lang_map.get(lang,'ko')
            search_query = ' '.join(filter(None,[title,genre,mood,'music']))
            search_url = (
                f'https://www.googleapis.com/youtube/v3/search'
                f'?part=snippet&q={urllib.parse.quote(search_query)}'
                f'&type=video&videoCategoryId=10'
                f'&maxResults=10&relevanceLanguage={hl}'
                f'&key={yt_api_key}'
            )
            import urllib.request as ureq
            with ureq.urlopen(search_url, timeout=10) as r:
                search_data = json.loads(r.read())

            video_ids = [it['id']['videoId'] for it in search_data.get('items',[]) if it.get('id',{}).get('videoId')]

            if video_ids:
                # 영상 상세 정보 (태그, 통계 포함)
                detail_url = (
                    f'https://www.googleapis.com/youtube/v3/videos'
                    f'?part=snippet,statistics&id={",".join(video_ids)}'
                    f'&key={yt_api_key}'
                )
                with ureq.urlopen(detail_url, timeout=10) as r:
                    detail_data = json.loads(r.read())

                for item in detail_data.get('items',[]):
                    snip = item.get('snippet',{})
                    stats = item.get('statistics',{})
                    views = int(stats.get('viewCount',0))
                    views_fmt = f'{views:,}회' if views else ''
                    refs.append({
                        'id': item['id'],
                        'title': snip.get('title',''),
                        'description': snip.get('description','')[:300],
                        'tags': snip.get('tags',[])[:20],
                        'views': views_fmt,
                        'channel': snip.get('channelTitle',''),
                    })
        except Exception as e:
            print(f'[YT API] {e}')

    # ── Claude로 최적화 추천 생성 ─────────────────────────────
    lang_labels = {'ko':'한국어','en':'English','ja':'日本語','zh':'Traditional Chinese','pt':'Português','es':'Español','vi':'Tiếng Việt','de':'Deutsch','it':'Italiano','nl':'Nederlands','sv':'Svenska'}
    lang_label = lang_labels.get(lang,'한국어')

    # 참고 데이터 요약
    ref_summary = ''
    if refs:
        titles_sample = [r['title'] for r in refs[:5]]
        tags_sample = []
        for r in refs[:5]:
            tags_sample.extend(r.get('tags',[]))
        ref_summary = (
            f'\n\nReference videos found (analyze these for patterns):\n'
            f'Titles: {json.dumps(titles_sample, ensure_ascii=False)}\n'
            f'Common tags: {json.dumps(list(set(tags_sample))[:30], ensure_ascii=False)}'
        )

    # 옵션에 따라 동적으로 스키마/요청 구성
    props = {}
    required = []
    want_lines = []
    if options.get('titles', True):
        props['titles'] = {'type': 'array', 'items': {'type': 'string'}}
        required.append('titles')
        want_lines.append('- titles: 5 compelling title variations (analyze reference patterns if available)')
    if options.get('description', True):
        props['description'] = {'type': 'string'}
        required.append('description')
        want_lines.append('- description: 2-3 short paragraphs, SEO optimized, hashtags at end (keep concise)')
    if options.get('tags', True):
        props['tags'] = {'type': 'array', 'items': {'type': 'string'}}
        required.append('tags')
        want_lines.append('- tags: 25-30 relevant tags as array')
    if options.get('hashtags', True):
        props['hashtags'] = {'type': 'array', 'items': {'type': 'string'}}
        required.append('hashtags')
        want_lines.append('- hashtags: 10-15 hashtags as array')
    if options.get('comment', True):
        props['comment'] = {'type': 'string'}
        required.append('comment')
        want_lines.append('- comment: engaging pinned comment to boost engagement')

    schema = {
        'type': 'object',
        'properties': props,
        'required': required,
        'additionalProperties': False,
    }

    system = (
        'You are a YouTube SEO expert specializing in music content. '
        f'Generate YouTube upload optimization content in {lang_label}.'
    )
    user = (
        f'Song title: {title}\nGenre: {genre}\nMood: {mood}\n'
        + (f'Lyrics excerpt:\n{lyrics[:400]}\n' if lyrics else '') +
        ref_summary +
        f'\n\nGenerate YouTube optimization for this music video upload.\n'
        f'Language: {lang_label}\n' +
        '\n'.join(want_lines)
    )

    try:
        result = call_claude_json(system, user, schema=schema, max_tokens=2500)
        return jsonify({
            'success': True,
            'refs': [{'id':r['id'],'title':r['title'],'views':r['views']} for r in refs],
            'titles': result.get('titles',[]),
            'description': result.get('description',''),
            'tags': result.get('tags',[]),
            'hashtags': result.get('hashtags',[]),
            'comment': result.get('comment',''),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── 프롬프트 생성 (쇼츠 스튜디오) ────────────────────────────
@app.route('/api/prompts/generate', methods=['POST'])
def gen_prompts():
    data = request.json
    genre = data.get('genre','')
    mood = data.get('mood','')
    keywords = data.get('keywords','')
    style = data.get('style','')

    system = 'You are a creative director for music video shorts.'
    user = (
        f'Genre: {genre}\nMood: {mood}\nKeywords: {keywords}\nVisual style: {style}\n'
        f'Generate: midjourney prompt (no --ar flags), suno style tags, capcut editing suggestion.'
    )
    schema = {
        'type': 'object',
        'properties': {
            'midjourney': {'type': 'string'},
            'suno_tags': {'type': 'string'},
            'capcut': {'type': 'string'},
        },
        'required': ['midjourney', 'suno_tags', 'capcut'],
        'additionalProperties': False,
    }
    try:
        prompts = call_claude_json(system, user, schema=schema, max_tokens=600)
        return jsonify({'success':True, 'prompts':prompts})
    except Exception as e:
        return jsonify({'success':False, 'error':str(e)}), 500

# ── 장면 프롬프트 ─────────────────────────────────────────────
@app.route('/api/prompts/scenes', methods=['POST'])
def gen_scenes():
    data = request.json
    lyrics = data.get('lyrics','').strip()
    style = data.get('style','modern anime style')
    if not lyrics: return jsonify({'error':'가사 필요'}), 400

    # 가사가 너무 길면 앞부분만 사용
    lyrics_trimmed = lyrics[:1500] if len(lyrics) > 1500 else lyrics

    system = (
        'You are an anime storyboard director. '
        'Given song lyrics, generate exactly 10 Midjourney image prompts for key scenes.'
    )
    user = (
        f'Visual style: {style}\n\n'
        f'Lyrics:\n{lyrics_trimmed}\n\n'
        f'Generate 10 scene prompts. Rules:\n'
        f'- Each prompt should describe a visual scene inspired by that lyric section\n'
        f'- Include lighting, color, composition details\n'
        f'- Do NOT include --ar or any parameters'
    )
    schema = {
        'type': 'object',
        'properties': {
            'scenes': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'index': {'type': 'integer'},
                        'lyric': {'type': 'string'},
                        'prompt': {'type': 'string'},
                    },
                    'required': ['index', 'lyric', 'prompt'],
                    'additionalProperties': False,
                }
            }
        },
        'required': ['scenes'],
        'additionalProperties': False,
    }
    try:
        parsed = call_claude_json(system, user, schema=schema, max_tokens=3000)
        scenes = parsed.get('scenes', [])
        if not scenes:
            return jsonify({'success': False, 'error': '장면 데이터를 파싱할 수 없습니다. 다시 시도해주세요.'}), 500
        return jsonify({'success': True, 'scenes': scenes})
    except Exception as e:
        print(f'[scenes error] {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

# ── 자막 줄 재배분 ───────────────────────────────────────────
@app.route('/api/subtitle/redistribute', methods=['POST'])
def subtitle_redistribute():
    data = request.json
    lyrics = data.get('lyrics','').strip()
    target_count = int(data.get('target_count', 0))
    if not lyrics or not target_count:
        return jsonify({'success':False,'error':'lyrics and target_count required'}), 400

    system = (
        'You are a subtitle editor. Redistribute song lyrics into exactly the specified number of subtitle lines. '
        'RULES:\n'
        f'1. Output EXACTLY {target_count} lines — no more, no less.\n'
        '2. Keep each line short enough to read as a subtitle (max ~60 chars).\n'
        '3. Preserve the original meaning and natural phrasing.\n'
        '4. Section markers like [Verse], [Chorus] should be omitted or merged into adjacent lines.\n'
        '5. Do not add or invent new lyrics — only split or merge existing lines.'
    )
    user = f'Redistribute into exactly {target_count} subtitle lines:\n\n{lyrics}'
    schema = {
        'type': 'object',
        'properties': {
            'lines': {'type': 'array', 'items': {'type': 'string'}}
        },
        'required': ['lines'],
        'additionalProperties': False,
    }

    try:
        parsed = call_claude_json(system, user, schema=schema, max_tokens=2000)
        lines = parsed.get('lines', [])
        if len(lines) != target_count:
            # 조정
            if len(lines) > target_count:
                lines = lines[:target_count]
            else:
                while len(lines) < target_count:
                    lines.append('')
        return jsonify({'success':True, 'lines':lines})
    except Exception as e:
        return jsonify({'success':False,'error':str(e)}), 500


# ── 플레이리스트 분석 (감성힙합 등 키워드 기반) ──────────────
PLAYLIST_PRESETS = {
    "1": {"name": "감성힙합 표준",
          "queries": ["감성힙합 플레이리스트", "감성힙합 플리", "감성힙합 R&B 플레이리스트",
                      "감성힙합 드라이브 플리", "국힙 플레이리스트"]},
    "2": {"name": "감성힙합 무드별 (새벽/밤/드라이브)",
          "queries": ["새벽 감성힙합 플레이리스트", "밤 감성힙합 플리", "드라이브 감성힙합 플레이리스트",
                      "비올때 듣는 감성힙합", "혼술 감성힙합 플리"]},
    "3": {"name": "R&B / 알앤비 중심",
          "queries": ["감성 R&B 플레이리스트", "한국 알앤비 플리", "감성 알앤비 노래모음",
                      "R&B 드라이브 플레이리스트", "달달한 R&B 플리"]},
    "4": {"name": "Lofi / 칠 힙합",
          "queries": ["lofi 힙합 플레이리스트", "로파이 힙합 플리", "chill hiphop playlist",
                      "공부할때 듣는 lofi", "lofi 한국 플레이리스트"]},
    "5": {"name": "장르 세분화 (트랩/붐뱁/멜로딕)",
          "queries": ["멜로딕 랩 플레이리스트", "붐뱁 힙합 플리", "트랩 플레이리스트 한국",
                      "올드스쿨 힙합 플레이리스트", "다크 힙합 플리"]},
    "6": {"name": "분위기 키워드 (느좋/감성/달달)",
          "queries": ["느좋 플레이리스트 힙합", "감성 플리 노래모음", "달달한 힙합 플리",
                      "분위기 있는 힙합 플레이리스트", "취향저격 감성힙합"]},
    "7": {"name": "씬/아티스트 중심",
          "queries": ["쇼미더머니 플레이리스트", "AOMG 플리", "하이라이트레코즈 플레이리스트",
                      "VMC 플리", "한국 힙합 명곡 플리"]},
    "8": {"name": "글로벌 / 영문 키워드",
          "queries": ["korean hiphop playlist", "k-rnb playlist", "k-hiphop chill playlist",
                      "korean rap playlist", "kpop hiphop playlist"]},
}

PL_ARTIST_ALIASES = {
    "big naughty": "BIG Naughty", "빅나티": "BIG Naughty", "서동현": "BIG Naughty",
    "beenzino": "빈지노", "leellamarz": "릴러말즈", "pateko": "PATEKO", "파테코": "PATEKO",
    "meenoi": "미노이", "heize": "헤이즈", "moon": "MOON", "be'o": "BE'O", "비오": "BE'O",
    "coogie": "쿠기", "thama": "THAMA", "jayci yucca": "Jayci Yucca",
    "ash island": "ASH ISLAND", "ph-1": "pH-1", "wonstein": "원슈타인",
    "lee young ji": "이영지", "geeks": "긱스", "giriboy": "기리보이",
    "skinny brown": "Skinny Brown", "zion.t": "Zion.T", "zion t": "Zion.T",
    "colde": "Colde", "col de": "Colde", "dean": "DEAN",
}

PL_PATTERN_A = re.compile(
    r"^[^\S\n]*(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<artist>.+?)\s+[-–—]\s+(?P<title>.+?)\s*$",
    re.MULTILINE)
PL_PATTERN_B = re.compile(
    r"^[^\S\n]*\d+\.\s*(?P<title>.+?)\s+[-–—]\s+(?P<artist>.+?)\s+(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\s*$",
    re.MULTILINE)


def pl_parse_duration(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def pl_parse_tracklist(desc):
    tracks = []
    for pat in (PL_PATTERN_A, PL_PATTERN_B):
        for m in pat.finditer(desc):
            artist = m.group("artist").strip()
            title = m.group("title").strip()
            ts = m.group("ts").strip()
            if not artist or not title:
                continue
            if len(artist) > 60 or len(title) > 120:
                continue
            text = (artist + " " + title).lower()
            if any(w in text for w in ["http", "@", "email", "instagram", "youtube.com", "subscribe", "구독"]):
                continue
            tracks.append({"timestamp": ts, "artist": artist, "title": title})
        if tracks:
            break
    return tracks


def pl_normalize_artist(name):
    cleaned = re.sub(r"\s*\([^)]*\)\s*", "", name).strip()
    return PL_ARTIST_ALIASES.get(cleaned.lower(), cleaned)


def pl_split_artists(s):
    parts = re.split(r"\s*[,&]\s*|\s+and\s+", s)
    return [p.strip() for p in parts if p.strip()]


def pl_normalize_title(title):
    pat = re.compile(
        r"\s*[\(\[](?:feat|featuring|prod|produced by|with)\.?\s*"
        r"[^()\[\]]*(?:[\(\[][^()\[\]]*[\)\]][^()\[\]]*)*[\)\]]", re.IGNORECASE)
    prev, cleaned = None, title
    while prev != cleaned:
        prev = cleaned
        cleaned = pat.sub("", cleaned)
    return cleaned.strip()


def pl_extract_featured(title):
    out = []
    pat = re.compile(
        r"[\(\[](?:feat|featuring)\.?\s+"
        r"(?P<names>[^()\[\]]*(?:[\(\[][^()\[\]]*[\)\]][^()\[\]]*)*)[\)\]]", re.IGNORECASE)
    for m in pat.finditer(title):
        names = re.sub(r"\s*[\(\[][^()\[\]]*[\)\]]", "", m.group("names"))
        out.extend(pl_split_artists(names))
    return out


@app.route('/api/playlist-analyzer/presets', methods=['GET'])
def pl_get_presets():
    return jsonify({
        "success": True,
        "presets": [{"id": k, "name": v["name"], "queries": v["queries"]}
                    for k, v in PLAYLIST_PRESETS.items()]
    })


@app.route('/api/playlist-analyzer/run', methods=['POST'])
def pl_run():
    import urllib.parse, urllib.request as ureq
    from collections import Counter

    data = request.json or {}
    settings = {}
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
    except:
        pass
    yt_key = settings.get('yt_api_key', '')
    if not yt_key:
        return jsonify({'success': False, 'error': 'YouTube API 키가 설정되지 않았습니다. 업로드 최적화 탭에서 먼저 등록하세요.'}), 400

    queries = data.get('queries', [])
    if not queries:
        return jsonify({'success': False, 'error': '검색어가 필요합니다.'}), 400
    queries = queries[:20]  # 최대 20개

    min_duration = int(data.get('min_duration', 3600))
    max_per_query = max(1, min(50, int(data.get('max_per_query', 50))))
    tag_filter = (data.get('tag_filter') or '').strip()

    # 1) 검색 - 영상 ID 수집
    all_ids = set()
    search_errors = []
    for q in queries:
        try:
            url = (f'https://www.googleapis.com/youtube/v3/search'
                   f'?part=id&q={urllib.parse.quote(q)}'
                   f'&type=video&videoDuration=long&maxResults={max_per_query}'
                   f'&relevanceLanguage=ko&key={yt_key}')
            with ureq.urlopen(url, timeout=15) as r:
                resp = json.loads(r.read())
            for item in resp.get('items', []):
                vid = item.get('id', {}).get('videoId')
                if vid:
                    all_ids.add(vid)
        except Exception as e:
            search_errors.append(f"'{q}': {str(e)[:80]}")

    if not all_ids:
        msg = '검색 결과가 없습니다.'
        if search_errors:
            msg += ' (오류: ' + '; '.join(search_errors[:3]) + ')'
        return jsonify({'success': False, 'error': msg}), 500

    # 2) 영상 상세 정보 가져오기 (50개씩)
    videos = []
    ids_list = list(all_ids)
    for i in range(0, len(ids_list), 50):
        batch = ids_list[i:i + 50]
        try:
            url = (f'https://www.googleapis.com/youtube/v3/videos'
                   f'?part=snippet,contentDetails,statistics'
                   f'&id={",".join(batch)}&key={yt_key}')
            with ureq.urlopen(url, timeout=15) as r:
                resp = json.loads(r.read())
            for item in resp.get('items', []):
                videos.append({
                    'id': item['id'],
                    'title': item['snippet']['title'],
                    'channel': item['snippet']['channelTitle'],
                    'description': item['snippet']['description'],
                    'tags': [t.lower() for t in item['snippet'].get('tags', [])],
                    'duration': pl_parse_duration(item['contentDetails']['duration']),
                    'views': int(item['statistics'].get('viewCount', 0)),
                })
        except Exception as e:
            print(f'[playlist] videos.list error: {e}')

    # 3) 길이 + 태그 필터
    filtered = []
    tag_lower = tag_filter.lower() if tag_filter else ''
    for v in videos:
        if v['duration'] < min_duration:
            continue
        if tag_lower:
            has = (any(tag_lower in t for t in v['tags']) or
                   tag_lower in v['description'].lower() or
                   tag_lower in v['title'].lower())
            if not has:
                continue
        filtered.append(v)

    # 4) 트랙리스트 파싱 + 빈도 집계
    artist_counter = Counter()
    song_counter = Counter()
    track_data = []
    videos_with_tracks = 0

    for v in filtered:
        tracks = pl_parse_tracklist(v['description'])
        if not tracks:
            continue
        videos_with_tracks += 1
        for t in tracks:
            main = [pl_normalize_artist(a) for a in pl_split_artists(t['artist'])]
            feat = [pl_normalize_artist(a) for a in pl_extract_featured(t['title'])]
            for a in main + feat:
                artist_counter[a] += 1
            song_key = f"{main[0] if main else 'Unknown'} - {pl_normalize_title(t['title'])}"
            song_counter[song_key] += 1
            track_data.append({
                'video_id': v['id'],
                'video_title': v['title'],
                'channel': v['channel'],
                'timestamp': t['timestamp'],
                'artist_raw': t['artist'],
                'title_raw': t['title'],
                'normalized': song_key,
            })

    return jsonify({
        'success': True,
        'summary': {
            'queries': queries,
            'tag_filter': tag_filter,
            'min_duration_seconds': min_duration,
            'total_video_ids': len(all_ids),
            'videos_after_filter': len(filtered),
            'videos_with_tracklist': videos_with_tracks,
            'total_tracks': len(track_data),
        },
        'artist_top': [{'rank': i, 'artist': a, 'count': c}
                       for i, (a, c) in enumerate(artist_counter.most_common(30), 1)],
        'song_top': [{'rank': i, 'song': s, 'count': c}
                     for i, (s, c) in enumerate(song_counter.most_common(30), 1)],
        'tracks': track_data[:300],  # 응답 크기 제한
    })


@app.route('/api/job/<job_id>')
def job_status(job_id):
    return jsonify(job_store.get(job_id, {'status':'unknown'}))

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    print('\n✅ Studio App: http://localhost:5000\n')
    app.run(debug=False, port=5000)
