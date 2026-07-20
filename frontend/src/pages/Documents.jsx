import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Card, Spinner } from '../components/ui.jsx'
import { usePersona } from '../persona.jsx'

const fmtSize = (b) =>
  b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`
const fmtDate = (iso) =>
  new Date(iso).toLocaleString('en', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function UploadPanel({ onUploaded }) {
  const { persona } = usePersona()
  const [docType, setDocType] = useState('submittal')
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const inputRef = useRef(null)

  const upload = async () => {
    if (!file) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const res = await api.uploadDocument(file, docType, `${persona.name} (${persona.role})`)
      setResult(res)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      onUploaded()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Upload a document">
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
          <label className="block">
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              Document type
            </span>
            <select value={docType} onChange={(e) => setDocType(e.target.value)}
              className="border-2 border-ink bg-paper px-2 py-2 font-mono text-xs uppercase tracking-wider focus:outline-none">
              <option value="submittal">Vendor submittal - UPS-02A (resubmittal)</option>
              <option value="reference">Reference document</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              File
            </span>
            <input ref={inputRef} type="file" onChange={(e) => setFile(e.target.files[0] || null)}
              className="font-body text-sm file:mr-3 file:border-2 file:border-ink file:bg-paper file:px-3 file:py-1.5 file:font-mono file:text-[10px] file:font-bold file:uppercase file:tracking-widest hover:file:bg-ink hover:file:text-paper" />
          </label>
          <button disabled={busy || !file} onClick={upload}
            className="bg-ink px-6 py-3 font-mono text-xs font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-2 hover:outline-ink disabled:opacity-40">
            {busy ? 'Uploading…' : 'Upload →'}
          </button>
          <span className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            as {persona.name} · {persona.role}
          </span>
        </div>
        <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
          {docType === 'submittal'
            ? 'Validated (PDF + must reference UPS-02A), then supersedes the live submittal - like a resubmittal landing in an EDMS.'
            : 'Stored in data/sources/ and listed here; not parsed by the pipeline.'}
        </p>
        {result && (
          <div className="bg-ink px-5 py-4 text-paper">
            <span className="font-mono text-xs font-bold uppercase tracking-widest">
              {result.filename} uploaded
            </span>
            <p className="mt-1 font-body text-[15px] text-paper/85">{result.note}</p>
          </div>
        )}
        {error && (
          <div className="border-2 border-alarm px-5 py-3 font-mono text-xs text-alarm">{error}</div>
        )}
      </div>
    </Card>
  )
}

export default function Documents() {
  const [docs, setDocs] = useState(null)
  const [error, setError] = useState(null)
  const [viewing, setViewing] = useState(null)
  const [historyFor, setHistoryFor] = useState(null)
  const [busyVersion, setBusyVersion] = useState(null)

  const refresh = useCallback(() => {
    api.documents().then((r) => setDocs(r.documents)).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const activateVersion = async (vid) => {
    setBusyVersion(vid)
    try { await api.activateSubmittalVersion(vid) } catch (e) { setError(String(e)) }
    finally { setBusyVersion(null); refresh() }
  }
  const deleteVersion = async (vid) => {
    if (!window.confirm('Delete this retained submittal revision?')) return
    setBusyVersion(vid)
    try { await api.deleteSubmittalVersion(vid) } catch (e) { setError(String(e)) }
    finally { setBusyVersion(null); refresh() }
  }

  if (error && !docs) {
    return <div className="border-2 border-ink p-6 font-mono text-sm">FAILED TO REACH API - {error}</div>
  }
  if (!docs) return <Spinner label="Loading document register" />

  return (
    <div className="space-y-8">
      <div className="border-b-4 border-ink pb-6">
        <h1 className="font-display text-6xl font-black leading-none tracking-tighter md:text-7xl">
          Documents.
        </h1>
        <p className="mt-3 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
          data/sources/ · the exact files the agents read - nothing analysed lives anywhere else
        </p>
      </div>

      <UploadPanel onUploaded={refresh} />

      <Card title="Document register">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b-2 border-ink font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              <th className="py-2 pr-4 font-medium">Document</th>
              <th className="pr-4 font-medium">File</th>
              <th className="pr-4 font-medium">Rev</th>
              <th className="pr-4 font-medium">Size</th>
              <th className="pr-4 font-medium">Updated</th>
              <th className="pr-4 font-medium">Status</th>
              <th className="font-medium" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {docs.map((d) => [
              <tr key={d.name} className="text-sm">
                <td className="py-3 pr-4">
                  <span className="font-bold">{d.label}</span>
                  <div className="font-mono text-[9px] uppercase tracking-wider text-muted-fg">
                    {d.uploaded_by && <>uploaded by {d.uploaded_by} </>}
                    {(d.submittal_versions?.length > 0 || d.upload_history?.length > 0) && (
                      <button onClick={() => setHistoryFor(historyFor === d.name ? null : d.name)}
                        className="ml-1 border border-line px-1.5 py-0.5 font-bold hover:border-ink hover:text-ink">
                        {historyFor === d.name ? 'hide' :
                          d.submittal_versions
                            ? `revisions (${d.submittal_versions.length})`
                            : `history (${d.upload_history.length})`}
                      </button>
                    )}
                  </div>
                </td>
                <td className="pr-4 font-mono text-xs text-muted-fg">{d.name}</td>
                <td className="pr-4 font-mono text-xs font-bold">{d.revision || '—'}</td>
                <td className="pr-4 font-mono text-xs tabular-nums">{fmtSize(d.size_bytes)}</td>
                <td className="pr-4 font-mono text-xs text-muted-fg">{fmtDate(d.modified)}</td>
                <td className="pr-4">
                  <span className={`font-mono text-[10px] uppercase tracking-wider ${
                    d.status === 'parsed' ? 'text-ink' :
                    d.status.startsWith('changed') ? 'font-bold text-alarm' : 'text-muted-fg'
                  }`}>{d.status}</span>
                </td>
                <td className="whitespace-nowrap text-right">
                  {d.name.endsWith('.pdf') && (
                    <button onClick={() => setViewing(viewing === d.name ? null : d.name)}
                      className="border border-ink px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-widest transition-none hover:bg-ink hover:text-paper">
                      {viewing === d.name ? 'Close' : 'View'}
                    </button>
                  )}
                  <a href={api.documentUrl(d.name)} download={d.name}
                    className="ml-2 font-mono text-[10px] uppercase tracking-widest text-muted-fg hover:text-ink hover:underline hover:underline-offset-4">
                    ↓
                  </a>
                </td>
              </tr>,
              historyFor === d.name && (
                <tr key={`${d.name}-history`}>
                  <td colSpan={7} className="bg-muted px-4 py-3">
                    {d.submittal_versions ? (
                      <>
                        <div className="mb-2 font-mono text-[9px] font-bold uppercase tracking-widest text-muted-fg">
                          Retained revisions - activate one to make it the live submittal
                        </div>
                        <table className="w-full text-left">
                          <tbody className="divide-y divide-line">
                            {d.submittal_versions.map((v) => (
                              <tr key={v.version_id} className="font-mono text-[11px]">
                                <td className="py-1.5 pr-3">
                                  <span className="font-bold">{v.label}</span>
                                  {v.live && <span className="ml-2 border border-ink bg-ink px-1 text-[9px] font-bold uppercase text-paper">live</span>}
                                </td>
                                <td className="pr-3 text-muted-fg">{v.uploaded_by}</td>
                                <td className="pr-3 text-muted-fg">{fmtDate(v.ts)}</td>
                                <td className="pr-3 text-muted-fg">{fmtSize(v.size_bytes)}</td>
                                <td className="whitespace-nowrap py-1.5 text-right">
                                  <button disabled={v.live || busyVersion === v.version_id}
                                    onClick={() => activateVersion(v.version_id)}
                                    className="border border-ink px-2 py-0.5 font-bold uppercase tracking-widest transition-none hover:bg-ink hover:text-paper disabled:cursor-not-allowed disabled:border-line disabled:text-muted-fg disabled:hover:bg-transparent">
                                    {v.live ? 'live' : busyVersion === v.version_id ? '…' : 'Make live'}
                                  </button>
                                  <button disabled={!v.deletable || busyVersion === v.version_id}
                                    onClick={() => deleteVersion(v.version_id)}
                                    title={!v.deletable ? (v.live ? 'activate another revision first' : 'bundled demo revision - respawns') : 'delete this retained revision'}
                                    className="ml-1.5 border border-line px-2 py-0.5 font-bold uppercase tracking-widest transition-none hover:border-alarm hover:text-alarm disabled:cursor-not-allowed disabled:text-muted-fg disabled:hover:border-line disabled:hover:text-muted-fg">
                                    Delete
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <div className="mt-2 font-mono text-[9px] uppercase tracking-wider text-muted-fg">
                          After activating a revision, run the agents to re-derive compliance.
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="mb-1 font-mono text-[9px] font-bold uppercase tracking-widest text-muted-fg">
                          Upload history
                        </div>
                        {d.upload_history.map((u, i) => (
                          <div key={i} className="py-0.5 font-mono text-[11px]">
                            <span className="font-bold">{u.original_name}</span>
                            <span className="text-muted-fg"> · {u.uploaded_by} · {fmtDate(u.ts)}</span>
                          </div>
                        ))}
                      </>
                    )}
                  </td>
                </tr>
              ),
            ])}
          </tbody>
        </table>
      </Card>

      {viewing && (
        <Card title={viewing}
          right={
            <button onClick={() => setViewing(null)}
              className="font-mono text-[10px] uppercase tracking-widest text-muted-fg hover:text-ink hover:underline hover:underline-offset-4">
              Close ✕
            </button>
          }>
          <iframe title={viewing} src={api.documentUrl(viewing)} className="h-[75vh] w-full border border-line" />
        </Card>
      )}
    </div>
  )
}
