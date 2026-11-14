import { ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Link, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { BuyerAdmin } from './components/mandate/MandateControls'
import { Main } from './pages/Main'
import { Approver } from './pages/Approver'
import { ApproveMobile } from './pages/ApproveMobile'
import { Log } from './pages/Log'
import { Spec } from './pages/Spec'
import { ValioView } from './pages/ValioView'
import { gateway, MOCK_STORE_KEY } from './lib/gateway'
import { adminHeaders, DEMO_ADMIN_STORAGE } from './lib/demoAccess'

function LogRoute() {
  const { proposalId } = useParams()
  return <Log proposalId={proposalId} />
}

export default function App() {
  const { pathname } = useLocation()
  const approver = pathname.startsWith('/approver') || pathname.startsWith('/approve/')
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState('')

  async function resetSession() {
    setResetting(true)
    setResetError('')
    try {
      await gateway.checkHealth()
      if (gateway.source === 'GATEWAY') {
        if (!sessionStorage.getItem(DEMO_ADMIN_STORAGE)) {
          throw new Error('Enter the admin key on the Buyer admin page before resetting.')
        }
        const response = await fetch('/v0.1/admin/demo/reset', { method: 'POST', headers: adminHeaders() })
        if (!response.ok) throw new Error('Could not reset your session. Please try again.')
      }
      localStorage.removeItem(MOCK_STORE_KEY)
      localStorage.removeItem('verified-or-ask.token-proposal-map.v1')
      window.location.assign('/')
    } catch (error) {
      setResetError(error instanceof Error ? error.message : 'Could not reset your session.')
      setResetting(false)
    }
  }
  return (
    <div className={`app ${approver ? 'app-approver' : ''}`}>
      <a href="#main" className="skip-link">
        {approver ? 'Skip to proposals for approval' : 'Skip to the purchasing flow'}
      </a>
      <header className="app-header">
        <Link to="/" className="brand" aria-label="Aimo Connect · Valio Aimo">
          <img src="/brand/valio-aimo-logo-white.svg" alt="Valio Aimo" className="brand-logo" />
        </Link>
        <nav className="app-nav" aria-label="Views">
          <Link
            to="/"
            aria-label={approver ? 'Buyer view' : 'Buy'}
            aria-current={pathname === '/' ? 'page' : undefined}
          >
            Buy
          </Link>
          {!approver && (
            /* The approver panel is a separate window, like a separate person's device. */
            <a href="/approver" target="_blank" rel="noopener" aria-label="Open Approver panel">
              Approve <ExternalLink size={12} aria-hidden />
            </a>
          )}
          <Link to="/log" aria-current={pathname.startsWith('/log') ? 'page' : undefined}>
            Log
          </Link>
          <Link
            to="/spec"
            aria-label="Open spec · Trust Labels v0.1"
            aria-current={pathname.startsWith('/spec') ? 'page' : undefined}
          >
            Spec
          </Link>
          <Link
            to="/valio"
            aria-label="Valio Aimo readiness"
            aria-current={
              pathname.startsWith('/valio') || pathname.startsWith('/readiness')
                ? 'page'
                : undefined
            }
          >
            Readiness
          </Link>
        </nav>
      </header>
      <main id="main">
        <Routes>
          <Route path="/" element={<Main />} />
          <Route path="/admin" element={<BuyerAdmin />} />
          <Route path="/valio" element={<ValioView />} />
          <Route path="/readiness" element={<ValioView />} />
          <Route path="/approver" element={<Approver />} />
          <Route path="/approve/:token" element={<ApproveMobile />} />
          <Route path="/log" element={<LogRoute />} />
          <Route path="/log/:proposalId" element={<LogRoute />} />
          <Route path="/spec" element={<Spec />} />
          <Route
            path="*"
            element={
              <p className="panel">
                Page not found. <Link to="/">Back to the buyer view</Link>
              </p>
            }
          />
        </Routes>
      </main>
      {!pathname.startsWith('/approve/') && (
        <footer className="app-footer">
          <span>
            Private session ·{' '}
            <button type="button" onClick={resetSession} disabled={resetting}>
              {resetting ? 'Resetting…' : 'Reset'}
            </button>
            {resetError && <span role="alert"> {resetError}</span>}
          </span>
        </footer>
      )}
    </div>
  )
}
