import { useChatStore } from "../stores/chatStore"
import { AgentItem } from "./AgentItem"
import type { ConnectionStatus } from "../lib/types"

const statusColor: Record<ConnectionStatus, string> = {
  connected: "bg-green-400",
  connecting: "bg-yellow-400",
  disconnected: "bg-red-400",
}

export function Sidebar() {
  const agents = useChatStore((s) => s.agents)
  const activeAgentId = useChatStore((s) => s.activeAgentId)
  const connectionStatus = useChatStore((s) => s.connectionStatus)
  const selectAgent = useChatStore((s) => s.selectAgent)

  return (
    <aside className="flex h-full w-[250px] shrink-0 flex-col border-r border-[#313244] bg-[#1e1e2e] text-[#cdd6f4]">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-[#313244] px-4 py-3">
        <img
          src="/assets/DrClaw.png"
          alt="DrClaw"
          className="h-8 w-8 rounded object-cover"
        />
        <h1 className="text-xl font-bold text-[#89b4fa]">DrClaw</h1>
        <span
          className={`h-2.5 w-2.5 rounded-full ${statusColor[connectionStatus]}`}
          title={connectionStatus}
          data-testid="connection-dot"
        />
      </div>

      {/* Agent list */}
      <nav className="flex-1 overflow-y-auto p-2">
        {agents.map((a) => (
          <AgentItem
            key={a.id}
            agent={a}
            active={a.id === activeAgentId}
            onClick={() => selectAgent(a.id)}
          />
        ))}
      </nav>
    </aside>
  )
}
