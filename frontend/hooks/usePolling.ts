import { useRef, useCallback } from 'react'

export function usePolling(intervalMs: number) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const schedule = useCallback(
    (fn: () => void) => {
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(fn, intervalMs)
    },
    [intervalMs],
  )

  const cancel = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  return { schedule, cancel }
}
