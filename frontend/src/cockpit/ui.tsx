import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "../lib/utils";
import { STATUS_STYLE } from "../lib/shift";
import type { MachineStatus } from "../lib/shift";

/** Status badge: colour + text label, readable at a distance. */
export function StatusBadge({ status, size = "md", className }: { status: MachineStatus; size?: "md" | "lg"; className?: string }) {
  const s = STATUS_STYLE[status];
  return (
    <span
      className={cn(
        "inline-flex w-full items-center justify-center rounded-md font-bold tracking-wide uppercase",
        size === "lg" ? "h-12 text-[22px]" : "h-10 text-[18px]",
        className,
      )}
      style={{ background: s.bg, color: s.fg }}
    >
      {s.label}
    </span>
  );
}

type BtnVariant = "primary" | "secondary" | "danger-inverse" | "disabled";

/** Every action is at least 48px tall (gloves, glare). Primary actions are 56px. */
export function Btn({
  variant = "primary",
  size = "md",
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: "md" | "lg" }) {
  const styles: Record<BtnVariant, string> = {
    primary: "bg-navy text-white hover:bg-[#10243a] disabled:bg-disabled disabled:text-muted-ink",
    secondary: "bg-surface text-navy border border-line hover:bg-page disabled:text-muted-ink",
    "danger-inverse": "bg-white text-fault hover:bg-danger-bg",
    disabled: "bg-disabled text-muted-ink",
  };
  return (
    <button
      type="button"
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg px-6 font-bold transition-colors disabled:cursor-not-allowed",
        size === "lg" ? "min-h-14 text-[18px]" : "min-h-12 text-[16px]",
        styles[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-lg border border-line bg-surface", className)}>{children}</section>;
}

export function Mono({ className, children }: { className?: string; children: ReactNode }) {
  return <span className={cn("font-mono", className)}>{children}</span>;
}

/** Coloured banner with a mono state label, used at the top of the agent work area. */
export function StateBanner({ label, text, tone }: { label: string; text: ReactNode; tone: "ok" | "warn" | "danger" | "neutral" }) {
  const t = {
    ok: "bg-ok-bg border-ok-line text-running",
    warn: "bg-warn-bg border-warn text-warn",
    danger: "bg-danger-bg border-danger-line text-fault",
    neutral: "bg-page border-line text-navy",
  }[tone];
  return (
    <div className={cn("flex items-center gap-4 rounded-md border px-4 py-3", t)}>
      <Mono className="text-[14px] font-medium">{label}</Mono>
      <p className="text-[17px] font-semibold text-navy">{text}</p>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("sf-spin size-6", className)} aria-hidden>
      <circle cx="12" cy="12" r="10" fill="none" stroke="var(--sf-line)" strokeWidth="3" />
      <path d="M12 2a10 10 0 0 1 10 10" fill="none" stroke="var(--sf-navy)" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/** The assistant's line-drawn face (original artwork, matches the Chat FAB in Figma). */
export function AssistantFace({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" stroke="#1c1c1c" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M9 9 Q12 7 14 9" />
      <path d="M18 9 Q20 7 23 9" />
      <circle cx="12" cy="13" r="1.15" fill="#1c1c1c" stroke="none" />
      <circle cx="20" cy="13" r="1.15" fill="#1c1c1c" stroke="none" />
      <path d="M16 13 L14.5 19 L17 19" />
      <path d="M12 23 Q16 26 20 23" />
    </svg>
  );
}

export function LogoMark({ className }: { className?: string }) {
  // Simple factory glyph; replace with the official SkillForge logo asset when available.
  return (
    <svg viewBox="0 0 40 32" className={className} aria-hidden>
      <path d="M2 30V14l10 6V14l10 6V8h6l2-6h4l2 6v22z" fill="var(--sf-accent)" />
      <rect x="8" y="22" width="5" height="4" fill="#fff" />
      <rect x="17" y="22" width="5" height="4" fill="#fff" />
      <rect x="26" y="22" width="5" height="4" fill="#fff" />
    </svg>
  );
}
