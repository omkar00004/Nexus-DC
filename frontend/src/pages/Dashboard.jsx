import { useCallback, useEffect, useState } from 'react'
import { api, riskBand, riskColor } from '../api'
import { useRun } from '../runstate.jsx'
import { Card, Md, MetricCard, SeverityBadge, Spinner } from '../components/ui.jsx'
import Gantt from '../components/Gantt.jsx'

// The design system's "inverted card": the convergence alert IS the black block.
function ConvergenceAlert({ alert }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="bg-ink text-paper">
      <button onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-baseline gap-x-4 gap-y-1 px-6 py-5 text-left">
        <span className="h-3.5 w-3.5 shrink-0 animate-pulse self-center bg-alarm" />
        <span className="font-display text-2xl font-black tracking-tight md:text-3xl">
          CONVERGENCE - {alert.entity_id}
        </span>
        <span className="font-mono text-[11px] uppercase tracking-widest text-paper/60"
          title={`convergence score ${alert.convergence_score} (threshold ${alert.threshold}, critical path ×${alert.criticality_weight})`}>
          <span className="font-bold text-paper">{alert.agents.length} agents agree</span>
          {' '}· {alert.agents.join(' + ')}
          {alert.on_critical_path && ' · on the critical path'}
          {alert.sla_exposure && (
            <span className="font-bold text-alarm">
              {' '}· {alert.sla_exposure.delay_days} days late ·{' '}
              ${(alert.sla_exposure.exposure_usd / 1e6).toFixed(1)}M at stake
            </span>
          )}
        </span>
        <span className="ml-auto flex h-10 w-10 shrink-0 items-center justify-center self-center border-2 border-paper/50 font-mono text-2xl font-bold text-paper">
          {open ? '−' : '+'}
        </span>
      </button>
      {open && (
        <div className="space-y-6 border-t border-paper/25 px-6 py-6">
          <div className="grid gap-px bg-paper/25 sm:grid-cols-3">
            {Object.entries(alert.agent_signals).map(([agent, s]) => (
              <div key={agent} className="bg-ink p-5">
                <div className="flex items-baseline justify-between">
                  <span className="font-mono text-sm font-bold uppercase tracking-widest">{agent}</span>
                  <span className={`font-display text-3xl font-bold ${
                    riskBand(s.risk_score) === 'CRITICAL' ? 'text-alarm' : ''
                  }`}>{riskBand(s.risk_score)}</span>
                </div>
                <p className="mt-2 font-body text-[15px] leading-relaxed text-paper/85">{s.description}</p>
                <div className="mt-2 font-mono text-[9px] uppercase tracking-wider text-paper/40">
                  risk score {s.risk_score}
                </div>
              </div>
            ))}
          </div>
          <div>
            <h3 className="mb-2 font-mono text-[11px] font-bold uppercase tracking-widest text-paper/50">
              Unified narrative
            </h3>
            <Md inverted text={alert.narrative} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <div>
              <h3 className="mb-2 font-mono text-[11px] font-bold uppercase tracking-widest text-paper/50">
                Root cause
              </h3>
              <p className="font-body text-base leading-relaxed text-paper/90">{alert.root_cause}</p>
            </div>
            <div>
              <h3 className="mb-2 font-mono text-[11px] font-bold uppercase tracking-widest text-paper/50">
                Combined impact
              </h3>
              <p className="font-body text-base leading-relaxed text-paper/90">{alert.combined_impact}</p>
            </div>
          </div>
          {/* double inversion: white block inside the black card for the number */}
          <div className="border-2 border-paper bg-paper px-5 py-4 text-ink">
            <div className="font-display text-3xl font-black tracking-tight md:text-4xl">
              ${alert.sla_exposure.exposure_usd.toLocaleString()}
            </div>
            <div className="mt-1 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
              SLA exposure · {alert.sla_exposure.delay_days} days × $
              {alert.sla_exposure.penalty_per_day_usd.toLocaleString()}/day
            </div>
            <div className="mt-1.5 text-[12px] italic text-muted-fg">{alert.sla_exposure.assumption}</div>
          </div>
          <div>
            <h3 className="mb-2 font-mono text-[11px] font-bold uppercase tracking-widest text-paper/50">
              Mitigation options
            </h3>
            <ol className="space-y-2.5">
              {alert.mitigation_options.map((m, i) => (
                <li key={i} className="flex gap-4 font-body text-base leading-relaxed text-paper/90">
                  <span className="font-display text-2xl font-bold leading-none">{String(i + 1).padStart(2, '0')}</span>
                  <span>{m}</span>
                </li>
              ))}
            </ol>
          </div>
          <div className="border-t border-paper/25 pt-4 font-mono text-[9px] uppercase tracking-wider text-paper/40">
            Engineering view: convergence score {alert.convergence_score} = Σ agent risk scores ×{' '}
            {alert.criticality_weight} (critical path) · fires at ≥ {alert.threshold} · {alert.threshold_note}
          </div>
        </div>
      )}
    </div>
  )
}

