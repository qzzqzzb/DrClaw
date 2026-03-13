import { useEffect, useRef, useCallback } from "react"
import { useChatStore } from "../stores/chatStore"
import type { Agent } from "../lib/types"

const API_URL = "/api/agents"
const DEBOUNCE_MS = 3000

export function useAgents(enabled: boolean) {
  const setAgents = useChatStore((s) => s.setAgents)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchAgents = useCallback(async () => {
    try {
      const res = await fetch(API_URL)
      if (!res.ok) return
      const data: Agent[] = await res.json()
      setAgents(data)
    } catch {
      // daemon not ready yet or network error
    }
  }, [setAgents])

  // Debounced re-fetch: call this when new agent responses arrive
  const debouncedFetch = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(fetchAgents, DEBOUNCE_MS)
  }, [fetchAgents])

  // Initial fetch on enable
  useEffect(() => {
    if (enabled) {
      fetchAgents()
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [enabled, fetchAgents])

  return { fetchAgents, debouncedFetch }
}

/**
 * Poll GET /api/agents until the daemon responds.
 * Returns the initial agent list on success.
 */
export async function pollUntilReady(
  maxRetries = 60,
  intervalMs = 1000
): Promise<Agent[]> {
  for (let i = 0; i < maxRetries; i++) {
    try {
      const res = await fetch(API_URL)
      if (res.ok) {
        return await res.json()
      }
    } catch {
      // not ready yet
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error("Daemon did not start in time")
}
