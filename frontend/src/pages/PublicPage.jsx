import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest } from '../api'
import Loading from '../components/Loading'

const PAGE_SIZE = 10

export default function PublicPage() {
  const [products, setProducts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)

  useEffect(() => {
    apiRequest('/api/public/products')
      .then((data) => {
        setProducts(data.products || [])
        setPage(1)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const pageCount = Math.max(1, Math.ceil(products.length / PAGE_SIZE))
  const visibleProducts = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE
    return products.slice(start, start + PAGE_SIZE)
  }, [page, products])

  useEffect(() => {
    if (page > pageCount) setPage(pageCount)
  }, [page, pageCount])

  function goToPage(nextPage) {
    const safePage = Math.min(Math.max(nextPage, 1), pageCount)
    setPage(safePage)
    window.requestAnimationFrame(() => {
      document.querySelector('.product-list')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  return (
    <main className="public-shell todaypicks-shell">
      <header className="public-header todaypicks-header">
        <img className="brand-logo" src="/todaypicks-logo.webp" alt="오늘픽스" />
        <p className="eyebrow">TODAYPICKS</p>
        <h1>오늘픽스</h1>
        <p>릴스에서 소개한 상품을 번호로 빠르게 확인하세요.</p>
      </header>

      <aside className="affiliate-disclosure" role="note" aria-label="쿠팡 파트너스 수수료 안내">
        이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.
      </aside>

      {loading && <Loading label="상품을 불러오는 중..." />}
      {error && <div className="notice error">{error}</div>}
      {!loading && !error && products.length === 0 && (
        <div className="empty-state">아직 등록된 상품이 없습니다.</div>
      )}

      {!loading && !error && products.length > 0 && (
        <>
          <section className="product-list" aria-label="상품 목록">
            {visibleProducts.map((product) => (
              <article className="product-link-card" key={product.id}>
                <img
                  className="product-thumbnail"
                  src={product.photo_url}
                  alt={product.product_name}
                  loading="lazy"
                />
                <span className="product-number" aria-label={`${formatProductNumber(product.id)}번`}>
                  {formatProductNumber(product.id)}
                </span>
                <div className="product-summary">
                  <strong>{product.product_name}</strong>
                  <span>오늘픽스 추천 상품</span>
                </div>
                <a
                  className="product-link-button"
                  href={product.purchase_link}
                  target="_blank"
                  rel="noreferrer noopener sponsored"
                  aria-label={`${product.product_name} 구매링크 열기`}
                >
                  보기 <span aria-hidden="true">↗</span>
                </a>
              </article>
            ))}
          </section>

          {pageCount > 1 && (
            <nav className="pagination" aria-label="상품 페이지 이동">
              <button
                type="button"
                className="pagination-arrow"
                onClick={() => goToPage(page - 1)}
                disabled={page === 1}
              >
                이전
              </button>
              <div className="pagination-pages">
                {Array.from({ length: pageCount }, (_, index) => index + 1).map((pageNumber) => (
                  <button
                    type="button"
                    className={`pagination-page ${pageNumber === page ? 'active' : ''}`}
                    aria-current={pageNumber === page ? 'page' : undefined}
                    onClick={() => goToPage(pageNumber)}
                    key={pageNumber}
                  >
                    {pageNumber}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="pagination-arrow"
                onClick={() => goToPage(page + 1)}
                disabled={page === pageCount}
              >
                다음
              </button>
            </nav>
          )}
        </>
      )}

      <footer className="public-footer">
        <span>오늘픽스 · Instagram product links</span>
        <Link to="/privacy">개인정보처리방침</Link>
        <Link to="/admin">운영자 로그인</Link>
      </footer>
    </main>
  )
}

function formatProductNumber(id) {
  return String(id).padStart(3, '0')
}
