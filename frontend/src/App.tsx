import { ShieldCheck } from 'lucide-react'
import { Main } from './pages/Main'
import { Log } from './pages/Log'
import { LOG_ROOT, proposalIdFromPath } from './components/log/logData'

function Page() {
  const path = window.location.pathname
  if (path === LOG_ROOT || path.startsWith(`${LOG_ROOT}/`)) {
    return <Log proposalId={proposalIdFromPath(path)} />
  }
  return <Main />
}

export default function App() {
  return (
    <div className="app">
      <a href="#main" className="skip-link">
        Skip to the purchasing flow
      </a>
      <header className="app-header">
        <div className="brand">
          <ShieldCheck size={22} aria-hidden />
          <div>
            <span className="brand-name">Verified or Ask</span>
            <span className="brand-sub">Trusted Trade Gateway · Valio Aimo</span>
          </div>
        </div>
        <p className="app-tagline">
          The agent acts alone only when every fact is proven and the order is inside the mandate.
        </p>
      </header>
      <main id="main">
        <Page />
      </main>
      <footer className="app-footer">
        <span>Simulated orders only — mock environment.</span>
        <span>Junction 2026 · proof of concept</span>
      </footer>
    </div>
  )
}
