import { useEffect, useRef, useState, useCallback } from 'react'

export function useWebSocket(onEvent) {
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)
  const onEventRef = useRef(onEvent)

  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  const connect = useCallback(() => {
    try {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = window.location.host
      const wsUrl = `${protocol}//${host}/ws`

      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (onEventRef.current && data) {
            onEventRef.current(data)
          }
        } catch (e) {
          console.error('Failed to parse WebSocket message', e)
        }
      }

      ws.onclose = () => {
        setConnected(false)
        wsRef.current = null
        // Reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        setConnected(false)
        ws.close()
      }
    } catch (e) {
      setConnected(false)
      reconnectTimeoutRef.current = setTimeout(connect, 3000)
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [connect])

  const send = useCallback((msg) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
  }, [])

  return { connected, send }
}
