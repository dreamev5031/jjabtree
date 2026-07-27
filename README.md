# 짭트리 (jjabtree)

Instagram 릴스에 소개한 상품을 링크트리 형태의 공개 페이지에 노출하고, 특정 댓글 키워드가 감지되면 댓글 작성자에게 상품 번호가 포함된 비공개 답장(DM)을 보내는 독립 서비스입니다.

`insta-ad-generator`와 코드, API, 데이터베이스, 배포 구성을 공유하지 않습니다.

## 구성

- `backend/`: FastAPI + SQLite + Instagram Graph API, Railway 배포
- `frontend/`: Vite + React, Cloudflare Pages 배포
- 공개 페이지: `/`
- 관리자 페이지: `/admin`
- Instagram 웹훅: `/api/webhooks/instagram`

## 구현된 기능

### 관리자

- `X-App-Key` 기반 관리자 인증
- 연결된 Instagram 프로 계정의 최근 미디어 조회
- 썸네일, 게시일, 미디어 유형, permalink 그리드 표시
- 릴스 선택 후 상품 사진, 제품명, 구매링크, 댓글유도문구 등록
- 등록 상품 활성/비활성 전환
- 같은 Instagram media ID의 중복 등록 방지
- 상품 저장 시 계정의 `comments` 웹훅 구독을 best-effort로 요청

### 공개 링크페이지

- `status=active` 상품만 ID 오름차순 노출
- 사진, 상품 번호, 제품명, 구매링크 카드
- 구매링크 새 탭 열기
- 모바일 우선 반응형 UI

### 댓글 웹훅과 DM

- Meta 댓글 웹훅 검증 GET 처리
- 댓글 이벤트 POST 수신
- `ig_media_id`로 상품 매칭
- Unicode 정규화, 대소문자 무시, 연속 공백 정리 후 trigger phrase 포함 여부 확인
- 5개 템플릿 중 하나를 무작위 선택하고 `{번호}`를 상품 ID로 치환
- 댓글 ID 기준 중복 발송 방지
- DM 실패 시 `processed_comments`에 실패 로그를 남기고 서버는 계속 동작
- 현대식 `/{ig-user-id}/messages` private reply를 먼저 호출하고, 호환성을 위해 `/{comment-id}/private_replies` 방식으로 한 번 fallback
- `META_APP_SECRET` 설정 시 `X-Hub-Signature-256` 검증

## 데이터베이스

### `products`

| 컬럼 | 설명 |
|---|---|
| `id` | 자동 증가 상품 번호 |
| `product_name` | 제품명 |
| `purchase_link` | 구매링크 |
| `trigger_phrase` | DM 발동 댓글 문구 |
| `photo_url` | Railway Volume에 저장된 이미지 경로 |
| `ig_media_id` | 연결된 Instagram 미디어 ID, unique |
| `ig_permalink` | 연결된 릴스 URL |
| `created_at` | UTC ISO 8601 생성 시각 |
| `status` | `active` 또는 `inactive` |

### `processed_comments`

댓글 ID, 상품 ID, 작성자 정보, 댓글 내용, 선택된 DM 문구, 성공/실패 상태와 오류를 기록합니다. `comment_id`가 unique이므로 동일 웹훅이 재전송돼도 한 번만 처리합니다.

## 백엔드 환경변수

### 필수

| 이름 | 설명 |
|---|---|
| `IG_ACCESS_TOKEN` | Instagram Graph API 장기 액세스 토큰 |
| `IG_BUSINESS_ACCOUNT_ID` | Instagram 프로 계정 ID |
| `ADMIN_APP_KEY` | 관리자 페이지가 `X-App-Key`로 보내는 비밀키 |

### 기본값 또는 선택

| 이름 | 기본값 | 설명 |
|---|---|---|
| `DB_PATH` | `./data/jjabtree.db` | SQLite 경로. Railway에서는 `/data/jjabtree.db` 권장 |
| `UPLOAD_DIR` | DB 파일과 같은 폴더의 `uploads` | 업로드 이미지 저장 폴더. Railway에서는 `/data/uploads` 권장 |
| `IG_GRAPH_API_VERSION` | `v25.0` | Meta API 버전. Meta 대시보드에 맞춰 변경 가능 |
| `IG_GRAPH_API_BASE_URL` | `https://graph.facebook.com` | Facebook Login 기반 Instagram API 기본 호스트. Instagram Login 구성은 `https://graph.instagram.com` 사용 가능 |
| `META_WEBHOOK_VERIFY_TOKEN` | `ADMIN_APP_KEY` 값 | Meta 웹훅 등록 시 입력할 검증 토큰. 운영에서는 별도 랜덤값 권장 |
| `META_APP_SECRET` | 비어 있음 | 설정하면 웹훅 서명 검증 활성화 |
| `CORS_ORIGINS` | `*` | 쉼표 구분 허용 Origin. 운영에서는 Cloudflare Pages 도메인 지정 권장 |

