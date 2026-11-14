import { ExternalLink, ShieldCheck, UserRoundCheck } from 'lucide-react'
import { Link, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { Main } from './pages/Main'
import { Approver } from './pages/Approver'
import { ApproveMobile } from './pages/ApproveMobile'
import { Log } from './pages/Log'
import { Spec } from './pages/Spec'
import { ValioView } from './pages/ValioView'

function LogRoute() {
  const { proposalId } = useParams()
  return <Log proposalId={proposalId} />
}

export default function App() {
  const { pathname } = useLocation()
  const approver = pathname.startsWith('/approver') || pathname.startsWith('/approve/')
  return (
    <div className={`app ${approver ? 'app-approver' : ''}`}>
      <a href="#main" className="skip-link">
        {approver ? 'Skip to proposals for approval' : 'Skip to the purchasing flow'}
      </a>
      <header className="app-header">
        <div className="brand">
          {approver ? (
            <UserRoundCheck size={18} aria-hidden />
          ) : (
            <ShieldCheck size={18} aria-hidden />
          )}
          <div>
            <span className="brand-name">{approver ? 'Approver panel' : 'Verified or Ask'}</span>
            <span className="brand-sub">
              {approver
                ? 'Human sign-off channel · not the agent'
                : 'Trusted Trade Gateway · Valio Aimo'}
            </span>
          </div>
        </div>
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
            <a
              href="/approver"
              target="_blank"
              rel="noopener"
              aria-label="Open Approver panel"
            >
              Approve <ExternalLink size={12} aria-hidden />
            </a>
          )}
          <Link
            to="/log"
            aria-current={pathname.startsWith('/log') ? 'page' : undefined}
          >
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
      <footer className="app-footer">
        <span>Simulated orders only — mock environment.</span>
        <span>Junction 2026 · proof of concept</span>
      </footer>
    </div>
  )
}
