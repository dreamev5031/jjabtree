import { useMemo, useState } from 'react'
import { adminHeaders, apiRequest } from '../api'
import Loading from '../components/Loading'

const initialForm = {
  product_name: '',
  purchase_link: '',
  trigger_phrase: '',
}

export default function AdminPage() {
  const [appKey, setAppKey] = useState(() => sessionStorage.getItem('jjabtree_admin_key') || '')
  const [draftKey, setDraftKey] = useState(appKey)
  const [media, setMedia] = useState([])
  const [products, setProducts] = useState([])
  const [selected, setSelected] = useState(null)
  const [form, setForm] = useState(initialForm)
  const [photo, setPhoto] = useState(null)
  const [loadingMedia, setLoadingMedia] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deletingProductId, setDeletingProductId] = useState(null)
  const [checkingProductId, setCheckingProductId] = useState(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const isAuthenticated = Boolean(appKey)
  const photoPreview = useMemo(() => (photo ? URL.createObjectURL(photo) : ''), [photo])

  function login(event) {
    event.preventDefault()
    const next = draftKey.trim()
    if (!next) return
    sessionStorage.setItem('jjabtree_admin_key', next)
    setAppKey(next)
    setError('')
    loadProducts(next)
  }

  function logout() {
    sessionStorage.removeItem('jjabtree_admin_key')
    setAppKey('')
    setDraftKey('')
    setProducts([])
    setMedia([])
  }

  async function loadMedia() {
    setLoadingMedia(true)
    setError('')
    setMessage('')
    try {
      const data = await apiRequest('/api/admin/media?limit=30', {
        headers: adminHeaders(appKey),
      })
      setMedia(data.media || [])
      if (!data.media?.length) setMessage('Instagram에서 불러온 게시물이 없습니다.')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoadingMedia(false)
    }
  }

  async function loadProducts(key = appKey) {
    try {
      const data = await apiRequest('/api/admin/products', {
        headers: adminHeaders(key),
      })
      setProducts(data.products || [])
    } catch (err) {
      setError(err.message)
    }
  }

  async function submitProduct(event) {
    event.preventDefault()
    if (!selected) {
      setError('먼저 Instagram 게시물을 선택하세요.')
      return
    }
    if (!photo) {
      setError('공개 링크페이지에 사용할 상품 사진을 업로드하세요.')
      return
    }

    const body = new FormData()
    body.append('product_name', form.product_name)
    body.append('purchase_link', form.purchase_link)
    body.append('trigger_phrase', form.trigger_phrase)
    body.append('ig_media_id', selected.id)
    body.append('ig_permalink', selected.permalink)
    body.append('photo', photo)

    setSaving(true)
    setError('')
    setMessage('')
    try {
      const data = await apiRequest('/api/admin/products', {
        method: 'POST',
        headers: adminHeaders(appKey),
        body,
      })
      setMessage(
        data.webhook_subscription?.ok
          ? `${data.product.id}번 상품이 저장됐습니다.`
          : `${data.product.id}번 상품은 저장됐지만 웹훅 구독 요청은 실패했습니다. README 설정을 확인하세요.`,
      )
      setForm(initialForm)
      setPhoto(null)
      setSelected(null)
      await loadProducts()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function toggleStatus(product) {
    const nextStatus = product.status === 'active' ? 'inactive' : 'active'
    setError('')
    setMessage('')
    try {
      await apiRequest(`/api/admin/products/${product.id}/status`, {
        method: 'PATCH',
        headers: adminHeaders(appKey, { 'Content-Type': 'application/json' }),
        body: JSON.stringify({ status: nextStatus }),
      })
      await loadProducts()
    } catch (err) {
      setError(err.message)
    }
  }

  async function checkMedia(product) {
    setCheckingProductId(product.id)
    setError('')
    setMessage('')
    try {
      const data = await apiRequest(`/api/admin/products/${product.id}/media-check`, {
        method: 'POST',
        headers: adminHeaders(appKey),
      })
      setProducts((current) => current.map((item) => (
        item.id === product.id ? data.product : item
      )))

      if (data.check.status === 'missing') {
        setMessage(`${product.id}번 상품의 연결된 릴스가 삭제된 것으로 의심됩니다. 자동 숨김은 하지 않았습니다.`)
      } else if (data.check.status === 'ok') {
        setMessage(`${product.id}번 상품의 연결된 릴스가 정상적으로 확인됐습니다.`)
      } else {
        setError(`릴스 상태를 확인하지 못했습니다. 기존 상태는 유지됩니다. ${data.check.detail || ''}`.trim())
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setCheckingProductId(null)
    }
  }

  async function deleteProduct(product) {
    const confirmed = window.confirm(
      `“${product.product_name}” 상품을 정말 삭제하시겠습니까?\n댓글 및 DM 이력은 그대로 보존됩니다.`,
    )
    if (!confirmed) return

    setDeletingProductId(product.id)
    setError('')
    setMessage('')
    try {
      await apiRequest(`/api/admin/products/${product.id}`, {
        method: 'DELETE',
        headers: adminHeaders(appKey),
      })
      setProducts((current) => current.filter((item) => item.id !== product.id))
      setMessage(`${product.id}번 상품이 삭제됐습니다. 기존 댓글 및 DM 이력은 유지됩니다.`)
    } catch (err) {
      setError(err.message)
    } finally {
      setDeletingProductId(null)
    }
  }

  if (!isAuthenticated) {
    return (
      <main className="admin-login-shell">
        <form className="login-card" onSubmit={login}>
          <p className="eyebrow">JJABTREE ADMIN</p>
          <h1>관리자 인증</h1>
          <p>Railway의 ADMIN_APP_KEY 값을 입력하세요.</p>
          <input
            type="password"
            value={draftKey}
            onChange={(event) => setDraftKey(event.target.value)}
            placeholder="ADMIN_APP_KEY"
            autoComplete="current-password"
          />
          <button className="primary-button" type="submit">관리자 화면 열기</button>
          {error && <div className="notice error">{error}</div>}
        </form>
      </main>
    )
  }

  return (
    <main className="admin-shell">
      <header className="admin-header">
        <div>
          <p className="eyebrow">JJABTREE ADMIN</p>
          <h1>상품 등록</h1>
        </div>
        <button className="text-button" type="button" onClick={logout}>로그아웃</button>
      </header>

      {message && <div className="notice success">{message}</div>}
      {error && <div className="notice error">{error}</div>}

      <section className="admin-panel">
        <div className="section-heading">
          <div>
            <span className="step">1</span>
            <h2>릴스 선택</h2>
          </div>
          <button className="secondary-button" type="button" onClick={loadMedia} disabled={loadingMedia}>
            최근 게시물 불러오기
          </button>
        </div>

        {loadingMedia && <Loading label="Instagram 게시물을 불러오는 중..." />}
        <div className="media-grid">
          {media.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`media-card ${selected?.id === item.id ? 'selected' : ''}`}
              onClick={() => setSelected(item)}
            >
              <div className="media-image">
                {item.image_url ? <img src={item.image_url} alt="Instagram 게시물 썸네일" /> : <span>이미지 없음</span>}
                {selected?.id === item.id && <span className="selected-check">✓</span>}
              </div>
              <strong>{item.media_type}</strong>
              <time>{formatDate(item.timestamp)}</time>
              <span className="truncate">{item.caption || '캡션 없음'}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="admin-panel">
        <div className="section-heading">
          <div>
            <span className="step">2</span>
            <h2>상품 정보 입력</h2>
          </div>
        </div>

        {selected && (
          <div className="selected-media-summary">
            <strong>선택된 게시물</strong>
            <a href={selected.permalink} target="_blank" rel="noreferrer">Instagram에서 보기 ↗</a>
          </div>
        )}

        <form className="product-form" onSubmit={submitProduct}>
          <label>
            <span>상품 사진</span>
            <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={(event) => setPhoto(event.target.files?.[0] || null)} />
          </label>
          {photoPreview && <img className="photo-preview" src={photoPreview} alt="업로드 미리보기" />}
          <label>
            <span>제품명</span>
            <input required maxLength="200" value={form.product_name} onChange={(event) => setForm({ ...form, product_name: event.target.value })} placeholder="예: 미니 무선 청소기" />
          </label>
          <label>
            <span>구매링크</span>
            <input required type="url" value={form.purchase_link} onChange={(event) => setForm({ ...form, purchase_link: event.target.value })} placeholder="https://..." />
          </label>
          <label>
            <span>댓글유도문구</span>
            <input required maxLength="100" value={form.trigger_phrase} onChange={(event) => setForm({ ...form, trigger_phrase: event.target.value })} placeholder="예: 링크" />
            <small>댓글에 이 문구가 포함되면 DM을 보냅니다. 대소문자와 연속 공백은 무시합니다.</small>
          </label>
          <button className="primary-button" type="submit" disabled={saving}>
            {saving ? '저장 중...' : '상품 저장'}
          </button>
        </form>
      </section>

      <section className="admin-panel">
        <div className="section-heading">
          <div>
            <span className="step">3</span>
            <h2>등록 상품</h2>
          </div>
          <button className="text-button" type="button" onClick={() => loadProducts()}>새로고침</button>
        </div>
        <div className="admin-products">
          {products.map((product) => {
            const isBusy = deletingProductId === product.id || checkingProductId === product.id
            return (
              <article className={`admin-product-row ${product.media_check_status === 'missing' ? 'media-missing' : ''}`} key={product.id}>
                <img src={product.photo_url} alt="" />
                <div className="admin-product-info">
                  <strong><span className="inline-number">{product.id}</span>{product.product_name}</strong>
                  <a href={product.ig_permalink} target="_blank" rel="noreferrer">연결된 릴스 ↗</a>
                  <span>트리거: “{product.trigger_phrase}”</span>
                  {product.media_check_status === 'missing' && (
                    <span className="media-missing-badge">⚠ 연결된 릴스가 삭제된 것 같아요</span>
                  )}
                  <span className={`media-check-meta ${product.media_check_status || 'unchecked'}`}>
                    릴스 확인: {mediaCheckLabel(product.media_check_status)}
                    {product.media_checked_at ? ` · ${formatDateTime(product.media_checked_at)}` : ''}
                  </span>
                </div>
                <div className="admin-product-actions">
                  <button
                    className="media-check-button"
                    type="button"
                    onClick={() => checkMedia(product)}
                    disabled={isBusy}
                  >
                    {checkingProductId === product.id ? '확인 중' : '지금 확인'}
                  </button>
                  <button
                    className={`status-button ${product.status}`}
                    type="button"
                    onClick={() => toggleStatus(product)}
                    disabled={isBusy}
                  >
                    {product.status === 'active' ? '노출 중' : '숨김'}
                  </button>
                  <button
                    className="delete-button"
                    type="button"
                    onClick={() => deleteProduct(product)}
                    disabled={isBusy}
                  >
                    {deletingProductId === product.id ? '삭제 중' : '삭제'}
                  </button>
                </div>
              </article>
            )
          })}
          {!products.length && <div className="empty-state compact">등록된 상품이 없습니다.</div>}
        </div>
      </section>
    </main>
  )
}

function formatDate(value) {
  if (!value) return '날짜 없음'
  return new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(new Date(value))
}

function formatDateTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function mediaCheckLabel(status) {
  if (status === 'ok') return '정상'
  if (status === 'missing') return '삭제 의심'
  return '미확인'
}