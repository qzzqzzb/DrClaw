import { create } from "zustand"
import type { Agent, ChatMessage, ConnectionStatus } from "../lib/types"

export interface ChatStore {
  agents: Agent[]
  activeAgentId: string | null
  messages: Record<string, ChatMessage[]>
  connectionStatus: ConnectionStatus

  setAgents: (agents: Agent[]) => void
  selectAgent: (id: string) => void
  appendMessage: (agentId: string, msg: ChatMessage) => void
  setConnectionStatus: (s: ConnectionStatus) => void
}

export const useChatStore = create<ChatStore>((set) => ({
  agents: [],
  activeAgentId: null,
  messages: {},
  connectionStatus: "disconnected",

  setAgents: (agents) => set({ agents }),

  selectAgent: (id) => set({ activeAgentId: id }),

  appendMessage: (agentId, msg) =>
    set((state) => ({
      messages: {
        ...state.messages,
        [agentId]: [...(state.messages[agentId] ?? []), msg],
      },
    })),

  setConnectionStatus: (connectionStatus) => set({ connectionStatus }),
}))
