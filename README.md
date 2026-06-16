# 🎙️ Studio Hub

> AI 기반 음악 제작 & 유튜브 콘텐츠 스튜디오 — 가사 생성부터 채널 최적화까지 한 곳에서

---

## 개발 배경 & 페인포인트

### 왜 만들었는가

유튜브 음악 콘텐츠 제작자나 인디 뮤지션이 겪는 반복적인 작업들이 있습니다.

- **가사 작성 병목**: 좋은 멜로디가 있어도 가사를 쓰는 데 시간이 오래 걸립니다. 특히 영어·일본어 등 외국어 가사를 쓰거나, 한국어 가사를 다국어로 번역하는 작업은 별도 도구를 열어야 해 흐름이 끊깁니다.
- **트랙 분석 수작업**: BPM·조성·에너지를 알아야 Suno AI나 Udio 같은 생성형 음악 도구에 정확한 프롬프트를 넣을 수 있는데, 이를 일일이 DAW나 온라인 툴로 분석해야 했습니다.
- **유튜브 메타데이터 비효율**: 제목·설명·태그를 SEO에 맞게 최적화하는 작업은 반복적이고 시간이 많이 걸립니다. 별도 툴이나 ChatGPT 탭을 오가며 복붙하는 과정이 번거롭습니다.
- **채널 분석 파편화**: 구독자 성장 지표, 트렌드 키워드, 플레이리스트 분위기 분석을 각각 다른 서비스에서 확인해야 했습니다.
- **쇼츠 제작 도구 부재**: 유튜브 쇼츠에 맞는 장면별 연출 프롬프트를 AI가 자동 생성해주는 개인화 도구가 없었습니다.

### 해결 방향

음악 제작 워크플로에 필요한 모든 AI 기능을 **하나의 웹 대시보드**에 통합합니다. YouTube URL 하나 또는 MP3 파일 하나를 업로드하면, 트랙 분석 → 가사 생성 → 다국어 번역 → 메타데이터 최적화까지 한 흐름으로 처리할 수 있도록 설계했습니다.

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

| 영역 | 기술 | 선택 이유 |
|------|------|-----------|
| 백엔드 | Python 3.10+, Flask 3.0 | 빠른 프로토타이핑과 AI SDK 통합에 최적. 단일 프로세스로 모든 기능을 서빙 가능 |
| 오디오 처리 | yt-dlp, FFmpeg, librosa, mutagen | yt-dlp는 YouTube 다운로드 de facto 표준. librosa는 Python에서 BPM·조성·스펙트럼 분석을 가장 쉽게 구현할 수 있는 라이브러리 |
| AI / LLM | Anthropic Claude, Google Gemini 2.5 Flash, Groq (Qwen3-32B) | 태스크별 최적 모델 사용. 오디오 분석은 Gemini의 멀티모달 능력, 가사 생성은 Groq의 빠른 추론 속도, 일반 텍스트 작업은 Claude 활용 |
| 외부 API | YouTube Data API v3, Google Trends, cobalt.tools | 각 플랫폼 공식 API만 사용해 안정성·합법성 확보 |
| 보안 | Rate Limiting, Session 인증, HttpOnly 쿠키 | 개인 운영 도구이지만 API 키 노출·무단 사용 방지를 위해 기본 보안 레이어 적용 |
| 네트워킹 | requests, PySocks (SOCKS5 프록시) | 지역 제한 콘텐츠 접근 및 IP 우회를 위한 프록시 지원 |

### 기술 선택 상세 과정

#### Flask vs FastAPI

FastAPI의 자동 문서화·타입 검증은 매력적이지만, 이 프로젝트는 **단일 사용자 개인 도구**에 가깝습니다. 빠른 기능 추가와 Jinja2 템플릿 기반 UI를 그대로 활용할 수 있는 Flask가 더 실용적이었습니다. AI SDK 호출은 대부분 동기 방식으로도 충분한 응답 속도가 나왔습니다.

#### yt-dlp + cobalt.tools 이중 폴백

YouTube는 지속적으로 다운로드 방지 정책을 업데이트합니다. yt-dlp 단독 사용 시 특정 환경(IP, 쿠키 만료 등)에서 실패할 수 있어, cobalt.tools API를 2차 폴백으로 두었습니다. 이 구조 덕분에 다운로드 성공률이 크게 높아졌습니다.

#### librosa 트랙 분석

BPM·조성·에너지 분석은 librosa 하나로 커버할 수 있습니다. 여기에 Gemini 2.5 Flash의 오디오 직접 청취 기능을 결합해, 수치 분석으로는 잡기 어려운 "장르 느낌"이나 "보컬 타입" 같은 주관적 요소를 AI가 보완합니다.