필수 값이 없으면 서버가 시작되지 않고 Railway 로그에 누락된 환경변수 이름을 한국어로 출력합니다.

## 프론트엔드 환경변수

| 이름 | 설명 |
|---|---|
| `VITE_API_BASE_URL` | Railway 백엔드 공개 URL. 예: `https://jjabtree-production.up.railway.app` |

## 로컬 실행

### 백엔드

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
# .env 값을 셸 환경변수로 적용한 뒤 실행
uvicorn app.main:app --reload --port 8000
```

FastAPI 문서:

- `http://localhost:8000/docs`
- `http://localhost:8000/health`

테스트:

```bash
cd backend
python -m pytest -q
```

### 프론트엔드

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

- 공개 페이지: `http://localhost:5173/`
- 관리자 페이지: `http://localhost:5173/admin`

관리자 키는 브라우저 `sessionStorage`에만 저장되며 요청마다 `X-App-Key` 헤더로 전송됩니다.

## Meta for Developers 설정

이 프로젝트는 **개발 모드에서 앱 역할 또는 Instagram 테스터로 등록된 본인 계정만 사용한다는 전제**로 작성했습니다. 본인이 소유·관리하며 앱 대시보드 역할에 연결된 프로 계정은 Standard Access 범위에서 테스트할 수 있지만, 외부 일반 계정에 서비스하려면 필요한 권한의 Advanced Access와 App Review가 필요할 수 있습니다.

### 1. 계정 준비

1. Instagram 계정을 비즈니스 또는 크리에이터 계정으로 전환합니다.
2. 사용하는 로그인 방식에 따라 Facebook Page 연결이 필요한 경우 연결합니다.
3. Meta for Developers에서 새 앱을 만들고 Instagram API 기능을 추가합니다.
4. 앱의 역할 또는 Instagram 테스터에 사용할 계정을 추가합니다.
5. 초대받은 Instagram 계정에서 테스터 초대를 수락합니다.

### 2. 토큰과 권한

Meta 앱에서 사용하는 로그인 방식에 맞는 장기 토큰을 발급합니다.

Facebook Login 기반에서 일반적으로 필요한 권한 예:

- `instagram_basic`
- `instagram_manage_comments`
- `instagram_manage_messages`
- 계정 조회 과정에 필요한 `pages_show_list`, `pages_read_engagement`

Instagram Login 기반에서 표시되는 권한 이름 예:

- `instagram_business_basic`
- `instagram_business_manage_comments`
- `instagram_business_manage_messages`

Meta 앱 구성에 따라 실제 권한 이름과 토큰 호스트가 달라질 수 있습니다. 기본 코드는 `graph.facebook.com`을 사용하며, Instagram Login 토큰이라면 `IG_GRAPH_API_BASE_URL=https://graph.instagram.com`으로 바꿉니다.

### 3. 웹훅 등록

Railway 배포 도메인이 아래라고 가정합니다.

```text
https://YOUR-RAILWAY-DOMAIN
```

Meta 대시보드의 Instagram Webhooks callback URL:

```text
https://YOUR-RAILWAY-DOMAIN/api/webhooks/instagram
```

Verify Token:

```text
Railway의 META_WEBHOOK_VERIFY_TOKEN 값
```

`META_WEBHOOK_VERIFY_TOKEN`을 따로 설정하지 않았다면 `ADMIN_APP_KEY`와 같은 값을 입력할 수 있지만, 운영에서는 분리하는 편이 안전합니다.

구독 필드:

```text
comments
```

웹훅은 특정 릴스마다 따로 등록하는 구조가 아니라 **Instagram 프로 계정 단위로 comments 이벤트를 구독**합니다. 짭트리는 수신한 이벤트의 `media.id`를 `products.ig_media_id`와 매칭해 어느 상품인지 결정합니다. 상품 저장 API가 `/{ig-user-id}/subscribed_apps` 호출을 시도하지만, 앱 유형에 따라 Meta 대시보드에서 직접 구독해야 합니다.

### 4. 웹훅 검증 확인

브라우저 또는 curl로 아래처럼 테스트할 수 있습니다.

```bash
curl "https://YOUR-RAILWAY-DOMAIN/api/webhooks/instagram?hub.mode=subscribe&hub.verify_token=YOUR_VERIFY_TOKEN&hub.challenge=12345"
```

