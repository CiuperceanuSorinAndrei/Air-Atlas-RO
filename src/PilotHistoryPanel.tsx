import './PilotHistoryPanel.css'
import { useEffect, useState } from 'react'
import { fetchPilotHistory, pilotHours, type PilotHistory as History } from './pilotHistory'
import { formatTime } from './stationView'

const utcHour = (value: string) =>
  new Date(value).toLocaleString('ro-RO', {
    timeZone: 'UTC',
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
const utcClock = (value: string) =>
  new Date(value).toLocaleTimeString('ro-RO', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' })
const states: Record<string, string> = { validated: 'validată', certified: 'certificată', raw: 'brută', preliminary: 'preliminară' }

export default function PilotHistoryPanel() {
  const [open, setOpen] = useState(false)
  const [history, setHistory] = useState<History | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!open || history) return
    let cancelled = false
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 15_000)
    fetchPilotHistory(import.meta.env.BASE_URL, controller.signal)
      .then((next) => {
        setHistory(next)
        setFailed(false)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
      .finally(() => window.clearTimeout(timeout))
    return () => {
      cancelled = true
      window.clearTimeout(timeout)
      controller.abort()
    }
  }, [open, history])
  return (
    <details className="data-details pilot-history" onToggle={(event) => {
        setOpen(event.currentTarget.open)
        if (event.currentTarget.open) setFailed(false)
      }}>
      <summary>Istoric pilot · DJ-1 · NO₂</summary>
      <div className="data-context">
        <p>
          Pilot local limitat: o serie istorică orară dintr-o captură verificată, fără colectare recurentă. Nu este
          serviciul public.
        </p>
        {!history && !failed && <p role="status">Se încarcă istoricul…</p>}
        {failed && !history && (
          <p className="data-error" role="status">
            Istoricul pilot nu este disponibil acum.
          </p>
        )}
        {history && (
          <>
            <table className="pilot-table">
              <caption>
                Fereastră: {utcHour(history.windowStart)} – {utcClock(history.windowEnd)} UTC
              </caption>
              <thead>
                <tr>
                  <th scope="col">Interval UTC</th>
                  <th scope="col">Ora României</th>
                  <th scope="col">Valoare</th>
                  <th scope="col">Sursă</th>
                  <th scope="col">Stare</th>
                </tr>
              </thead>
              <tbody>
                {pilotHours(history).map(({ observedFrom, observedTo, observation }) => (
                  <tr key={observedTo} className={observation ? undefined : 'is-missing'}>
                    <th scope="row">
                      {utcClock(observedFrom)}–{utcClock(observedTo)}
                    </th>
                    <td>până la {formatTime(observedTo)}</td>
                    {observation ? (
                      <>
                        <td title={`${observation.value} µg/m³`}>
                          {Number(observation.value).toLocaleString('ro-RO', { maximumFractionDigits: 2 })} µg/m³
                        </td>
                        <td>{observation.source}</td>
                        <td>
                          {observation.validationState
                            ? (states[observation.validationState] ?? observation.validationState)
                            : 'nedeclarată'}
                          <br />
                          <small>preluată {formatTime(observation.ingestedAt)}</small>
                        </td>
                      </>
                    ) : (
                      <td colSpan={3}>Fără observație acceptată · cauză necunoscută</td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <p>
              O oră lipsă înseamnă doar că nu există o observație acceptată în stocare: poate fi necolectată, respinsă,
              ambiguă sau absentă la sursă.
            </p>
            <p>
              {history.sources.length === 0
                ? 'Nicio sursă nu are drept de afișare înregistrat.'
                : history.sources.map((source) => (
                    <span key={source.code}>
                      {source.code}: <a href={source.licenceUrl}>{source.attributionText}</a>{' '}
                    </span>
                  ))}
            </p>
          </>
        )}
      </div>
    </details>
  )
}
