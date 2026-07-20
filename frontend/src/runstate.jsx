import { createContext, useContext, useEffect, useState } from 'react'
import { api } from './api'

// Global "Run All Agents" state. The run itself happens server-side; this
// context only keeps the UI truth (running / elapsed / last timings) ABOVE
// the router, so switching tabs mid-run never hides the processing banner.
const RunContext = createContext(null)

export function RunProvider({ children }) {
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState(null)
  const [lastRun, setLastRun] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem('nexus_last_run') || 'null') } catch { return null }
  })

  useEffect(() => {
    if (!running) return
    setElapsed(0)
    const t0 = Date.now()
    const t = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000)
    return () => clearInterval(t)
  }, [running])

  const runAll = async () => {
    if (running) return
    setRunning(true)
    setError(null)
    try {
      const res = await api.runAllAgents()
      if (res.timings) {
        setLastRun(res.timings)
        sessionStorage.setItem('nexus_last_run', JSON.stringify(res.timings))
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <RunContext.Provider value={{ running, elapsed, error, lastRun, runAll }}>
      {children}
    </RunContext.Provider>
  )
}

export const useRun = () => useContext(RunContext)
