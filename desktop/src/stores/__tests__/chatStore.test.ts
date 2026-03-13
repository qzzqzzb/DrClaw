import { describe, it, expect, beforeEach } from "vitest"
import { useChatStore } from "../chatStore"
import type { Agent, ChatMessage } from "../../lib/types"

function makeAgent(id: string, label: string, type: Agent["type"] = "assistant"): Agent {
  return { id, label, type, status: "idle" }
}

function makeMsg(id: string, role: ChatMessage["role"], text: string): ChatMessage {
  return { id, role, text, timestamp: Date.now() }
}

describe("chatStore", () => {
  beforeEach(() => {
    useChatStore.setState({
      agents: [],
      activeAgentId: null,
      messages: {},
      connectionStatus: "disconnected",
    })
  })

  it("selectAgent sets activeAgentId", () => {
    useChatStore.getState().selectAgent("proj-1")
    expect(useChatStore.getState().activeAgentId).toBe("proj-1")
  })

  it("setAgents replaces agent list", () => {
    const agents = [makeAgent("main", "Assistant Agent"), makeAgent("p1", "Student 1", "student")]
    useChatStore.getState().setAgents(agents)
    expect(useChatStore.getState().agents).toEqual(agents)

    const newAgents = [makeAgent("main", "Assistant Agent")]
    useChatStore.getState().setAgents(newAgents)
    expect(useChatStore.getState().agents).toHaveLength(1)
  })

  it("appendMessage adds to correct agent's history", () => {
    const msg = makeMsg("1", "user", "hello")
    useChatStore.getState().appendMessage("main", msg)

    expect(useChatStore.getState().messages["main"]).toEqual([msg])
    expect(useChatStore.getState().messages["other"]).toBeUndefined()
  })

  it("appendMessage creates agent history if not exists", () => {
    expect(useChatStore.getState().messages["new-agent"]).toBeUndefined()

    const msg = makeMsg("1", "agent", "hi")
    useChatStore.getState().appendMessage("new-agent", msg)

    expect(useChatStore.getState().messages["new-agent"]).toEqual([msg])
  })

  it("appendMessage appends to existing history", () => {
    const msg1 = makeMsg("1", "user", "hello")
    const msg2 = makeMsg("2", "agent", "world")

    useChatStore.getState().appendMessage("main", msg1)
    useChatStore.getState().appendMessage("main", msg2)

    expect(useChatStore.getState().messages["main"]).toEqual([msg1, msg2])
  })

  it("setConnectionStatus updates status", () => {
    expect(useChatStore.getState().connectionStatus).toBe("disconnected")

    useChatStore.getState().setConnectionStatus("connecting")
    expect(useChatStore.getState().connectionStatus).toBe("connecting")

    useChatStore.getState().setConnectionStatus("connected")
    expect(useChatStore.getState().connectionStatus).toBe("connected")
  })
})
