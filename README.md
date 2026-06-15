# 🎙️ Studio Hub

> AI 기반 음악 제작 & 유튜브 콘텐츠 스튜디오 — 가사 생성부터 채널 최적화까지 한 곳에서

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0+-000000?logo=flask)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## ✨ 주요 기능

### 🎵 오디오 & 다운로드
- **YouTube 오디오 추출** — yt-dlp + cobalt.tools 이중 폴백 구조로 안정적인 다운로드
- **OAuth2 / 쿠키 인증** — 프리미엄 콘텐츠 접근을 위한 YouTube 로그인 지원
- **FFmpeg 자동 설치** — Windows 환경에서 최초 실행 시 FFmpeg 바이너리 자동 설치
- **MP3 업로드** — 로컬 파일 업로드 (최대 200MB, 크기 검증 포함)

### 🧠 AI 트랙 분석
- **librosa 특성 추출** — BPM, 에너지, 조성(key) 자동 분석
- **Gemini 2.5 Flash 오디오 분석** — AI가 오디오를 직접 청취해 장르·분위기·보컬 타입 분류
- **Suno 태그 & 프롬프트 생성** — 분석 결과를 Suno AI 입력 포맷으로 자동 변환

### ✍️ 다국어 가사 생성
- **AI 작사** — Groq(Qwen3-32B)으로 장르·분위기·테마 기반 가사 생성
- **11개 언어 번역** — 한국어·영어·일본어·중국어(번체)·베트남어 등 자동 번역
- **언어 순도 검증** — 한국어 가사에 외국 문자 혼입 시 자동 재시도 (최대 3회)

### 📊 채널 & 트렌드 분석
- **YouTube 채널 분석** — 구독자 수·조회수·성장 지표·콘텐츠 패턴 리포트
- **Google 트렌드** — 실시간 트렌드 + 키워드별 지역 관심도 데이터
- **플레이리스트 분석** — 감정·장르·바이브 프리셋 기반 YouTube 플레이리스트 분석

### 🎬 쇼츠 & 영상 제작
- **YouTube 메타데이터 최적화** — 제목·설명·태그 AI 보정 (SEO 최적화)
- **쇼츠 스튜디오 프롬프트** — 장면 단위 시각 연출 프롬프트 자동 생성
- **자막 재분배** — 자막 줄 길이를 자동으로 균등 재배치

---

## 🛠️ 기술 스택

| 영역 | 기술 |
|------|------|
| **백엔드** | Python 3.10+, Flask 3.0, Flask-CORS, Flask-Limiter |
| **오디오 처리** | yt-dlp, FFmpeg, librosa, mutagen, numpy |
| **AI / LLM** | Anthropic Claude, Google Gemini 2.5 Flash, Groq (Qwen3-32B) |
| **외부 API** | YouTube Data API v3, Google Trends, cobalt.tools |
| **보안** | Rate Limiting, Session 인증, HttpOnly 쿠키, Path Traversal 방어 |
| **네트워킹** | requests, PySocks (SOCKS5 프록시) |

---

## 🚀 설치 및 실행

### 1. 저장소 클론

```bash
git clone https://github.com/goldmireu-source/studio-hub.git
cd studio-hub
```

### 2. 가상환경 생성 및 의존성 설치

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열어 아래 값을 입력합니다:

```env
ANTHROPIC_API_KEY=sk-ant-...          # Anthropic 콘솔에서 발급
YOUTUBE_API_KEY=AIzaSy...             # Google Cloud Console에서 발급
FLASK_SECRET_KEY=                     # 아래 명령으로 생성
```

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. 앱 실행

```bash
python app.py
```

브라우저에서 `http://localhost:5000` 접속 후 초기 계정(`admin` / 콘솔에 출력된 임시 비밀번호)으로 로그인합니다.

> **⚠️ 보안 주의**: 첫 로그인 후 설정 페이지에서 반드시 비밀번호를 변경하세요.

### Windows 원클릭 실행

```bash
run.bat
```

---

## ⚙️ API 엔드포인트 요약

<details>
<summary>펼치기</summary>

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/yt/download` | YouTube 오디오 다운로드 (비동기) |
| GET | `/api/yt/progress/<job_id>` | 다운로드 진행 상태 조회 |
| POST | `/api/analyze/track` | 오디오 트랙 AI 분석 |
| POST | `/api/lyrics/generate` | 다국어 가사 생성 |
| POST | `/api/suno/tags` | Suno AI 태그 생성 |
| POST | `/api/suno/prompt` | Suno AI 프롬프트 생성 |
| POST | `/api/channel/analyze` | YouTube 채널 분석 |
| POST | `/api/trends` | Google 트렌드 조회 |
| POST | `/api/youtube/optimize` | 유튜브 메타데이터 최적화 |
| POST | `/api/prompts/scenes` | 쇼츠 장면 프롬프트 생성 |
| POST | `/api/subtitle/redistribute` | 자막 줄 재분배 |
| POST | `/api/translate` | 텍스트 번역 |
| POST | `/login` | 로그인 (10회/분 제한) |

</details>

---

## 🔐 보안

- 로그인 브루트포스 방어: **10회/분** Rate Limit
- AI API 엔드포인트: **20~30회/시** Rate Limit
- 파일 다운로드: Path Traversal 방어 (`realpath` 검증)
- 파일 업로드: `secure_filename` + 랜덤 저장명 + 200MB 크기 제한
- 세션 쿠키: `HttpOnly`, `SameSite=Lax`, **8시간** 만료

---

## 📁 프로젝트 구조

```
studio-hub/
├── app.py              # Flask 앱 진입점 (라우팅, 비즈니스 로직)
├── requirements.txt    # Python 의존성
├── .env.example        # 환경변수 템플릿
├── run.bat             # Windows 원클릭 실행 스크립트
├── templates/
│   ├── index.html      # 메인 대시보드 UI
│   └── login.html      # 로그인 페이지
├── downloads/          # 다운로드된 오디오 파일 (gitignore)
├── ffmpeg_bin/         # FFmpeg 바이너리 (gitignore)
└── data/
    └── settings.json   # 런타임 설정 저장소 (gitignore)
```

---

## 📋 필수 조건

| 항목 | 요구사항 |
|------|----------|
| Python | 3.10 이상 |
| Anthropic API Key | [console.anthropic.com](https://console.anthropic.com) |
| YouTube Data API Key | [Google Cloud Console](https://console.cloud.google.com) |
| Groq API Key | [console.groq.com](https://console.groq.com) (설정 페이지에서 입력) |
| Gemini API Key | [aistudio.google.com](https://aistudio.google.com) (설정 페이지에서 입력) |

---

## 📄 라이선스

MIT License — 자세한 내용은 [LICENSE](LICENSE) 파일을 참조하세요.
