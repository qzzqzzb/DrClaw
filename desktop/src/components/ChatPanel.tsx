import { useChatStore } from "../stores/chatStore"
import { MessageList } from "./MessageList"
import { ChatInput } from "./ChatInput"

interface Props {
  onSend: (agentId: string, text: string) => void
}

export function ChatPanel({ onSend }: Props) {
  const activeAgentId = useChatStore((s) => s.activeAgentId)
  const agents = useChatStore((s) => s.agents)
  const activeAgent = agents.find((a) => a.id === activeAgentId)

  return (
    <div className="flex flex-1 flex-col bg-[#fafafa]">
      {/* Header */}
      <div className="border-b border-[#e0e0e0] bg-white px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-800">
          {activeAgent ? activeAgent.label : "DrClaw"}
        </h2>
      </div>

      <MessageList />
      <ChatInput onSend={onSend} />
    </div>
  )
}
