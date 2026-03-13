import type { Agent } from "../lib/types"

const typeBadge: Record<Agent["type"], string> = {
  assistant: "bg-[#89b4fa] text-[#1e1e2e]",
  student: "bg-[#a6e3a1] text-[#1e1e2e]",
  equipment: "bg-[#f9e2af] text-[#1e1e2e]",
  external: "bg-[#d1d5db] text-[#1e1e2e]",
}

const statusDot: Record<Agent["status"], string> = {
  idle: "bg-green-400",
  working: "bg-orange-400",
  error: "bg-red-400",
}

interface Props {
  agent: Agent
  active: boolean
  onClick: () => void
}

export function AgentItem({ agent, active, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors ${
        active ? "bg-[#45475a]" : "hover:bg-[#313244]"
      }`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${statusDot[agent.status]}`} />
      <span className="flex-1 truncate">{agent.label}</span>
      <span className={`rounded px-1 py-0.5 text-[10px] font-medium ${typeBadge[agent.type]}`}>
        {agent.type}
      </span>
    </button>
  )
}
