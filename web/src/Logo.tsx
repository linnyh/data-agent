export default function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="none">
      <defs>
        <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#22d3ee" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <rect
        width="31"
        height="31"
        x=".5"
        y=".5"
        rx="7.5"
        stroke="url(#lg)"
        strokeOpacity=".55"
      />
      <path
        d="M7 22l6-7 4 3 8-11"
        stroke="url(#lg)"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="25" cy="7" r="2" fill="#22d3ee" />
    </svg>
  );
}
