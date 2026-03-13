import { useState, useCallback } from "react"
import { useChatStore } from "../stores/chatStore"
import type { ChatMessage } from "../lib/types"

let inputMsgId = 0

interface Props {
  onSend: (agentId: string, text: string) => void
}

export function ChatInput({ onSend }: Props) {
  const [text, setText] = useState("")
  const activeAgentId = useChatStore((s) => s.activeAgentId)
  const connectionStatus = useChatStore((s) => s.connectionStatus)
  const disabled = connectionStatus !== "connected" || !activeAgentId

  const send = useCallback(() => {
    const trimmed = text.trim()
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
  }, [text, activeAgentId, onSend])

  return (
    <div className="flex gap-2 border-t border-[#e0e0e0] bg-white p-3">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            send()
          }
        }}
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
