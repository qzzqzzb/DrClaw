import { describe, it, expect, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { Sidebar } from "../Sidebar"
import { useChatStore } from "../../stores/chatStore"
import type { Agent } from "../../lib/types"

function makeAgent(id: string, label: string, type: Agent["type"] = "assistant"): Agent {
  return { id, label, type, status: "idle" }
}

describe("Sidebar", () => {
  beforeEach(() => {
    useChatStore.setState({
      agents: [makeAgent("main", "Assistant Agent", "assistant"), makeAgent("p1", "Student 1", "student")],
      activeAgentId: null,
      messages: {},
      connectionStatus: "connected",
    })
  })

  it("renders agent list", () => {
    render(<Sidebar />)
    expect(screen.getByText("Assistant Agent")).toBeTruthy()
    expect(screen.getByText("Student 1")).toBeTruthy()
  })

  it("click selects agent", () => {
    render(<Sidebar />)
    fireEvent.click(screen.getByText("Assistant Agent"))
    expect(useChatStore.getState().activeAgentId).toBe("main")
  })

  it("shows connection status dot green when connected", () => {
    render(<Sidebar />)
    const dot = screen.getByTestId("connection-dot")
    expect(dot.className).toContain("bg-green-400")
  })

  it("shows connection status dot yellow when connecting", () => {
    useChatStore.setState({ connectionStatus: "connecting" })
    render(<Sidebar />)
    const dot = screen.getByTestId("connection-dot")
    expect(dot.className).toContain("bg-yellow-400")
  })

  it("shows connection status dot red when disconnected", () => {
    useChatStore.setState({ connectionStatus: "disconnected" })
    render(<Sidebar />)
    const dot = screen.getByTestId("connection-dot")
    expect(dot.className).toContain("bg-red-400")
  })

  it("shows DrClaw header", () => {
    render(<Sidebar />)
    expect(screen.getByText("DrClaw")).toBeTruthy()
  })
})
