import { useEffect, useState } from 'react'
import { api } from '../api'
import { Card, Spinner } from '../components/ui.jsx'
import { usePersona } from '../persona.jsx'

function criteriaText(c) {
  if (c.operator === 'range') return `${c.min} - ${c.max} ${c.unit || ''}`
  if (c.operator === 'boolean') return `must be ${String(c.expected)}`
  const sym = { less_equal: '≤', greater_equal: '≥', equal: '=' }[c.operator] || c.operator
  return `${sym} ${c.limit} ${c.unit || ''}`
}

function StepRunner({ procedure, session, setSession }) {
  const [reading, setReading] = useState('')
  const [busy, setBusy] = useState(false)
  const [lastResult, setLastResult] = useState(null)
  // restore a previously generated ITP when the session is re-opened
  const [itp, setItp] = useState(session.itp || null)

  const step = procedure.steps[session.current_step - 1]
  const done = session.status !== 'in_progress'
  const isBool = step && step.acceptance_criteria.operator === 'boolean'

  const submit = async (value) => {
    setBusy(true)
    try {
      const res = await api.guideSubmit(session.session_id, value)
      setSession(res.session)
      setLastResult(res.step_result)
      setReading('')
    } finally {
      setBusy(false)
    }
  }

  const complete = async () => {
    setBusy(true)
    try {
      setItp(await api.guideComplete(session.session_id))
    } finally {
      setBusy(false)
    }
  }

  const downloadItp = () => {
    const blob = new Blob([JSON.stringify(itp, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${itp.itp_no}.json`
    a.click()
  }

  return (
    <div className="space-y-6">
      {/* square step pips - PASS inverts, FAIL is the ✕ */}
      <div className="flex items-center gap-1.5">
        {procedure.steps.map((s) => {
          const rec = session.readings.find((r) => r.step_no === s.step_no)
          const cls = rec
            ? rec.result === 'PASS'
              ? 'bg-ink text-paper border-ink'
              : 'bg-alarm text-paper border-alarm'
            : s.step_no === session.current_step && !done
              ? 'bg-paper text-ink border-ink border-2'
              : 'bg-paper text-muted-fg border-line'
          return (
            <span key={s.step_no}
              className={`flex h-8 w-8 items-center justify-center border font-mono text-xs font-bold ${cls}`}>
              {rec && rec.result === 'FAIL' ? '✕' : s.step_no}
            </span>
          )
        })}
        <span className="ml-3 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
          {session.readings.length}/{session.total_steps} recorded
        </span>
      </div>

      {session.sign_off_blocked && (
        <div className="bg-ink px-6 py-5 text-paper" style={{ borderLeft: '8px solid #dc2626' }}>
          <div className="font-display text-2xl font-black tracking-tight text-alarm">
            Sign-off blocked.
          </div>
          <div className="mt-2 text-[15px] leading-relaxed text-paper/80">
            Step{(session.failed_steps || [session.failed_step]).length > 1 ? 's' : ''}{' '}
            {(session.failed_steps || [session.failed_step]).join(', ')} failed acceptance.{' '}
            <strong className="text-paper">{(session.rfi_ids || [session.rfi_id]).join(', ')}</strong>{' '}
            auto-raised in the knowledge graph, linked to {procedure.linked_activity}. Continue the
            remaining steps - the ITP records the full picture; sign-off stays blocked until
            rectification and retest.
          </div>
        </div>
      )}

      {!done && step && (
        <div className="border-2 border-ink p-6">
          <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] font-bold uppercase tracking-widest">
            <span>Step {step.step_no} / {session.total_steps}</span>
            {step.hold_point && <span className="border border-ink px-1.5 py-0.5">Hold point</span>}
            {step.critical_hold && <span className="bg-ink px-1.5 py-0.5 text-paper">Critical</span>}
          </div>
          <p className="mt-3 font-body text-lg leading-relaxed">{step.instruction}</p>
          <div className="mt-2 font-mono text-[11px] uppercase tracking-wider text-muted-fg">
            Measure <span className="font-bold text-ink">{step.measurement}</span> · acceptance{' '}
            <span className="font-bold text-ink">{criteriaText(step.acceptance_criteria)}</span>
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            {isBool ? (
              <>
                <button disabled={busy} onClick={() => submit(false)}
                  className="bg-ink px-8 py-3.5 font-mono text-xs font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-2 hover:outline-ink disabled:opacity-40">
                  No / False
                </button>
                <button disabled={busy} onClick={() => submit(true)}
                  className="border-2 border-ink bg-paper px-8 py-3.5 font-mono text-xs font-bold uppercase tracking-widest text-ink transition-none hover:bg-ink hover:text-paper disabled:opacity-40">
                  Yes / True
                </button>
              </>
            ) : (
              <>
                <input
                  type="number" step="any" value={reading}
                  onChange={(e) => setReading(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && reading !== '' && submit(parseFloat(reading))}
                  placeholder={`reading (${step.unit || 'value'})`}
                  className="w-48 border-0 border-b-2 border-ink bg-transparent px-1 py-2 font-mono text-lg tabular-nums placeholder:font-body placeholder:text-base placeholder:italic placeholder:text-muted-fg focus:border-b-4 focus:outline-none"
                />
                <button disabled={busy || reading === ''} onClick={() => submit(parseFloat(reading))}
                  className="bg-ink px-8 py-3.5 font-mono text-xs font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-2 hover:outline-ink disabled:opacity-40">
                  Record →
                </button>
              </>
            )}
            {busy && <Spinner label="evaluating" />}
          </div>
        </div>
      )}

      {lastResult && (
        <div className={`px-5 py-3 font-mono text-xs uppercase tracking-widest ${
          lastResult.result === 'PASS' ? 'border border-ink' : 'bg-alarm text-paper'
        }`}>
          Step {lastResult.step_no} - {lastResult.result} · {lastResult.detail}
        </div>
      )}

      {session.readings.length > 0 && (
        <table className="w-full text-left">
          <thead>
            <tr className="border-b-2 border-ink font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              <th className="py-2 font-medium">#</th><th className="font-medium">Measurement</th>
              <th className="font-medium">Reading</th><th className="font-medium">Result</th>
              <th className="font-medium">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {session.readings.map((r) => (
              <tr key={r.step_no} className="text-sm">
                <td className="py-2.5 font-mono text-xs">{r.step_no}</td>
                <td className="font-mono text-xs text-muted-fg">{r.measurement}</td>
                <td className="tabular-nums">{String(r.reading)} {r.unit || ''}</td>
                <td>
                  <span className={`px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-widest ${
                    r.result === 'PASS' ? 'border border-ink' : 'bg-alarm text-paper'
                  }`}>{r.result}</span>
                </td>
                <td className="text-[13px] text-muted-fg">{r.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {done && !itp && (
        <button disabled={busy} onClick={complete}
          className="bg-ink px-8 py-4 font-mono text-xs font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-2 hover:outline-ink disabled:opacity-40">
          Generate ITP record →
        </button>
      )}

      {itp && (
        <div className={`border-2 border-ink p-6 ${itp.result === 'PASS' ? '' : 'bg-muted'}`}>
          <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-ink pb-3">
            <div>
              <span className="font-mono text-sm font-bold">{itp.itp_no}</span>
              <span className="ml-4 font-display text-2xl font-black">
                {itp.result} - {itp.sign_off}
              </span>
            </div>
            <button onClick={downloadItp}
              className="border-2 border-ink px-4 py-2 font-mono text-[10px] font-bold uppercase tracking-widest transition-none hover:bg-ink hover:text-paper">
              Download ITP ↓
            </button>
          </div>
          <p className="mt-3 font-body text-[15px] leading-relaxed">{itp.summary}</p>
          <div className="mt-3 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            {itp.standard_ref} · {itp.tier_target}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Commissioning() {
  const { persona } = usePersona()
  const [procedures, setProcedures] = useState(null)
  const [procedure, setProcedure] = useState(null)
  const [session, setSession] = useState(null)
  const [operator, setOperator] = useState(`${persona.name} · ${persona.role}`)

  // the header persona is the default operator; still editable per session
  useEffect(() => { setOperator(`${persona.name} · ${persona.role}`) }, [persona])

  useEffect(() => {
    api.guideProcedures().then((r) => setProcedures(r.procedures))
    // resume the in-flight test if the user switched tabs mid-session
    const saved = sessionStorage.getItem('nexus_guide_session')
    if (saved) {
      const { pid, sid } = JSON.parse(saved)
      Promise.all([api.guideProcedure(pid), api.guideSession(sid)])
        .then(([proc, sess]) => { setProcedure(proc); setSession(sess) })
        .catch(() => sessionStorage.removeItem('nexus_guide_session'))
    }
  }, [])

  const start = async (pid) => {
    const [proc, sess] = await Promise.all([api.guideProcedure(pid), api.guideStart(pid, operator)])
    setProcedure(proc)
    setSession(sess)
    sessionStorage.setItem('nexus_guide_session',
      JSON.stringify({ pid, sid: sess.session_id }))
  }

  const exitSession = () => {
    setProcedure(null)
    setSession(null)
    sessionStorage.removeItem('nexus_guide_session')
  }

  if (!procedures) return <Spinner label="Loading procedures" />

  if (procedure && session) {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <button onClick={exitSession}
          className="font-mono text-[11px] uppercase tracking-widest text-muted-fg hover:text-ink hover:underline hover:underline-offset-4">
          ← All procedures
        </button>
        <Card title={`${procedure.procedure_id} - ${procedure.title}`}>
          <p className="mb-5 border-b border-line pb-4 font-body text-[15px] italic leading-relaxed text-muted-fg">
            {procedure.objective}
          </p>
          <StepRunner procedure={procedure} session={session} setSession={setSession} />
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <div className="border-b-4 border-ink pb-6">
        <h1 className="font-display text-6xl font-black leading-none tracking-tighter md:text-7xl">
          Commission.
        </h1>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            Deterministic pass/fail against authored criteria - the LLM never grades a reading
          </p>
          <input value={operator} onChange={(e) => setOperator(e.target.value)}
            className="w-64 border-0 border-b-2 border-ink bg-transparent px-1 py-1.5 font-body text-sm placeholder:italic placeholder:text-muted-fg focus:border-b-4 focus:outline-none"
            placeholder="Operator name" />
        </div>
      </div>
      <div className="grid gap-px bg-ink sm:grid-cols-2 xl:grid-cols-3" style={{ border: '1px solid #000' }}>
        {procedures.map((p) => (
          <button key={p.procedure_id} onClick={() => start(p.procedure_id)}
            className="group bg-paper p-7 text-left transition-colors duration-100 hover:bg-ink hover:text-paper">
            <div className="font-mono text-[10px] font-bold uppercase tracking-widest text-muted-fg group-hover:text-paper/60">
              {p.procedure_id}
            </div>
            <div className="mt-2 font-display text-xl font-bold leading-tight tracking-tight">
              {p.title}
            </div>
            <div className="mt-4 font-mono text-[10px] uppercase tracking-widest text-muted-fg group-hover:text-paper/60">
              {p.system} · {p.steps} steps{p.equipment_tag ? ` · ${p.equipment_tag}` : ''}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
