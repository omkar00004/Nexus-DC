// Critical-path Gantt, monochrome, two modes:
//   simple (default)  - judge-readable: only REMAINING work, one bar per row,
//                       slip annotated in red on the bar, SLA + P90 markers only
//   detailed          - full QSRA view: all rows, baseline vs forecast bars,
//                       P50 marker, complete legend
const DAY = 86400000

function d(iso) {
  return iso ? new Date(iso.slice(0, 10)).getTime() : null
}

export default function Gantt({ activities, milestone, detailed = false }) {
  if (!activities?.length) {
    return <div className="font-mono text-xs uppercase tracking-widest text-muted-fg">No schedule data.</div>
  }

  let rows = activities.filter((a) => a.target_start || a.early_start)
  if (!detailed) rows = rows.filter((a) => a.status !== 'TK_Complete')

  const times = []
  rows.forEach((a) => {
    ;[a.target_start, a.target_end, a.early_start, a.early_end].forEach((x) => d(x) && times.push(d(x)))
  })
  const markers = []
  if (milestone?.baseline_finish) markers.push({ t: d(milestone.baseline_finish), label: 'PROMISED', dash: null, color: '#000' })
  if (detailed && milestone?.mc_p50) markers.push({ t: d(milestone.mc_p50), label: 'P50', dash: '6 4', color: '#000' })
  // P90 is the live risk state - the one place the Gantt uses alarm red
  if (milestone?.mc_p90) markers.push({ t: d(milestone.mc_p90), label: detailed ? 'P90' : 'WORST CASE', dash: '2 3', color: '#dc2626' })
  markers.forEach((m) => times.push(m.t))

  const t0 = Math.min(...times) - 7 * DAY
  const t1 = Math.max(...times) + (detailed ? 7 : 24) * DAY
  const ROW = detailed ? 26 : 34
  const W = 860, LABEL = detailed ? 230 : 250, H = rows.length * ROW + 46
  const x = (t) => LABEL + ((t - t0) / (t1 - t0)) * (W - LABEL - 10)

  const months = []
  const cur = new Date(t0)
  cur.setDate(1)
  while (cur.getTime() < t1) {
    months.push(new Date(cur))
    cur.setMonth(cur.getMonth() + 1)
  }

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <defs>
          <pattern id="hatch" patternUnits="userSpaceOnUse" width="5" height="5">
            <path d="M0 5 L5 0" stroke="#000" strokeWidth="1.2" />
          </pattern>
        </defs>
        {months.map((m, i) => (
          <g key={i}>
            <line x1={x(m.getTime())} x2={x(m.getTime())} y1={20} y2={H - 14} stroke="#e5e5e5" strokeWidth="1" />
            <text x={x(m.getTime()) + 4} y={12} fill="#525252" fontSize="9"
              fontFamily="JetBrains Mono, monospace" letterSpacing="1">
              {m.toLocaleDateString('en', { month: 'short' }).toUpperCase()} {String(m.getFullYear()).slice(2)}
            </text>
          </g>
        ))}
        {rows.map((a, i) => {
          const y = 26 + i * ROW
          const ts = d(a.target_start) ?? d(a.early_start)
          const te = d(a.target_end) ?? d(a.early_end)
          const es = d(a.early_start) ?? ts
          const ee = d(a.early_end) ?? te
          const mile = ts === te || es === ee
          const slipDays = d(a.early_end) && d(a.target_end)
            ? Math.round((d(a.early_end) - d(a.target_end)) / DAY) : 0
          const slipped = slipDays > 0
          const done = a.status === 'TK_Complete'
          const barY = detailed ? y + 10 : y + 8
          const barH = detailed ? 9 : 12
          return (
            <g key={a.task_code}>
              <text x={0} y={y + (detailed ? 13 : 16)} fontSize={detailed ? 10 : 11}>
                <tspan fill="#525252" fontFamily="JetBrains Mono, monospace">{a.task_code}</tspan>
                <tspan dx="6" fill="#000" fontFamily="Source Serif 4, serif">
                  {(a.name || '').slice(0, detailed ? 26 : 27)}
                </tspan>
              </text>
              {detailed && ts && te && !mile && (
                <rect x={x(ts)} y={y + 3} width={Math.max(x(te) - x(ts), 2)} height={5} fill="#e5e5e5" />
              )}
              {es && ee && (mile ? (
                <path d={`M ${x(ee)} ${barY - 3} l 8 8 l -8 8 l -8 -8 Z`} fill="#000" />
              ) : (
                <rect
                  x={x(es)} y={barY} width={Math.max(x(ee) - x(es), 2)} height={barH}
                  fill={done ? '#000' : slipped ? 'url(#hatch)' : a.status === 'TK_Active' ? '#000' : '#fff'}
                  stroke="#000" strokeWidth="1"
                />
              ))}
              {!detailed && slipped && !mile && (
                <text x={x(ee) + 6} y={barY + barH - 2} fontSize="10" fontWeight="bold" fill="#dc2626"
                  fontFamily="JetBrains Mono, monospace">
                  +{slipDays}d late
                </text>
              )}
            </g>
          )
        })}
        {markers.map((m, i) => (
          <g key={i}>
            <line x1={x(m.t)} x2={x(m.t)} y1={20} y2={H - 14} stroke={m.color} strokeWidth="1.5"
              strokeDasharray={m.dash || undefined} />
            <text x={x(m.t) + 4} y={H - 4} fontSize="9" fontWeight="bold" fill={m.color}
              fontFamily="JetBrains Mono, monospace" letterSpacing="1">{m.label}</text>
          </g>
        ))}
      </svg>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t border-line pt-2 font-mono text-[9px] uppercase tracking-widest text-muted-fg">
        {detailed && <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2 w-4 bg-line" /> baseline</span>}
        <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2 w-4 bg-ink" /> {detailed ? 'complete' : 'in progress'}</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2 w-4 border border-ink bg-paper" /> {detailed ? 'forecast' : 'planned work'}</span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2 w-4 border border-ink"
            style={{ backgroundImage: 'repeating-linear-gradient(45deg, transparent, transparent 2px, #000 2px, #000 3px)' }} /> running late
        </span>
        <span className="inline-flex items-center gap-1.5"><span className="font-bold text-ink">◆</span> finish milestone</span>
      </div>
    </div>
  )
}
