export function Spinner({ className = '' }: { className?: string }) {
  return <span className={`inline-block size-5 animate-spin rounded-pill border-2 border-sand border-t-orange ${className}`} />
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-control bg-cream ${className}`} />
}

export function PageLoading() {
  return (
    <div className="grid min-h-screen place-items-center bg-cream">
      <Spinner className="size-8" />
    </div>
  )
}
