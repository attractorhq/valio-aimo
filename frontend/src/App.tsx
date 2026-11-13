import { ExternalLink, ShieldCheck, UserRoundCheck } from 'lucide-react'
import { Link, Route, Routes, useLocation } from 'react-router-dom'
import { Main } from './pages/Main'
import { Approver } from './pages/Approver'

export default function App() {
  const approver = useLocation().pathname.startsWith('/approver')
  return (
    <div className={`app ${approver ? 'app-approver' : ''}`}>
      <a href="#main" className="skip-link">
        {approver ? 'Skip to proposals for approval' : 'Skip to the purchasing flow'}
      </a>
      <header className="app-header">
        <div className="brand">
          {approver ? (
            <UserRoundCheck size={22} aria-hidden />
          ) : (
            <ShieldCheck size={22} aria-hidden />
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
          {approver ? (
            <Link to="/">Buyer view</Link>
          ) : (
            // The approver panel is a separate window, like a separate person's device.
            <a href="/approver" target="_blank" rel="noopener">
              Open Approver panel <ExternalLink size={13} aria-hidden />
            </a>
          )}
        </nav>
      </header>
      <main id="main">
        <Routes>
          <Route path="/" element={<Main />} />
          <Route path="/approver" element={<Approver />} />
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
