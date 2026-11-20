import React from "react"
import { cn } from "@/lib/utils"

export function PageContainer({ children, className }) {
  return (
    <main
      className={cn(
        "flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8 animate-in fade-in-50 duration-300",
        className
      )}
    >
      <div className="max-w-7xl mx-auto space-y-6">{children}</div>
    </main>
  )
}
