import { useEffect, useState } from "react"
import { invoke } from "@tauri-apps/api/core"
import { useChatStore } from "./stores/chatStore"
import { useWebSocket } from "./hooks/useWebSocket"
import { useAgents, pollUntilReady } from "./hooks/useAgents"
import { Sidebar } from "./components/Sidebar"
import { ChatPanel } from "./components/ChatPanel"

type BootState = "starting" | "ready" | "error"

function App() {
  const [boot, setBoot] = useState<BootState>("starting")
  const [bootError, setBootError] = useState("")
  const daemonReady = boot === "ready"

  const { send } = useWebSocket(daemonReady)
  useAgents(daemonReady)

  useEffect(() => {
    let cancelled = false

    async function bootDaemon() {
      try {
        await invoke("start_daemon")
      } catch (e) {
        console.warn("start_daemon invoke failed:", e)
        // Daemon may already be running externally — try polling anyway
      }
      try {
        const initialAgents = await pollUntilReady()
        if (cancelled) return
        useChatStore.getState().setAgents(initialAgents)
        setBoot("ready")
      } catch (e) {
        if (cancelled) return
        setBootError(String(e))
        setBoot("error")
      }
    }

    bootDaemon()
    return () => {
      cancelled = true
    }
  }, [])

  if (boot === "starting") {
    return (
      <div className="flex h-screen items-center justify-center bg-[#1e1e2e] text-[#cdd6f4]">
        <p className="text-lg">Starting daemon...</p>
      </div>
    )
  }

  if (boot === "error") {
    return (
      <div className="flex h-screen items-center justify-center bg-[#1e1e2e] text-[#f38ba8]">
        <div className="text-center">
          <p className="text-lg font-bold">Failed to start daemon</p>
          <p className="mt-2 text-sm text-[#cdd6f4]">{bootError}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <ChatPanel onSend={send} />
    </div>
  )
}

export default App
