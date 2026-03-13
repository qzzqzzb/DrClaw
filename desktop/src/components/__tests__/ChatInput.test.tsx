import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { ChatInput } from "../ChatInput"
import { useChatStore } from "../../stores/chatStore"

describe("ChatInput", () => {
  beforeEach(() => {
    useChatStore.setState({
      agents: [],
      activeAgentId: "main",
      messages: {},
      connectionStatus: "connected",
    })
  })

  it("sends message on Enter", () => {
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)

    const input = screen.getByPlaceholderText("Type a message...")
    fireEvent.change(input, { target: { value: "hello" } })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(onSend).toHaveBeenCalledWith("main", "hello")
    // User message appended to store
    expect(useChatStore.getState().messages["main"]).toHaveLength(1)
    expect(useChatStore.getState().messages["main"]![0].role).toBe("user")
  })

  it("sends message on button click", () => {
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)

    const input = screen.getByPlaceholderText("Type a message...")
    fireEvent.change(input, { target: { value: "hi" } })
    fireEvent.click(screen.getByText("Send"))

    expect(onSend).toHaveBeenCalledWith("main", "hi")
  })

  it("is disabled when disconnected", () => {
    useChatStore.setState({ connectionStatus: "disconnected" })
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)

    const input = screen.getByPlaceholderText("Connecting...") as HTMLInputElement
    expect(input.disabled).toBe(true)
  })

  it("is disabled when no agent selected", () => {
    useChatStore.setState({ activeAgentId: null })
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)

    const input = screen.getByPlaceholderText("Connecting...") as HTMLInputElement
    expect(input.disabled).toBe(true)
  })

  it("does not send empty text", () => {
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)

    const input = screen.getByPlaceholderText("Type a message...")
    fireEvent.keyDown(input, { key: "Enter" })

    expect(onSend).not.toHaveBeenCalled()
  })
})
