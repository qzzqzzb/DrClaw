import { useEffect, useRef } from "react"
import { useChatStore } from "../stores/chatStore"
import { MessageBubble } from "./MessageBubble"
import type { ChatMessage } from "../lib/types"

const EMPTY: ChatMessage[] = []

export function MessageList() {
  const activeAgentId = useChatStore((s) => s.activeAgentId)
  const messages = useChatStore((s) =>
    s.activeAgentId ? (s.messages[s.activeAgentId] ?? EMPTY) : EMPTY
  )
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages.length])

  if (!activeAgentId) {
    return (
      <div className="flex flex-1 items-center justify-center text-zinc-400">
        Select an agent to start chatting
      </div>
    )
  }

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-zinc-400">
        No messages yet
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-4">
      {messages.map((m) => (
        <MessageBubble key={m.id} msg={m} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
