import { useEffect, useState } from 'react'
import { apiRequest } from '../api'
import Loading from '../components/Loading'

export default function PublicPage() {
  const [products, setProducts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    apiRequest('/api/public/products')
      .then((data) => setProducts(data.products || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <main className="public-shell">
      <header className="public-header">
        <img className="brand-logo" src="/todaypicks-logo.webp" alt="오늘픽스" />
        <p className="eyebrow">JJABTREE</p>
        <h1>영상 속 상품 모아보기</h1>
        <p>릴스에서 안내받은 번호의 상품을 눌러 확인하세요.</p>
      </header>

      {loading && <Loading label="상품을 불러오는 중..." />}
      {error && <div className="notice error">{error}</div>}
      {!loading && !error && products.length === 0 && (
        <div className="empty-state">아직 등록된 상품이 없습니다.</div>
      )}

      <section className="product-grid" aria-label="상품 목록">
        {products.map((product) => (
          <a
            className="product-card"
            href={product.purchase_link}
            target="_blank"
            rel="noreferrer noopener sponsored"
            key={product.id}
          >
            <div className="image-wrap">
              <img src={product.photo_url} alt={product.product_name} loading="lazy" />
              <span className="number-badge">{product.id}</span>
            </div>
            <div className="product-copy">
              <strong>{product.product_name}</strong>
              <span>상품 보러가기 <span aria-hidden="true">↗</span></span>
            </div>
          </a>
        ))}
      </section>

      <footer className="public-footer">JJABTREE · Instagram product links</footer>
    </main>
  )
}