---

## AI 모델 선택 과정

### 태스크별 모델 분리 전략

단일 모델로 모든 기능을 처리하는 대신, **태스크의 특성에 맞게 모델을 분리**했습니다.

| 태스크 | 현재 모델 | 선택 이유 |
|--------|-----------|-----------|
| 오디오 직접 분석 (장르·분위기) | Gemini 2.5 Flash | 현재 가장 뛰어난 오디오 멀티모달 이해 능력 |
| 가사 생성 | Groq / Qwen3-32B | Groq 인퍼런스 서버의 빠른 토큰 생성 속도. 창작 태스크에서 품질도 충분 |
| 가사 번역 (11개 언어) | Groq / Qwen3-32B (무료 플랜) | ⚠️ **현재 무료 모델 사용으로 성능 제한 있음** — 아래 항목 참고 |
| 메타데이터 최적화·기타 텍스트 | Anthropic Claude | 한국어 SEO 문구 생성과 구조적 텍스트 작업에서 품질 우수 |

### ⚠️ 가사 번역 모델 교체 예정

현재 가사 번역(11개 언어)은 **Groq 무료 플랜의 Qwen3-32B**로 처리하고 있습니다. 무료 플랜 특성상 아래 한계가 있습니다.

- RPM(분당 요청 수) 제한으로 다중 언어 동시 번역 시 지연 또는 실패 발생
- 일부 언어(특히 베트남어, 중국어 번체)에서 번역 품질이 기대에 못 미침
- 무료 할당량 초과 시 서비스 중단

**교체 계획**: Gemini API 또는 Claude API로 전환 예정입니다.

- **Gemini 2.5 Flash**: 다국어 번역 품질이 우수하고, Google AI Studio 무료 크레딧으로 초기 운영 비용 절감 가능. 오디오 분석과 동일 SDK를 사용해 코드 통일성도 높아짐
- **Claude Haiku 4.5**: 한국어 ↔ 외국어 번역에서 자연스러운 문체 유지. 비용은 클러스터당 매우 저렴하며, 이미 메타데이터 최적화에 사용 중인 Anthropic SDK를 재사용 가능

두 후보 모두 현재 Groq 무료 플랜 대비 번역 품질이 높고, API 안정성이 더 좋습니다. 최종 선택은 번역 품질 A/B 테스트 후 결정할 예정입니다.

### Gemini 2.5 Flash 오디오 분석

오디오 분석 태스크에서 Gemini를 선택한 이유는 단순합니다. 현재 **오디오 파일을 직접 입력으로 받아 장르·분위기·보컬 타입을 분류**할 수 있는 모델이 Gemini 2.5 Flash뿐입니다. GPT-4o의 오디오 모드는 실시간 음성 위주이고, Claude는 현재 오디오 직접 입력을 지원하지 않습니다.

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
ANTHROPIC_API_KEY=sk-ant-...   # Anthropic 콘솔에서 발급
YOUTUBE_API_KEY=AIzaSy...      # Google Cloud Console에서 발급
FLASK_SECRET_KEY=              # 아래 명령으로 생성
```

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. 앱 실행

```bash
python app.py
```

브라우저에서 `http://localhost:5000` 접속 후 초기 계정(admin / 콘솔에 출력된 임시 비밀번호)으로 로그인합니다.

> ⚠️ **보안 주의**: 첫 로그인 후 설정 페이지에서 반드시 비밀번호를 변경하세요.

### Windows 원클릭 실행

```batch
run.bat
```

---

## 🔐 보안

- **로그인 브루트포스 방어**: 10회/분 Rate Limit
- **AI API 엔드포인트**: 20~30회/시 Rate Limit
- **파일 다운로드**: Path Traversal 방어 (realpath 검증)
- **파일 업로드**: secure_filename + 랜덤 저장명 + 200MB 크기 제한
- **세션 쿠키**: HttpOnly, SameSite=Lax, 8시간 만료

---

## 📁 프로젝트 구조

```
studio-hub/
├── app.py               # Flask 앱 진입점 (라우팅, 비즈니스 로직)
├── requirements.txt     # Python 의존성
├── .env.example         # 환경변수 템플릿
├── run.bat              # Windows 원클릭 실행 스크립트
├── templates/
│   ├── index.html       # 메인 대시보드 UI
│   └── login.html       # 로그인 페이지
├── downloads/           # 다운로드된 오디오 파일 (gitignore)
├── ffmpeg_bin/          # FFmpeg 바이너리 (gitignore)
└── data/
    └── settings.json    # 런타임 설정 저장소 (gitignore)
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

MIT License — 자세한 내용은 LICENSE 파일을 참조하세요.
