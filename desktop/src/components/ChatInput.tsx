import { useState, useCallback, useRef, useEffect } from "react"
import { useChatStore } from "../stores/chatStore"
import type { ChatMessage } from "../lib/types"

let inputMsgId = 0

const IME_DEBOUNCE_MS = 200

interface Props {
  onSend: (agentId: string, text: string) => void
}

export function ChatInput({ onSend }: Props) {
  const [text, setText] = useState("")
  const textRef = useRef(text)
  const inputRef = useRef<HTMLInputElement>(null)
  const lastCompositionEndRef = useRef(0)
  const activeAgentId = useChatStore((s) => s.activeAgentId)
  const activeAgent = useChatStore((s) => s.agents.find((agent) => agent.id === s.activeAgentId) ?? null)
  const connectionStatus = useChatStore((s) => s.connectionStatus)
  const chatEnabled = activeAgent?.chat_enabled !== false
  const disabled = connectionStatus !== "connected" || !activeAgentId || !chatEnabled

  textRef.current = text

  const send = useCallback(() => {
    const trimmed = textRef.current.trim()
    if (!trimmed || !activeAgentId) return

    const msg: ChatMessage = {
      id: `user-${++inputMsgId}`,
      role: "user",
      text: trimmed,
      timestamp: Date.now(),
    }
    useChatStore.getState().appendMessage(activeAgentId, msg)
    onSend(activeAgentId, trimmed)
    setText("")
  }, [activeAgentId, onSend])

  useEffect(() => {
    const el = inputRef.current
    if (!el) return

    const onCompositionEnd = () => {
      lastCompositionEndRef.current = Date.now()
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.shiftKey) return

      // Block send if any IME signal is active
      if (e.isComposing || e.keyCode === 229) return

      // Block send if compositionend just fired (within debounce window).
      // This catches Chrome/Electron where compositionend precedes keydown
      // and isComposing is already false.
      if (Date.now() - lastCompositionEndRef.current < IME_DEBOUNCE_MS) return

      e.preventDefault()
      send()
    }

    el.addEventListener("compositionend", onCompositionEnd)
    el.addEventListener("keydown", onKeyDown)
    return () => {
      el.removeEventListener("compositionend", onCompositionEnd)
      el.removeEventListener("keydown", onKeyDown)
    }
  }, [send])

  return (
    <div className="flex gap-2 border-t border-[#e0e0e0] bg-white p-3">
      <input
        ref={inputRef}
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={disabled}
        placeholder={
          connectionStatus !== "connected"
            ? "Connecting..."
            : !activeAgentId
              ? "Select an agent..."
              : !chatEnabled
                ? "This agent is view-only. Send work to the project manager."
                : "Type a message..."
        }
        className="flex-1 rounded-lg border border-[#e0e0e0] px-3 py-2 text-sm text-zinc-900 outline-none focus:border-[#89b4fa] disabled:opacity-50"
      />
      <button
        onClick={send}
        disabled={disabled || !text.trim()}
        className="rounded-lg bg-[#89b4fa] px-4 py-2 text-sm font-medium text-[#1e1e2e] transition-colors hover:bg-[#74c7ec] disabled:opacity-50"
      >
        Send
      </button>
    </div>
  )
}
