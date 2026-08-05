import { cn } from "@/lib/utils";

/**
 * PTC / Aerolloy mark: precision turbine impeller logo with vibrant PTC Red styling.
 */
export function AerolloyMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("h-9 w-9", className)}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="ptc-red-blade" x1="8" y1="4" x2="40" y2="44">
          <stop offset="0%" stopColor="#F87171" />
          <stop offset="50%" stopColor="#DC2626" />
          <stop offset="100%" stopColor="#991B1B" />
        </linearGradient>
        <linearGradient id="ptc-gold-core" x1="18" y1="18" x2="30" y2="30">
          <stop offset="0%" stopColor="#FBBF24" />
          <stop offset="100%" stopColor="#D97706" />
        </linearGradient>
      </defs>

      {/* Six precision turbine blades */}
      <g fill="url(#ptc-red-blade)">
        {[0, 60, 120, 180, 240, 300].map((angle) => (
          <path
            key={angle}
            d="M24 5.5c3.9 1.6 6.4 4.7 7.2 9.1.5 2.9-.3 5.6-2.3 8-1.4 1.7-3.1 2.7-4.9 3.1v-20.2z"
            transform={`rotate(${angle} 24 24)`}
            opacity={0.95}
          />
        ))}
      </g>

      <circle
        cx="24"
        cy="24"
        r="7.5"
        fill="url(#ptc-gold-core)"
        opacity="0.95"
      />
      <circle cx="24" cy="24" r="3.1" className="fill-background" />
      <circle
        cx="24"
        cy="24"
        r="20.5"
        stroke="#DC2626"
        strokeOpacity="0.35"
        strokeWidth="1.6"
      />
    </svg>
  );
}

interface BrandLockupProps {
  className?: string;
  subtitle?: string;
  size?: "sm" | "md" | "lg";
}

export function BrandLockup({
  className,
  subtitle = "Enterprise AI Assistant",
  size = "md",
}: BrandLockupProps) {
  const markSize = {
    sm: "h-7 w-7",
    md: "h-9 w-9",
    lg: "h-12 w-12",
  }[size];

  const titleSize = {
    sm: "text-sm",
    md: "text-base",
    lg: "text-xl",
  }[size];

  return (
    <div className={cn("flex items-center gap-3", className)}>
      <AerolloyMark className={cn(markSize, "shrink-0")} />
      <div className="min-w-0 leading-tight">
        <div
          className={cn(
            "truncate font-bold tracking-tight text-foreground flex items-center gap-1.5",
            titleSize,
          )}
        >
          <span className="text-red-600 dark:text-red-500">PTC</span>
          <span className="text-foreground">Industries</span>
          <span className="text-muted-foreground font-normal text-[0.85em]">• Aerolloy</span>
        </div>
        {subtitle && (
          <div className="truncate text-[0.7rem] uppercase tracking-[0.14em] text-red-700/80 dark:text-red-400/80 font-medium">
            {subtitle}
          </div>
        )}
      </div>
    </div>
  );
}
