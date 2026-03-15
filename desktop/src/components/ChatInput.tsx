import { useState, useCallback, useRef, useEffect } from "react"
import { useChatStore } from "../stores/chatStore"
import type { ChatMessage } from "../lib/types"

let inputMsgId = 0

interface Props {
  onSend: (agentId: string, text: string) => void
}

export function ChatInput({ onSend }: Props) {
  const [text, setText] = useState("")
  const textRef = useRef(text)
  const inputRef = useRef<HTMLInputElement>(null)
  const composingRef = useRef(false)
  const activeAgentId = useChatStore((s) => s.activeAgentId)
  const connectionStatus = useChatStore((s) => s.connectionStatus)
  const disabled = connectionStatus !== "connected" || !activeAgentId

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

  // Attach native DOM listeners to bypass React's synthetic event system,
  // which can reorder composition and keydown events.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return

    const onCompositionStart = () => { composingRef.current = true }
    const onCompositionEnd = () => {
      // Use setTimeout so the flag is still true when the keydown
      // that follows compositionend (in Chrome) is processed.
      setTimeout(() => { composingRef.current = false }, 0)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey && !composingRef.current && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault()
        send()
      }
    }

    el.addEventListener("compositionstart", onCompositionStart)
    el.addEventListener("compositionend", onCompositionEnd)
    el.addEventListener("keydown", onKeyDown)
    return () => {
      el.removeEventListener("compositionstart", onCompositionStart)
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
        placeholder={disabled ? "Connecting..." : "Type a message..."}
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
