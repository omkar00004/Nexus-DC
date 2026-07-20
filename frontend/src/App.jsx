import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { PERSONAS, PersonaProvider, usePersona } from './persona.jsx'
import { RunProvider, useRun } from './runstate.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Commissioning from './pages/Commissioning.jsx'
import OracleChat from './pages/OracleChat.jsx'
import Documents from './pages/Documents.jsx'
import Ncr from './pages/Ncr.jsx'

const navCls = ({ isActive }) =>
  `px-3 py-1.5 font-mono text-[11px] font-medium uppercase tracking-widest transition-colors duration-100 ${
    isActive
      ? 'bg-ink text-paper'
      : 'text-muted-fg hover:text-ink hover:underline hover:underline-offset-4'
  }`

// Attribution, not authentication: the persona tags GUIDE sessions and
// document uploads. Everyone sees the same converged project truth.
function PersonaSwitcher() {
  const { persona, setPersona } = usePersona()
  return (
    <label className="flex items-center gap-2">
      <span className="hidden font-mono text-[10px] uppercase tracking-widest text-muted-fg lg:inline">
        Acting as
      </span>
      <select value={persona.id} onChange={(e) => setPersona(e.target.value)}
        className="border border-ink bg-paper px-2 py-1.5 font-mono text-[10px] uppercase tracking-widest focus:outline-none">
        {PERSONAS.map((p) => (
          <option key={p.id} value={p.id}>{p.name} · {p.role}</option>
        ))}
      </select>
    </label>
  )
}

// Rendered above the router, so the analysis stays visible on every tab.
function RunBanner() {
  const { running, elapsed, error } = useRun()
  const [events, setEvents] = useState(null)

  useEffect(() => {
    if (!running) return
    const poll = () => api.events(1).then((r) => setEvents(r.count)).catch(() => {})
    poll()
    const t = setInterval(poll, 3000)
    return () => clearInterval(t)
  }, [running])

  if (error) {
    return (
      <div className="mx-auto max-w-7xl px-6 pt-6 md:px-8">
        <div className="border-2 border-alarm px-5 py-3 font-mono text-xs text-alarm">
          Run all agents failed - {error}
        </div>
      </div>
    )
  }
  if (!running) return null
  return (
    <div className="mx-auto max-w-7xl px-6 pt-6 md:px-8">
      <div className="flex flex-wrap items-center gap-4 bg-ink px-6 py-4 text-paper">
        <span className="h-3.5 w-3.5 animate-spin border-2 border-paper/30 border-t-paper" />
        <span className="font-display text-3xl font-bold tabular-nums leading-none">{elapsed}s</span>
        <span className="font-mono text-xs font-bold uppercase tracking-widest">
          Processing - SPECTRA → CHRONOS → TRACIS → CONVERGENCE
        </span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-paper/60">
          parsing documents, running 5,000 simulations, reconciling signals · typically 60-90 s
        </span>
        {events !== null && (
          <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-paper/60">
            live events: {events}
          </span>
        )}
      </div>
    </div>
  )
}

function Shell() {
  const [apiUp, setApiUp] = useState(null)

  useEffect(() => {
    const check = () => api.health().then(() => setApiUp(true)).catch(() => setApiUp(false))
    check()
    const t = setInterval(check, 15000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="min-h-screen bg-paper text-ink">
      <header className="sticky top-0 z-20 border-b-2 border-ink bg-paper">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-8 gap-y-2 px-6 py-4">
          <div className="flex items-center gap-3">
            <img src="/logo.png" alt="NEXUS-DC" className="h-23" />
            <span className="hidden font-mono text-[10px] uppercase tracking-widest text-muted-fg xl:inline">
              Meridian Data Centre · Phase 1 · Rated-3 (TIA-942) / Tier III (Uptime)
            </span>
          </div>
          <nav className="flex gap-1">
            <NavLink to="/" end className={navCls}>Risk Dashboard</NavLink>
            <NavLink to="/commissioning" className={navCls}>Commissioning</NavLink>
            <NavLink to="/oracle" className={navCls}>Oracle</NavLink>
            <NavLink to="/documents" className={navCls}>Documents</NavLink>
            <NavLink to="/ncr" className={navCls}>NCR</NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-4">
            <PersonaSwitcher />
            <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest">
              <span className={`h-2 w-2 ${
                apiUp === null ? 'bg-line' : apiUp ? 'bg-ink' : 'animate-pulse border border-ink bg-paper'
              }`} />
              <span className="text-muted-fg">{apiUp === false ? 'API offline' : 'API :8000'}</span>
            </div>
          </div>
        </div>
      </header>
      <RunBanner />
      <main className="mx-auto max-w-7xl px-6 py-10 md:px-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/commissioning" element={<Commissioning />} />
          <Route path="/oracle" element={<OracleChat />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/ncr" element={<Ncr />} />
        </Routes>
      </main>
      <footer className="border-t-4 border-ink">
        <div className="mx-auto max-w-7xl px-6 py-6 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
          NEXUS-DC · synthetic content in authentic formats · every conclusion computed from source
        </div>
      </footer>
    </div>
  )
}

export default function App() {
  return (
    <PersonaProvider>
      <RunProvider>
        <Shell />
      </RunProvider>
    </PersonaProvider>
  )
}
