import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Card, SeverityBadge, Spinner } from '../components/ui.jsx'
import { usePersona } from '../persona.jsx'

const EMPTY = { title: '', equipment_tag: '', location: '', spec_clause: '', severity: 'MAJOR' }

const inputCls =
  'w-full border-0 border-b-2 border-ink bg-transparent px-1 py-1.5 font-body text-sm ' +
  'placeholder:italic placeholder:text-muted-fg focus:border-b-4 focus:outline-none'

// The record is created HERE, by a structured form - the chat/LLM only
// prefills a draft the user confirms. The formal PDF is generated from the
// filed record, never the other way round.
function RaiseNcr({ onFiled }) {
  const { persona } = usePersona()
  const [description, setDescription] = useState('')
  const [fields, setFields] = useState(EMPTY)
  const [drafting, setDrafting] = useState(false)
  const [draftNote, setDraftNote] = useState(null)
  const [busy, setBusy] = useState(false)
  const [filed, setFiled] = useState(null)
  const [error, setError] = useState(null)

  const set = (k) => (e) => setFields((f) => ({ ...f, [k]: e.target.value }))

  const prefill = async () => {
    if (!description.trim()) return
    setDrafting(true)
    setError(null)
    try {
      const res = await api.ncrDraft(description)
      setFields({ ...EMPTY, ...res.draft })
      setDraftNote(res.source === 'llm'
        ? 'Draft extracted - review every field before filing.'
        : res.source)
    } catch (e) {
      setError(String(e))
    } finally {
      setDrafting(false)
    }
  }

  const file = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.ncrCreate({
        ...fields,
        description,
        raised_by: `${persona.name} (${persona.role})`,
      })
      setFiled(res)
      setDescription('')
      setFields(EMPTY)
      setDraftNote(null)
      onFiled()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Raise a non-conformance report">
      <div className="space-y-5">
        <div>
          <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            Describe the issue observed on site
          </span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
            placeholder="e.g. UPS-02A arrived with a dented enclosure door and the rating plate reads 1.8 MVA against the 2.0 MVA required by ELEC-4.2.1…"
            className="w-full border-2 border-ink bg-transparent px-3 py-2 font-body text-[15px] leading-relaxed placeholder:italic placeholder:text-muted-fg focus:outline-none" />
          <button disabled={drafting || !description.trim()} onClick={prefill}
            className="mt-2 border-2 border-ink px-4 py-2 font-mono text-[10px] font-bold uppercase tracking-widest transition-none hover:bg-ink hover:text-paper disabled:opacity-40">
            {drafting ? 'Extracting…' : 'Prefill form from description →'}
          </button>
          {draftNote && (
            <span className="ml-3 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              {draftNote}
            </span>
          )}
        </div>

        <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
          <label className="sm:col-span-2">
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">Title *</span>
            <input value={fields.title} onChange={set('title')} className={inputCls}
              placeholder="one-line non-conformance title" />
          </label>
          <label>
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">Equipment tag</span>
            <input value={fields.equipment_tag} onChange={set('equipment_tag')} className={inputCls}
              placeholder="UPS-02A" />
          </label>
          <label>
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">Location</span>
            <input value={fields.location} onChange={set('location')} className={inputCls}
              placeholder="Electrical Room B, Level 1" />
          </label>
          <label>
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">Spec clause</span>
            <input value={fields.spec_clause} onChange={set('spec_clause')} className={inputCls}
              placeholder="ELEC-4.2.1 (optional)" />
          </label>
          <label>
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">Severity</span>
            <select value={fields.severity} onChange={set('severity')}
              className="border-2 border-ink bg-paper px-2 py-2 font-mono text-xs uppercase tracking-wider focus:outline-none">
              <option>CRITICAL</option>
              <option>MAJOR</option>
              <option>MINOR</option>
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <button disabled={busy || !fields.title.trim() || !description.trim()} onClick={file}
            className="bg-ink px-8 py-3.5 font-mono text-xs font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-2 hover:outline-ink disabled:opacity-40">
            {busy ? 'Filing…' : 'File NCR →'}
          </button>
          <span className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            raised by {persona.name} · {persona.role}
          </span>
        </div>

        {filed && (
          <div className="bg-ink px-6 py-5 text-paper">
            <div className="flex flex-wrap items-baseline gap-4">
              <span className="font-display text-2xl font-black tracking-tight">{filed.ncr_id} filed.</span>
              <a href={api.ncrPdfUrl(filed.ncr_id)} target="_blank" rel="noreferrer"
                className="border-2 border-paper px-4 py-1.5 font-mono text-[10px] font-bold uppercase tracking-widest transition-none hover:bg-paper hover:text-ink">
                Formal NCR form (PDF) ↓
              </a>
            </div>
            <p className="mt-2 font-body text-[15px] leading-relaxed text-paper/85">{filed.note}</p>
          </div>
        )}
        {error && <div className="border-2 border-alarm px-5 py-3 font-mono text-xs text-alarm">{error}</div>}
      </div>
    </Card>
  )
}

