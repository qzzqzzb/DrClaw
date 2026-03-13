import { useEffect, useRef, useCallback } from "react"
import { useChatStore } from "../stores/chatStore"
import type { WsIncoming, ChatMessage } from "../lib/types"

const WS_URL = `ws://${window.location.host}/drclaw-ws`
const RECONNECT_BASE_MS = 2000
const RECONNECT_MAX_MS = 30000

let msgCounter = 0
function makeMsg(role: ChatMessage["role"], text: string): ChatMessage {
  return { id: String(++msgCounter), role, text, timestamp: Date.now() }
}

export function useWebSocket(enabled: boolean) {
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(RECONNECT_BASE_MS)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true

    if (!enabled) return

    const { appendMessage, setConnectionStatus } = useChatStore.getState()

    function connect() {
      if (!mountedRef.current) return
      setConnectionStatus("connecting")

      console.log("[WS] connecting to", WS_URL)
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => {
        console.log("[WS] connected")
        setConnectionStatus("connected")
        retryRef.current = RECONNECT_BASE_MS
      }

      ws.onmessage = (ev) => {
        let data: WsIncoming
        try {
          data = JSON.parse(ev.data)
        } catch {
          return
        }

        const activeAgentId = useChatStore.getState().activeAgentId

        if (data.type === "chat") {
          appendMessage(data.source, makeMsg("agent", data.text))
        } else if (data.type === "tool_call") {
          appendMessage(data.source, makeMsg("tool_call", data.text))
        } else if (data.type === "equipment_status" || data.type === "equipment_report") {
          const msg = makeMsg("routed", `[${data.type}] ${data.text}`)
          appendMessage(data.topic, msg)
          if (data.source !== data.topic) {
            appendMessage(data.source, makeMsg("routed", `[${data.type}] ${data.text}`))
          }
        } else if (data.type === "inbound") {
          const text = `${data.source} → ${data.topic}: ${data.text}`
          const msg = makeMsg("routed", text)
          appendMessage(data.source, msg)
          if (data.topic !== data.source) {
            appendMessage(data.topic, makeMsg("routed", text))
          }
        } else if (data.type === "error") {
          if (activeAgentId) {
            appendMessage(activeAgentId, makeMsg("error", data.text))
          }
        }
      }

      ws.onclose = () => {
        console.log("[WS] closed")
        setConnectionStatus("disconnected")
        wsRef.current = null
        if (!mountedRef.current) return
        const delay = retryRef.current
        retryRef.current = Math.min(delay * 2, RECONNECT_MAX_MS)
        timerRef.current = setTimeout(connect, delay)
      }

      ws.onerror = (e) => {
        console.error("[WS] error", e)
      }
    }

    connect()

    return () => {
      mountedRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
      if (wsRef.current) {
        wsRef.current.onclose = null // prevent reconnect on cleanup
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [enabled])

  const send = useCallback((agentId: string, text: string) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "chat", agent_id: agentId, text }))
    }
  }, [])

  return { send }
}
