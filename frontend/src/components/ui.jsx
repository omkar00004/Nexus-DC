import { severityColor } from '../api'

export function Card({ title, right, children, className = '' }) {
  return (
    <section className={`border border-ink bg-paper ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-4 border-b border-ink px-5 py-3">
          <h2 className="font-mono text-[11px] font-medium uppercase tracking-widest text-ink">
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

export function MetricCard({ label, value, sub, tone = 'default' }) {
  // inversion for emphasis - the "danger" metric is the black card
  const inverted = tone === 'danger'
  return (
    <div className={`border px-4 py-3 transition-colors duration-100 ${
      inverted ? 'border-ink bg-ink text-paper' : 'border-ink bg-paper text-ink'
    }`}>
      <div className={`font-mono text-[10px] font-medium uppercase tracking-widest ${
        inverted ? 'text-paper/70' : 'text-muted-fg'
      }`}>
        {label}
      </div>
      <div className="mt-1 font-display text-4xl font-bold leading-none tabular-nums tracking-tight">
        {value}
      </div>
      {sub && (
        <div className={`mt-1.5 font-mono text-[10px] ${inverted ? 'text-paper/60' : 'text-muted-fg'}`}>
          {sub}
        </div>
      )}
    </div>
  )
}

export function SeverityBadge({ severity }) {
  const cls = severityColor[severity] || severityColor.INFO
  return (
    <span className={`inline-block border px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-widest ${cls}`}>
      {severity}
    </span>
  )
}

export function Spinner({ label }) {
  return (
    <span className="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-widest text-muted-fg">
      <span className="h-3.5 w-3.5 animate-spin border-2 border-line border-t-ink" />
      {label}
    </span>
  )
}

// minimal markdown: **bold**, `code`, bullet lines (* / -), paragraphs
export function Md({ text, inverted = false }) {
  if (!text) return null
  const strongCls = inverted ? 'font-bold text-paper' : 'font-bold text-ink'
  const codeCls = inverted
    ? 'bg-paper/10 px-1 font-mono text-[0.85em] text-paper'
    : 'bg-muted px-1 font-mono text-[0.85em] text-ink'
  const inline = (s) =>
    s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
      if (part.startsWith('**')) return <strong key={i} className={strongCls}>{part.slice(2, -2)}</strong>
      if (part.startsWith('`')) return <code key={i} className={codeCls}>{part.slice(1, -1)}</code>
      return part
    })
  const lines = String(text).split('\n')
  const blocks = []
  let list = null
  lines.forEach((line, i) => {
    const m = line.match(/^\s*[*-]\s+(.*)/)
    if (m) {
      list = list || []
      list.push(m[1])
    } else {
      if (list) { blocks.push({ type: 'ul', items: list }); list = null }
      if (line.trim()) blocks.push({ type: 'p', text: line })
    }
    if (i === lines.length - 1 && list) blocks.push({ type: 'ul', items: list })
  })
  return (
    <div className={`space-y-2 font-body text-[15px] leading-relaxed ${
      inverted ? 'text-paper/90' : 'text-ink/90'
    }`}>
      {blocks.map((b, i) =>
        b.type === 'ul' ? (
          <ul key={i} className={`ml-5 list-['-__'] space-y-1 ${inverted ? 'marker:text-paper/50' : 'marker:text-muted-fg'}`}>
            {b.items.map((it, j) => <li key={j} className="pl-1">{inline(it)}</li>)}
          </ul>
        ) : (
          <p key={i}>{inline(b.text)}</p>
        ),
      )}
    </div>
  )
}
