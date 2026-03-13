import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { ChatMessage } from "../lib/types"

const roleStyles: Record<ChatMessage["role"], string> = {
  user: "ml-auto bg-[#89b4fa] text-[#1e1e2e] rounded-2xl rounded-br-sm",
  agent: "mr-auto bg-white text-zinc-900 border border-[#e0e0e0] rounded-2xl rounded-bl-sm",
  routed: "mx-auto bg-[#f5e0dc] text-zinc-600 italic rounded-xl text-center text-xs",
  error: "mx-auto bg-[#f38ba8] text-white rounded-xl text-center text-sm",
  tool_call:
    "mx-auto bg-[#94e2d5]/20 text-[#94e2d5] border border-[#94e2d5]/30 rounded-lg text-center text-xs font-mono",
}

export function MessageBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className={`max-w-[75%] px-3 py-2 ${roleStyles[msg.role]}`} data-role={msg.role}>
      {msg.role === "agent" ? (
        <div className="prose prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
        </div>
      ) : (
        <span className="whitespace-pre-wrap break-words">{msg.text}</span>
      )}
    </div>
  )
}
