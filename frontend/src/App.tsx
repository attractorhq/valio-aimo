import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowUpRight,
  BookOpen,
  ChevronDown,
  ClipboardCheck,
  History as HistoryIcon,
  Menu,
  MessageSquare,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { API_BASE, request, type Identity } from './lib/api'
import type { Context } from './lib/types'
import { Buyer } from './pages/Buyer'
import { Approvals } from './pages/Approvals'
import { History } from './pages/History'
import { Settings } from './pages/Settings'
import { Button } from './components/ui/button'
import { ErrorNotice, Loading } from './components/Common'

const names: Record<Identity, string> = {
  'demo-restaurant-a': 'Restaurant A',
  'demo-hotel-b': 'Hotel B',
  'demo-manager': 'Demo manager',
}
const navigation = [
  { to: '/', icon: MessageSquare, label: 'Buyer assistant', section: 'Workspace' },
  { to: '/approvals', icon: ClipboardCheck, label: 'Approvals' },
  { to: '/history', icon: HistoryIcon, label: 'Audit trail' },
  { to: '/settings', icon: Settings2, label: 'Buying policies' },
]
export default function App() {
  const [identity, setIdentity] = useState<Identity>(() => {
    const saved = sessionStorage.getItem('aimo-demo-identity') as Identity
    return saved in names ? saved : 'demo-restaurant-a'
  })
  const [menu, setMenu] = useState(false)
  const location = useLocation()
  const context = useQuery({
    queryKey: ['context', identity],
    queryFn: () => request<Context>(identity, '/api/v1/context'),
    retry: 1,
  })
  function changeIdentity(value: Identity) {
    sessionStorage.setItem('aimo-demo-identity', value)
    setIdentity(value)
  }
  return (
    <div className="app-shell">
      <aside className={`sidebar ${menu ? 'mobile-open' : ''}`}>
        <NavLink to="/" className="brand" onClick={() => setMenu(false)}>
          <span className="brand-word">
            aimo<span>®</span>
          </span>
          <span className="brand-divider" />
          <span className="brand-sub">
            trusted
            <br />
            trade
          </span>
        </NavLink>
        <div className="workspace-label">PURCHASING WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              onClick={() => setMenu(false)}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              <Icon size={18} />
              {label}
              {to === '/' && <span className="nav-active-dot" />}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-trust">
            <div className="trust-orbit">
              <ShieldCheck size={28} />
            </div>
            <h4>
              Agents move fast.
              <br />
              Trust goes further.
            </h4>
            <p>Open interfaces. Clear permissions. Verifiable decisions.</p>
          </div>
          <a
            className="nav-link api-link"
            href={`${API_BASE}/docs`}
            target="_blank"
            rel="noreferrer"
          >
            <BookOpen size={17} />
            Developer API
            <ArrowUpRight size={15} />
          </a>
          <div className="identity-box">
            <div className="avatar">
              {identity === 'demo-manager' ? 'DM' : identity === 'demo-hotel-b' ? 'HB' : 'RA'}
            </div>
            <div>
              <label htmlFor="identity">DEMO IDENTITY</label>
              <div className="identity-select">
                <select
                  id="identity"
                  value={identity}
                  onChange={(e) => changeIdentity(e.target.value as Identity)}
                >
                  {Object.entries(names).map(([value, label]) => (
                    <option value={value} key={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={13} />
              </div>
            </div>
          </div>
          <span className="sidebar-footnote">JUNCTION 2026 · PROOF OF CONCEPT</span>
        </div>
      </aside>
      {menu && (
        <button
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="content-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu"
              aria-label={menu ? 'Close menu' : 'Open menu'}
              onClick={() => setMenu(!menu)}
            >
              {menu ? <X size={22} /> : <Menu size={22} />}
            </button>
            <span>Workspace</span>
            <span className="slash">/</span>
            <strong>
              {navigation.find((n) => n.to === location.pathname)?.label || 'Page not found'}
            </strong>
          </div>
          <div className="topbar-badges">
            <span className={`env-badge ${context.data ? '' : 'env-offline'}`}>
              <i />
              {context.data
                ? context.data.mock_data
                  ? 'MOCK DATA'
                  : 'LIVE CHALLENGE API'
                : 'CONNECTING'}
            </span>
            <span className="demo-badge">DEMO AUTH</span>
            <span className="topbar-avatar">
              {names[identity]
                .split(' ')
                .map((s) => s[0])
                .join('')}
            </span>
          </div>
        </header>
        <main key={identity}>
          <ErrorNotice error={context.error} />
          {context.isPending ? (
            <Loading label="Connecting to the purchasing gateway…" />
          ) : context.error ? (
            <div className="connection-help">
              <h1>Let’s connect your gateway.</h1>
              <p>The React app is running. Start FastAPI, then retry.</p>
              <code>{API_BASE}</code>
              <Button onClick={() => context.refetch()}>Retry connection</Button>
            </div>
          ) : (
            context.data && (
              <Routes>
                <Route path="/" element={<Buyer identity={identity} context={context.data} />} />
                <Route path="/approvals" element={<Approvals identity={identity} />} />
                <Route path="/history" element={<History identity={identity} />} />
                <Route
                  path="/settings"
                  element={<Settings identity={identity} mockMode={context.data.mock_data} />}
                />
                <Route
                  path="*"
                  element={
                    <div className="page-heading">
                      <h1>Page not found.</h1>
                      <NavLink to="/">Back to the buyer assistant</NavLink>
                    </div>
                  }
                />
              </Routes>
            )
          )}
        </main>
        <footer className="site-footer">
          <span>
            Aimo Trusted Trade <span>·</span> Built for an agent-ready foodservice future.
          </span>
          <span>
            SIMULATED ORDERS ONLY <ShieldCheck size={13} />
          </span>
        </footer>
      </div>
    </div>
  )
}
