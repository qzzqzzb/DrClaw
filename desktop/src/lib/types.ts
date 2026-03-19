export interface Agent {
  id: string
  label: string
  type: "assistant" | "project_manager" | "project_student" | "equipment" | "external"
  status: "idle" | "working" | "error"
  chat_enabled?: boolean
}

export interface ChatMessage {
  id: string
  role: "user" | "agent" | "routed" | "error" | "tool_call"
  text: string
  timestamp: number
}

/** Client-to-server WebSocket message */
export interface WsClientMessage {
  type: "chat"
  agent_id: string
  text: string
}

/** Server-to-client WebSocket message */
export interface WsServerMessage {
  type: "chat" | "inbound" | "tool_call" | "equipment_status" | "equipment_report"
  source: string
  topic: string
  text: string
  metadata?: Record<string, unknown>
}

/** Server-to-client error message */
export interface WsErrorMessage {
  type: "error"
  text: string
}

export type WsIncoming = WsServerMessage | WsErrorMessage

export type ConnectionStatus = "disconnected" | "connecting" | "connected"