// translates SLA/P50/P90 statistics into one sentence a judge reads in 3 seconds
function ScheduleVerdict({ milestone }) {
  const fmt = (iso) => iso && new Date(iso).toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' })
  const lateDays = milestone.expected_delay_days
  const breachPct = Math.round((milestone.sla_breach_risk || 0) * 100)
  return (
    <div className="mb-5 border-2 border-ink">
      <div className="grid divide-y divide-line sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        <div className="px-4 py-3">
          <div className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">Promised finish</div>
          <div className="font-display text-xl font-bold">{fmt(milestone.baseline_finish)}</div>
        </div>
        <div className="px-4 py-3">
          <div className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">Expected finish (P50)</div>
          <div className="font-display text-xl font-bold">{fmt(milestone.mc_p50)}</div>
          <div className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">
            ~{Math.round(lateDays / 7)} weeks late
          </div>
        </div>
        <div className="px-4 py-3">
          <div className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">Worst case (P90)</div>
          <div className="font-display text-xl font-bold text-alarm">{fmt(milestone.mc_p90)}</div>
          <div className="font-mono text-[9px] uppercase tracking-widest text-muted-fg">9-in-10 runs finish by this date</div>
        </div>
        <div className="bg-ink px-4 py-3 text-paper">
          <div className="font-mono text-[9px] uppercase tracking-widest text-paper/60">Chance of missing the promise</div>
          <div className="font-display text-xl font-bold">{breachPct}%</div>
          <div className="font-mono text-[9px] uppercase tracking-widest text-paper/60">from 5,000 simulations</div>
        </div>
      </div>
    </div>
  )
}

