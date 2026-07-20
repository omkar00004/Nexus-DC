import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Md, Spinner } from '../components/ui.jsx'

// Progressive reveal: the answer "types out" as it renders. Client-side only -
// the full answer already arrived (~2 s); this is perceived-latency UX, not
// backend token streaming (that is the production upgrade, done with SSE).
function Reveal({ text, animate, onDone, onTick }) {
  const [shown, setShown] = useState(animate ? 0 : text.length)
  useEffect(() => {
    if (!animate) return
    // time-based (not tick-based): full speed in foreground, and if the tab
    // is backgrounded it simply completes on return instead of crawling
    const duration = Math.min(2500, Math.max(800, text.length * 5))
    const start = performance.now()
    let raf
    const tick = () => {
      const p = Math.min((performance.now() - start) / duration, 1)
      setShown(Math.round(p * text.length))
      onTick?.()
      if (p < 1) {
        raf = requestAnimationFrame(tick)
      } else {
        onDone?.()
      }
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [text, animate])
  return <Md text={text.slice(0, shown)} />
}

const QUICK_QUERIES = [
  'Which open RFIs are on the critical path?',
  'What is the current status of UPS-02A?',
  'What does the spec require for UPS battery autonomy?',
  'Which deliveries are at risk?',
]

function Citations({ msg }) {
  const [open, setOpen] = useState(false)
  const docCites = (msg.citations || []).filter((c) => c.source !== 'knowledge_graph')
  const paths = msg.graph_paths || []
  if (!docCites.length && !paths.length) return null
  return (
    <div className="mt-3 border-t border-line pt-2">
      <button onClick={() => setOpen(!open)}
        className="font-mono text-[10px] font-bold uppercase tracking-widest text-muted-fg hover:text-ink hover:underline hover:underline-offset-4">
        {open ? '− ' : '+ '}Evidence · {paths.length} graph path{paths.length === 1 ? '' : 's'} · {docCites.length} citation{docCites.length === 1 ? '' : 's'}
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {paths.length > 0 && (
            <div>
              <div className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">
                Graph traversal (multi-hop)
              </div>
              {paths.map((p, i) => (
                <div key={i} className="mt-1.5 border-l-2 border-ink pl-3 font-mono text-[11px] leading-relaxed">
                  {p}
                </div>
              ))}
            </div>
          )}
          {docCites.length > 0 && (
            <div>
              <div className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">
                Documents (vector retrieval)
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {docCites.map((c, i) => (
                  <span key={i} className="border border-line px-2 py-0.5 font-mono text-[10px] text-muted-fg">
                    {c.source}{c.ref ? ` · ${c.ref}` : ''}{c.page ? ` · p${c.page}` : ''}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// chat survives tab switches: state is mirrored to sessionStorage (cleared
// when the browser tab closes - fine for a demo, no backend needed)
function loadChat() {
  try {
    return JSON.parse(sessionStorage.getItem('nexus_oracle_chat') || '[]')
  } catch {
    return []
  }
}

export default function OracleChat() {
  const [messages, setMessages] = useState(loadChat)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    sessionStorage.setItem('nexus_oracle_chat', JSON.stringify(messages.slice(-40)))
  }, [messages])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  const ask = async (q) => {
    const query = (q ?? input).trim()
    if (!query || busy) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', content: query }])
    setBusy(true)
    try {
      const res = await api.oracleQuery(query)
      setMessages((m) => [...m, {
        role: 'oracle', content: res.answer, citations: res.citations,
        graph_paths: res.graph_paths, intent: res.intent, animate: true,
      }])
    } catch (e) {
      setMessages((m) => [...m, { role: 'oracle', content: `Query failed: ${e}` }])
    } finally {
      setBusy(false)
    }
  }

  const settle = (i) => {
    // freeze the message once revealed so tab switches don't replay the animation
    setMessages((m) => m.map((msg, j) => (j === i ? { ...msg, animate: false } : msg)))
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col">
      <div className="border-b-4 border-ink pb-5">
        <h1 className="font-display text-6xl font-black leading-none tracking-tighter md:text-7xl">
          Ask.
        </h1>
        <div className="mt-2 flex items-center justify-between gap-4">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">
            Hybrid GraphRAG · NetworkX traversal + ChromaDB + synthesis · every claim cited
          </p>
          {messages.length > 0 && (
            <button onClick={() => setMessages([])}
              className="font-mono text-[10px] uppercase tracking-widest text-muted-fg hover:text-ink hover:underline hover:underline-offset-4">
              Clear chat
            </button>
          )}
        </div>
      </div>

      <div className="min-h-72 space-y-5 overflow-y-auto py-6 md:h-[45vh]">
        {messages.length === 0 && (
          <p className="py-12 text-center font-body text-lg italic text-muted-fg">
            Ask about the project - answers cite spec clauses, RFIs and schedule activities.
          </p>
        )}
        {messages.map((m, i) =>
          m.role === 'user' ? (
            <div key={i} className="ml-auto max-w-[85%] w-fit bg-ink px-5 py-3 text-paper">
              <span className="font-body text-[15px]">{m.content}</span>
            </div>
          ) : (
            <div key={i} className="max-w-[92%] border-l-4 border-ink pl-5">
              <div className="mb-1.5 flex items-center gap-2 font-mono text-[9px] font-bold uppercase tracking-widest text-muted-fg">
                Oracle{m.intent && <span className="border border-line px-1.5 py-0.5 normal-case">{m.intent}</span>}
              </div>
              <Reveal text={m.content} animate={!!m.animate} onDone={() => settle(i)}
                onTick={() => endRef.current?.scrollIntoView({ behavior: 'auto' })} />
              {!m.animate && <Citations msg={m} />}
            </div>
          ),
        )}
        {busy && <div className="border-l-4 border-line pl-5"><Spinner label="traversing graph" /></div>}
        <div ref={endRef} />
      </div>

      <div className="space-y-3 border-t-2 border-ink pt-4">
        <div className="flex flex-wrap gap-1.5">
          {QUICK_QUERIES.map((q) => (
            <button key={q} onClick={() => ask(q)} disabled={busy}
              className="border border-ink px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider transition-colors duration-100 hover:bg-ink hover:text-paper disabled:opacity-40">
              {q}
            </button>
          ))}
        </div>
        <div className="flex gap-3">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && ask()}
            placeholder="Ask Oracle about the project…"
            className="flex-1 border-0 border-b-2 border-ink bg-transparent px-1 py-2.5 font-body text-base placeholder:italic placeholder:text-muted-fg focus:border-b-4 focus:outline-none"
          />
          <button onClick={() => ask()} disabled={busy || !input.trim()}
            className="bg-ink px-8 py-3 font-mono text-xs font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-2 hover:outline-ink disabled:opacity-40">
            Ask →
          </button>
        </div>
      </div>
    </div>
  )
}