응답 본문이 `12345`이면 검증 엔드포인트가 정상입니다.

### 5. 개발용 댓글 이벤트 테스트

Meta 대시보드의 웹훅 테스트 기능을 사용하거나 아래 형태의 payload를 서버로 보낼 수 있습니다.

```json
{
  "object": "instagram",
  "entry": [
    {
      "id": "IG_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "field": "comments",
          "value": {
            "id": "COMMENT_ID",
            "text": "링크 주세요",
            "media": { "id": "REGISTERED_MEDIA_ID" },
            "from": { "id": "IG_SCOPED_USER_ID", "username": "tester" }
          }
        }
      ]
    }
  ]
}
```

`META_APP_SECRET`이 설정된 서버에 수동 요청을 보낼 때는 올바른 `X-Hub-Signature-256` 서명이 필요합니다.

### 6. Private Reply 제한 주의

Instagram의 댓글 기반 private reply는 일반적인 임의 DM 전송이 아닙니다.

- 댓글 ID를 수신자로 지정해 비공개 답장을 보냅니다.
- 댓글 생성 후 허용된 기간 안에 보내야 합니다.
- 동일 댓글에 보낼 수 있는 private reply 횟수와 후속 메시지 가능 범위는 Meta 정책을 따릅니다.
- 짭트리는 댓글 하나를 최초 처리할 때 한 번만 전송합니다.

## Railway 배포 설정

1. 새 Railway 프로젝트를 만들고 GitHub 저장소를 연결합니다.
2. 서비스 Root Directory를 `backend`로 지정합니다.
3. Railway Volume을 `/data`에 마운트합니다.
4. 환경변수를 설정합니다.

권장값:

```text
DB_PATH=/data/jjabtree.db
UPLOAD_DIR=/data/uploads
CORS_ORIGINS=https://YOUR-CLOUDFLARE-PAGES-DOMAIN
```

`backend/Dockerfile`과 `backend/railway.toml`이 준비되어 있습니다. 시작 명령은 Dockerfile의 아래 명령을 사용합니다.

```text
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Railway 서비스는 SQLite 파일과 업로드 이미지를 같은 `/data` Volume에 영구 보관합니다. Volume 없이 배포하면 재배포 시 데이터와 이미지가 사라질 수 있습니다.

## Cloudflare Pages 배포 설정

- Root Directory: `frontend`
- Build command: `npm run build`
- Build output directory: `dist`
- 환경변수: `VITE_API_BASE_URL=https://YOUR-RAILWAY-DOMAIN`

`frontend/public/_redirects`가 `/admin` 직접 접속과 새로고침을 `index.html`로 돌려 React Router가 처리하도록 구성합니다.

## API 엔드포인트

| Method | Path | 인증 | 용도 |
|---|---|---|---|
| GET | `/health` | 없음 | Railway health check |
| GET | `/api/public/products` | 없음 | 활성 상품 공개 목록 |
| GET | `/api/admin/media` | `X-App-Key` | 최근 Instagram 미디어 |
| GET | `/api/admin/products` | `X-App-Key` | 전체 상품 목록 |
| POST | `/api/admin/products` | `X-App-Key` | multipart 상품 등록 |
| PATCH | `/api/admin/products/{id}/status` | `X-App-Key` | 활성/비활성 변경 |
| GET | `/api/admin/dm-logs` | `X-App-Key` | 최근 DM 처리 로그 |
| GET | `/api/webhooks/instagram` | Meta verify token | 웹훅 검증 |
| POST | `/api/webhooks/instagram` | Meta signature 선택 | 댓글 이벤트 수신 |

## 보안 메모

- `ADMIN_APP_KEY`, Instagram 토큰, Meta App Secret을 프론트 빌드 변수에 넣지 마세요.
- 관리자 키는 서버의 Railway 환경변수에만 두고, 관리자가 `/admin`에서 직접 입력합니다.
- 운영 CORS는 `*` 대신 실제 Cloudflare Pages/custom domain만 허용하세요.
- `META_APP_SECRET`을 설정해 실제 Meta 웹훅 서명 검증을 활성화하는 것을 권장합니다.
- 구매링크는 관리자 입력값이므로 등록 전에 목적지를 확인하세요.

## 현재 범위 밖

- 여러 Instagram 계정 지원
- 관리자 사용자 계정/비밀번호/세션 서버
- 상품 수정 또는 삭제
- DM 재시도 큐
- 클릭 통계
- 맞춤 공개 프로필 정보
- 외부 사용자용 OAuth 연결

초기 버전은 한 개 Instagram 프로 계정, 한 명의 관리자, Railway 단일 인스턴스를 기준으로 합니다.
