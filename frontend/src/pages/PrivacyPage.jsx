import { Link } from 'react-router-dom'

export default function PrivacyPage() {
  return (
    <main className="public-shell privacy-shell">
      <header className="public-header privacy-header">
        <div className="brand-mark">J</div>
        <p className="eyebrow">JJABTREE PRIVACY</p>
        <h1>짭트리 개인정보처리방침</h1>
        <p>
          짭트리는 인스타그램 댓글에 특정 키워드가 포함된 경우,
          해당 댓글 작성자에게 자동으로 다이렉트 메시지(DM)를 발송하는 서비스입니다.
        </p>
      </header>

      <article className="privacy-card">
        <section>
          <h2>수집하는 정보</h2>
          <ul>
            <li>댓글 작성자의 인스타그램 사용자 ID 및 사용자명</li>
            <li>댓글 내용</li>
            <li>발송된 DM 내용 및 발송 결과</li>
          </ul>
        </section>

        <section>
          <h2>이용 목적</h2>
          <p>댓글 키워드에 대응하는 자동 응답(DM) 제공</p>
        </section>

        <section>
          <h2>보관</h2>
          <p>
            수집된 정보는 서비스 운영 목적으로만 사용되며,
            제3자에게 제공되지 않습니다.
          </p>
        </section>

        <section>
          <h2>문의</h2>
          <p>문의사항은 인스타그램 계정 todaypicks19로 연락 바랍니다.</p>
        </section>
      </article>

      <Link className="privacy-back-link" to="/">
        상품 페이지로 돌아가기
      </Link>
    </main>
  )
}