export default function Dashboard() {
  // run state lives above the router (runstate.jsx) so the analysis - and
  // its banner - survives switching tabs mid-run
  const { running, elapsed, lastRun, runAll } = useRun()
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  const [ganttDetailed, setGanttDetailed] = useState(false)
  const [openEvent, setOpenEvent] = useState(null)

  const refresh = useCallback(() => {
    api.dashboardSummary().then(setSummary).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    refresh()
    // poll fast while agents run so the live event count ticks visibly;
    // also refreshes immediately when a run finishes (running -> false)
    const t = setInterval(refresh, running ? 3000 : 8000)
    return () => clearInterval(t)
  }, [refresh, running])

  if (error && !summary) {
    return <div className="border-2 border-ink p-6 font-mono text-sm">FAILED TO REACH API - {error}</div>
  }
  if (!summary) return <Spinner label="Loading project state" />

  const { metrics, equipment, deviations, milestone, critical_path, convergence, recent_events } = summary
  const basis = metrics.hours_saved_basis

  return (
    <div className="space-y-10">
      {/* editorial masthead */}
      <div className="border-b-4 border-ink pb-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h1 className="font-display text-6xl font-black leading-none tracking-tighter md:text-7xl">
            Risk.
          </h1>
          <div className="flex items-center gap-4">
            {convergence.risk_storm && (
              <span className="animate-pulse bg-alarm px-3 py-2 font-mono text-[11px] font-bold uppercase tracking-widest text-paper">
                Risk storm - {convergence.converged_entities.length} entities
              </span>
            )}
            <button onClick={runAll} disabled={running}
              className="bg-ink px-8 py-4 font-mono text-xs font-bold uppercase tracking-widest text-paper transition-none hover:bg-paper hover:text-ink hover:outline hover:outline-2 hover:outline-ink disabled:opacity-40">
              {running ? `Running… ${elapsed}s` : 'Run all agents →'}
            </button>
          </div>
        </div>
        <p className="mt-2 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[10px] uppercase tracking-widest text-muted-fg">
          <span>Five agents · one knowledge graph · every figure computed from source</span>
          {lastRun && (
            <span className="text-ink">
              Last full analysis: <strong>{lastRun.total}s</strong>
              {' '}(SPECTRA {lastRun.spectra}s · CHRONOS {lastRun.chronos}s ·
              TRACIS {lastRun.tracis}s · Convergence {lastRun.convergence}s)
            </span>
          )}
        </p>
      </div>

      {summary.cache_present === false && !running && (
        <div className="border-2 border-ink bg-muted px-6 py-4">
          <div className="font-mono text-[11px] font-bold uppercase tracking-widest">
            No analysis yet - parsed cache is empty
          </div>
          <div className="mt-1 font-body text-[14px] leading-relaxed text-muted-fg">
            Click <strong className="text-ink">Run all agents</strong> to parse the source documents,
            rebuild the cache and knowledge graph, and derive every figure below from scratch.
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-px bg-ink/0 md:grid-cols-4 xl:grid-cols-7 xl:gap-3">
        <MetricCard label="Convergence alerts" value={metrics.convergence_alerts}
          tone={metrics.convergence_alerts ? 'danger' : 'default'} />
        <MetricCard label="Equipment at risk" value={metrics.equipment_at_risk} />
        <MetricCard label="Spec deviations" value={metrics.open_deviations} sub="derived by SPECTRA" />
        <MetricCard label="Open RFIs" value={metrics.open_rfis} />
        <MetricCard label="Open NCRs" value={metrics.open_ncrs ?? 0} sub="field-raised" />
        <MetricCard label="Bus events" value={metrics.events_total} />
        <MetricCard label="Hours saved" value={metrics.hours_saved}
          sub={`${basis.submittals.n}×${basis.submittals.hours_each}h + ${basis.rfis.n}×${basis.rfis.hours_each}h + ${basis.itps.n}×${basis.itps.hours_each}h`} />
      </div>

      {convergence.alerts.map((a) => <ConvergenceAlert key={a.alert_id} alert={a} />)}

      <div className="grid gap-8 xl:grid-cols-3">
        <Card title={ganttDetailed ? 'Critical path - baseline vs forecast' : 'What is left, and where it lands'}
          className="xl:col-span-2"
          right={
            <div className="flex">
              {[['Simple', false], ['Detailed', true]].map(([label, val]) => (
                <button key={label} onClick={() => setGanttDetailed(val)}
                  className={`px-3 py-1 font-mono text-[10px] font-bold uppercase tracking-widest transition-none ${
                    ganttDetailed === val ? 'bg-ink text-paper' : 'text-muted-fg hover:text-ink'
                  }`}>
                  {label}
                </button>
              ))}
            </div>
          }>
          {milestone && <ScheduleVerdict milestone={milestone} />}
          <Gantt activities={critical_path} milestone={milestone} detailed={ganttDetailed} />
        </Card>

        <div className="space-y-8">
          <Card title="Equipment risk">
            <div className="divide-y divide-line">
              {equipment.map((e) => (
                <div key={e.tag} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex items-baseline justify-between">
                    <span className="font-mono text-sm font-bold">{e.tag}</span>
                    <span className="text-right">
                      <SeverityBadge severity={riskBand(e.risk_score)} />
                      <span className="ml-2 font-mono text-[9px] uppercase tracking-wider text-muted-fg">
                        {e.risk_score.toFixed(2)}
                      </span>
                    </span>
                  </div>
                  {e.vendor && (
                    <div className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">{e.vendor}</div>
                  )}
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {Object.entries(e.agent_risks).map(([a, s]) => (
                      <span key={a} title={`risk score ${s}`}
                        className="border border-line px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-muted-fg">
                        {a} <span className={riskColor(s)}>{riskBand(s)}</span>
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Derived deviations">
            <div className="space-y-3">
              {deviations.length === 0 && (
                <div className="font-mono text-xs uppercase tracking-widest text-muted-fg">
                  None - submittal compliant.
                </div>
              )}
              {deviations.map((d, i) => (
                <div key={i} className="flex items-start gap-3 border-b border-line pb-3 text-sm last:border-0 last:pb-0">
                  <SeverityBadge severity={d.severity} />
                  <div>
                    <span className="font-mono text-xs font-bold">{d.clause_id}</span>{' '}
                    <span className="italic">{d.parameter}</span>
                    <div className="text-[13px] text-muted-fg">
                      submitted <strong className="text-ink">{d.submitted}</strong> vs required{' '}
                      <strong className="text-ink">{d.required}</strong>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <Card title="Event ledger"
        right={<span className="font-mono text-[10px] uppercase tracking-widest text-muted-fg">click a row for detail · data/events.json</span>}>
        <div className="max-h-80 overflow-y-auto">
          {recent_events.length === 0 && (
            <div className="font-mono text-xs uppercase tracking-widest text-muted-fg">
              No events yet - run the agents.
            </div>
          )}
          <table className="w-full text-left">
            <tbody className="divide-y divide-line">
              {recent_events.map((e, i) => [
                <tr key={i} onClick={() => setOpenEvent(openEvent === i ? null : i)}
                  className="cursor-pointer font-mono text-[11px] hover:bg-muted">
                  <td className="whitespace-nowrap py-2 pr-4 text-muted-fg">{e.ts?.slice(11, 19)}</td>
                  <td className="py-2 pr-4 font-bold">{e.agent}</td>
                  <td className="py-2 pr-4"><SeverityBadge severity={e.severity} /></td>
                  <td className="py-2 pr-4 font-bold">{e.entity_id}</td>
                  <td className="max-w-md truncate py-2 pr-4 font-body text-[13px] text-muted-fg">{e.description}</td>
                  <td className={`py-2 text-right ${riskColor(e.risk_score)}`}
                    title={`risk score ${e.risk_score}`}>{riskBand(e.risk_score)}</td>
                </tr>,
                openEvent === i && (
                  <tr key={`${i}-detail`}>
                    <td colSpan={6} className="bg-muted px-4 py-4">
                      <p className="font-body text-[14px] leading-relaxed text-ink">{e.description}</p>
                      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[10px] uppercase tracking-wider text-muted-fg">
                        <span>agent <strong className="text-ink">{e.agent}</strong></span>
                        <span>type <strong className="text-ink">{e.event_type}</strong></span>
                        <span>entity <strong className="text-ink">{e.entity_id}</strong> ({e.entity_type})</span>
                        <span>severity <strong className="text-ink">{e.severity}</strong></span>
                        <span>risk score <strong className="text-ink">{e.risk_score}</strong></span>
                        {e.ref && <span>ref <strong className="text-ink">{e.ref}</strong></span>}
                        <span>{e.ts?.replace('T', ' ').slice(0, 19)} UTC</span>
                      </div>
                    </td>
                  </tr>
                ),
              ])}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