function LifecycleActions({ n, onChanged }) {
  const { persona } = usePersona()
  const [disp, setDisp] = useState('rework')
  const [busy, setBusy] = useState(false)
  const by = `${persona.name} (${persona.role})`

  const act = async (fn) => {
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
    onChanged()
  }

  if (n.status === 'OPEN') {
    return (
      <span className="inline-flex items-center gap-2">
        <select value={disp} onChange={(e) => setDisp(e.target.value)}
          className="border border-ink bg-paper px-1.5 py-1 font-mono text-[10px] uppercase tracking-wider focus:outline-none">
          <option value="use-as-is">use-as-is</option>
          <option value="rework">rework</option>
          <option value="reject">reject</option>
        </select>
        <button disabled={busy} onClick={() => act(() => api.ncrDisposition(n.ncr_id, disp, by, ''))}
          className="border border-ink px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-widest transition-none hover:bg-ink hover:text-paper disabled:opacity-40">
          Disposition
        </button>
      </span>
    )
  }
  if (n.status === 'DISPOSITIONED') {
    return (
      <button disabled={busy} onClick={() => act(() => api.ncrClose(n.ncr_id, by))}
        className="bg-ink px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-1 hover:outline-ink disabled:opacity-40">
        Close
      </button>
    )
  }
  return null
}

export default function Ncr() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [openNcr, setOpenNcr] = useState(null)

  const refresh = useCallback(() => {
    api.ncrs().then(setData).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  if (error && !data) {
    return <div className="border-2 border-ink p-6 font-mono text-sm">FAILED TO REACH API - {error}</div>
  }
  if (!data) return <Spinner label="Loading NCR register" />

  return (
    <div className="space-y-8">
      <div className="border-b-4 border-ink pb-6">
        <h1 className="font-display text-6xl font-black leading-none tracking-tighter md:text-7xl">
          Quality.
        </h1>
        <p className="mt-3 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
          Non-conformance reports · open → dispositioned (use-as-is / rework / reject) → closed ·
          filing publishes a FIELD signal to the convergence engine
        </p>
      </div>

      <RaiseNcr onFiled={refresh} />

      <Card title={`NCR register · ${data.open} open`}>
        {data.ncrs.length === 0 ? (
          <div className="font-mono text-xs uppercase tracking-widest text-muted-fg">
            No NCRs on record.
          </div>
        ) : (
          <table className="w-full text-left">
            <thead>
              <tr className="border-b-2 border-ink font-mono text-[10px] uppercase tracking-widest text-muted-fg">
                <th className="py-2 pr-4 font-medium">NCR</th>
                <th className="pr-4 font-medium">Title</th>
                <th className="pr-4 font-medium">Equipment</th>
                <th className="pr-4 font-medium">Severity</th>
                <th className="pr-4 font-medium">Status</th>
                <th className="pr-4 font-medium">Raised by</th>
                <th className="font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.ncrs.map((n) => [
                <tr key={n.ncr_id} className="text-sm">
                  <td className="whitespace-nowrap py-3 pr-4 font-mono text-xs font-bold">{n.ncr_id}</td>
                  <td className="max-w-xs cursor-pointer pr-4" title="click for detail"
                    onClick={() => setOpenNcr(openNcr === n.ncr_id ? null : n.ncr_id)}>
                    <span className="font-bold underline decoration-line decoration-dotted underline-offset-2 hover:decoration-ink">
                      {n.title}
                    </span>
                    <div className="truncate text-[12px] text-muted-fg">{n.description}</div>
                  </td>
                  <td className="pr-4 font-mono text-xs">{n.equipment_tag || '—'}</td>
                  <td className="pr-4"><SeverityBadge severity={n.severity} /></td>
                  <td className="whitespace-nowrap pr-4">
                    <span className={`font-mono text-[10px] font-bold uppercase tracking-wider ${
                      n.status === 'OPEN' ? 'text-alarm' : n.status === 'CLOSED' ? 'text-muted-fg' : ''
                    }`}>
                      {n.status}{n.disposition ? ` · ${n.disposition}` : ''}
                    </span>
                  </td>
                  <td className="pr-4 font-mono text-[10px] uppercase tracking-wider text-muted-fg">
                    {n.raised_by}
                  </td>
                  <td className="whitespace-nowrap py-2 text-right">
                    <LifecycleActions n={n} onChanged={refresh} />
                    <a href={api.ncrPdfUrl(n.ncr_id)} target="_blank" rel="noreferrer"
                      className="ml-2 border border-ink px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-widest transition-none hover:bg-ink hover:text-paper">
                      PDF
                    </a>
                  </td>
                </tr>,
                openNcr === n.ncr_id && (
                  <tr key={`${n.ncr_id}-detail`}>
                    <td colSpan={7} className="bg-muted px-4 py-4">
                      <div className="font-display text-lg font-bold leading-snug">{n.title}</div>
                      <p className="mt-2 max-w-3xl font-body text-[14px] leading-relaxed text-ink">{n.description}</p>
                      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[10px] uppercase tracking-wider text-muted-fg">
                        <span>{n.ncr_id}</span>
                        <span>equipment <strong className="text-ink">{n.equipment_tag || '—'}</strong></span>
                        {n.location && <span>location <strong className="text-ink">{n.location}</strong></span>}
                        {n.spec_clause && <span>clause <strong className="text-ink">{n.spec_clause}</strong></span>}
                        <span>severity <strong className="text-ink">{n.severity}</strong></span>
                        <span>status <strong className="text-ink">{n.status}{n.disposition ? ` · ${n.disposition}` : ''}</strong></span>
                        <span>raised <strong className="text-ink">{n.date_raised}</strong> by {n.raised_by}</span>
                      </div>
                      {n.disposition_note && (
                        <div className="mt-2 font-body text-[13px] italic text-muted-fg">Disposition note: {n.disposition_note}</div>
                      )}
                      {n.history?.length > 0 && (
                        <div className="mt-3">
                          <div className="mb-1 font-mono text-[9px] font-bold uppercase tracking-widest text-muted-fg">History</div>
                          {n.history.map((h, i) => (
                            <div key={i} className="font-mono text-[11px] text-muted-fg">
                              {String(h.ts).replace('T', ' ').slice(0, 16)} · {h.action} · {h.by}
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ),
              ])}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
