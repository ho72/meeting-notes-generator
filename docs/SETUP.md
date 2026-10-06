# 실행과 환경 설정

[프로젝트 소개](../README.md) · [환경변수 예시](../.env.example)

## 준비와 시작

검증 환경은 Python 3.12.14입니다. PyAV 기반으로 음성을 디코딩하며, 전사 모델은 처음 사용하는 크기의 파일을 다운로드합니다. 테스트·화면 파일을 위해 실제 회의 자료를 준비할 필요는 없습니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 127.0.0.1 --port 8000
```

- 화면: `http://localhost:8000`
- 상태: `http://localhost:8000/health`
- API 문서: `http://localhost:8000/docs`

저장소 루트에서 실행합니다. `main.py`는 실제 앱을 가져오는 진입점이며 기능별 소스는 `meeting_notes/`에 있습니다. 재시작 시 저장한 프로젝트는 유지하지만, 실행 중인 작업을 복원하는 영속 작업 큐는 제공하지 않습니다. 프로세스 종료 시 현재 음성 처리가 끝나기를 기다립니다.

## 설정

| 항목 | 기본값·역할 |
| --- | --- |
| `WHISPER_MODEL_SIZE` | `medium`. 첫 시험에는 작은 `tiny` 또는 `base`로 시작 가능 |
| `WHISPER_DEVICE` | `auto`. CPU 환경은 `cpu`를 명시 가능 |
| `WHISPER_COMPUTE_TYPE` | `int8`. 자신의 장치와 모델 실행 환경에 맞게 설정 |
| `WHISPER_LANGUAGE` | `ko`. 영어 검증에는 `en` 사용 |
| `WHISPER_INITIAL_PROMPT` | 기본 한국어 회의 전사 안내. 언어를 바꾸면 프롬프트도 맞춤 |
| `DIARIZATION_NUM_SPEAKERS` | `0`: 자동 추정. 알고 있는 화자 수로 설정 가능 |
| `DIARIZATION_MIN_SPEAKERS`, `DIARIZATION_MAX_SPEAKERS` | 자동 추정 범위. 실제 구간 수와 점수 조건에 따라 더 적은 수로 처리 가능 |
| `MEETING_DATA_DIR` | 로컬 `data/`. 상대 경로는 서버 실행 디렉터리 기준 |
| `MAX_UPLOAD_SIZE_MB` | `500`. 업로드 파일 크기 제한 |
| `MAX_PENDING_JOBS` | `8`. 업로드 수신·처리·대기를 합친 최대 작업 수 |
| `JOB_TTL_SECONDS` | `1800`. 완료·실패 작업의 메모리 상태 보관 시간. 저장 프로젝트를 삭제하는 기간이 아님 |
| `GEMINI_API_KEY` | 자신의 제공자 키. 비어 있으면 전사·저장은 사용 가능하고 AI 생성은 503 반환 |
| `GEMINI_MODEL` | `gemini-3.8-flash`. 자신의 계정에서 사용 가능한 모델 ID |
| `GEMINI_MAX_OUTPUT_TOKENS` | `8192`. 출력이 한도로 중단되면 미완성 결과를 저장하지 않고 오류 표시 |
| `CORS_ORIGINS` | 기본 빈 값. 같은 서버의 화면에는 설정 불필요. 필요하면 명시적인 Origin을 쉼표로 구분 |

기존 기본 모델이던 Gemini 2.5 계열은 현재 신규 계정 접근에 제한이 안내되어 있어 공개 기본값을 조정했습니다. 제공자 모델은 변경될 수 있으므로 [공식 모델 제공·종료 안내](https://ai.google.dev/gemini-api/docs/deprecations)를 확인하고 자신의 환경에서 선택합니다. 실제 제공자 호출을 통과했다는 의미는 아닙니다.

작업 관리·이벤트 상태는 프로세스 메모리를 사용하므로 **uvicorn worker는 1개**로 실행합니다. 음성 처리도 공유 모델의 자원 사용을 제한하기 위해 한 번에 하나씩 진행합니다.

## 문서 생성·저장

- AI 입력은 화면의 현재 전사문입니다. 저장하지 않은 편집 내용도 생성 요청에 포함합니다.
- API에서 전사를 생략하면 저장한 편집 전사, 없으면 원래 전사 구간을 사용합니다.
- 같은 전사·모델·양식·출력 설정의 생성 결과는 디스크 캐시를 사용합니다.
- 자동 생성 캐시와 사람이 저장한 회의록은 별도 파일입니다. 새 생성 결과가 검토 완료 회의록을 자동으로 덮어쓰지 않습니다.
- 저장 프로젝트는 같은 `MEETING_DATA_DIR`를 사용하는 서버를 다시 시작해도 조회할 수 있습니다.

## 문제 해결

| 증상 | 확인 |
| --- | --- |
| 최초 분석이 느림 | 모델 다운로드·로딩 중인지 확인. 작은 모델로 먼저 시험 |
| 큐가 가득 참 / 503 | 기존 작업이 끝난 뒤 재시도. 큰 녹음 처리 자원과 대기 한도 확인 |
| AI 생성 / 503 | 실제 `.env`에서 키를 설정했는지 확인 |
| AI 생성 / 502·504 | 계정의 모델 접근·사용 한도·출력 한도·연결 상태 확인. 원래 provider 오류 본문은 브라우저에 노출하지 않음 |
| 결과에 화자가 잘못 붙음 | 녹음 품질·짧은 발화·실제 화자 수 확인. 자동 추정은 정확도 보장 기능이 아님 |
| 새 환경에서 오디오 디코딩 실패 | `requirements.txt`의 조합으로 설치. 검증한 PyAV 16.1.0을 유지 |

## 테스트

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
node --check static/app.js
node --test tests/download.test.cjs
python -m pytest -q
```

Node.js는 웹 JavaScript 문법 검사와 내보내기 테스트에 사용하며 앱 실행에 필수는 아닙니다. CI도 실제 개인 자료·API 키·Whisper 다운로드 없이 이 검사와 회귀 테스트를 수행합니다. 의존성을 바꾸면 모델 디코딩 경로까지 별도로 다시 확인합니다.
