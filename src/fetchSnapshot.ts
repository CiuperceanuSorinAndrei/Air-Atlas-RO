import { validateObservationDocument, type ObservationDocument } from './airQualityObservation.ts'
const maximumBytes = 2_000_000
export async function readSnapshot(response: Response): Promise<ObservationDocument> {
  if (!response.ok || !response.body) throw Error('Snapshot unavailable')
  const advertised = response.headers.get('content-length')
  if (advertised && Number(advertised) > maximumBytes) throw Error('Snapshot too large')
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let length = 0, text = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      length += value.byteLength
      if (length > maximumBytes) throw Error('Snapshot too large')
      text += decoder.decode(value, { stream: true })
    }
    text += decoder.decode()
    return validateObservationDocument(JSON.parse(text))
  } finally {
    await reader.cancel()
  }
}
