import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { MessageBubble } from "../MessageBubble"
import type { ChatMessage } from "../../lib/types"

function msg(role: ChatMessage["role"], text: string): ChatMessage {
  return { id: "1", role, text, timestamp: Date.now() }
}

describe("MessageBubble", () => {
  it("renders user message right-aligned with blue bg", () => {
    const { container } = render(<MessageBubble msg={msg("user", "hello")} />)
    const el = container.firstElementChild as HTMLElement
    expect(el.dataset.role).toBe("user")
    expect(el.className).toContain("bg-[#89b4fa]")
    expect(el.className).toContain("ml-auto")
    expect(screen.getByText("hello")).toBeTruthy()
  })

  it("renders agent message left-aligned with white bg and markdown", () => {
    const { container } = render(<MessageBubble msg={msg("agent", "**bold**")} />)
    const el = container.firstElementChild as HTMLElement
    expect(el.dataset.role).toBe("agent")
    expect(el.className).toContain("mr-auto")
    expect(el.className).toContain("bg-white")
    // Markdown renders <strong>
    expect(container.querySelector("strong")).toBeTruthy()
  })

  it("renders routed message centered and italic", () => {
    const { container } = render(<MessageBubble msg={msg("routed", "main → proj: hi")} />)
    const el = container.firstElementChild as HTMLElement
    expect(el.dataset.role).toBe("routed")
    expect(el.className).toContain("mx-auto")
    expect(el.className).toContain("italic")
    expect(el.className).toContain("bg-[#f5e0dc]")
  })

  it("renders error message centered with red bg", () => {
    const { container } = render(<MessageBubble msg={msg("error", "oops")} />)
    const el = container.firstElementChild as HTMLElement
    expect(el.dataset.role).toBe("error")
    expect(el.className).toContain("mx-auto")
    expect(el.className).toContain("bg-[#f38ba8]")
    expect(screen.getByText("oops")).toBeTruthy()
  })
})
