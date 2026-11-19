import { cn } from "@/lib/utils"

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn(
        "bg-accent animate-pulse rounded-md motion-reduce:animate-none motion-reduce:opacity-60",
        className
      )}
      {...props}
    />
  )
}

/** Row-shaped placeholder that matches the list cards used across the app. */
function ListRowSkeleton({ className, lines = 3 }: { className?: string; lines?: number }) {
  return (
    <div
      aria-hidden
      className={cn("flex items-center gap-4 rounded-lg border p-4", className)}
    >
      <Skeleton className="h-10 w-10 shrink-0 rounded-full" />
      <div className="min-w-0 flex-1 space-y-2">
        <Skeleton className="h-4 w-2/5" />
        <Skeleton className="h-3 w-3/5" />
        {lines > 2 && <Skeleton className="h-3 w-1/4" />}
      </div>
    </div>
  )
}

export { ListRowSkeleton, Skeleton }
