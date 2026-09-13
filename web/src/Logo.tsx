export default function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="none">
      <rect
        width="31"
        height="31"
        x=".5"
        y=".5"
        rx="7.5"
        fill="var(--color-accent)"
      />
      <path
        d="M8 21l5.5-6.5 3.5 2.6L24.5 9"
        stroke="#fff"
        strokeWidth="2.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24.5" cy="9" r="1.8" fill="#fff" />
    </svg>
  );
}
