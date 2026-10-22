import type { ReactElement } from "react";
import type { Art } from "../api";

// Line-art glyphs drawn on a 100x100 canvas. Product imagery is generated, so the store
// has no external image dependencies and works in any account or region.
const GLYPHS: Record<string, ReactElement> = {
  smartphones: (
    <>
      <rect x="34" y="16" width="32" height="64" rx="7" />
      <line x1="45" y1="23" x2="55" y2="23" />
      <circle cx="50" cy="72" r="2.5" />
    </>
  ),
  laptops: (
    <>
      <rect x="24" y="26" width="52" height="34" rx="4" />
      <path d="M16 68h68l-5 7H21z" />
    </>
  ),
  audio: (
    <>
      <path d="M26 58v-8a24 24 0 0 1 48 0v8" />
      <rect x="20" y="54" width="12" height="22" rx="5" />
      <rect x="68" y="54" width="12" height="22" rx="5" />
    </>
  ),
  wearables: (
    <>
      <path d="M40 22l2-8h16l2 8M40 78l2 8h16l2-8" />
      <rect x="32" y="22" width="36" height="56" rx="11" />
      <path d="M50 38v13l8 6" />
    </>
  ),
  cameras: (
    <>
      <path d="M20 34h14l5-8h22l5 8h14v40H20z" />
      <circle cx="50" cy="53" r="13" />
      <circle cx="50" cy="53" r="5" />
    </>
  ),
  gaming: (
    <>
      <path d="M30 36h40a14 14 0 0 1 13 18l-4 14a8 8 0 0 1-13 3l-7-7H41l-7 7a8 8 0 0 1-13-3l-4-14a14 14 0 0 1 13-18z" />
      <path d="M33 47v10M28 52h10" />
      <circle cx="64" cy="48" r="2.5" />
      <circle cx="70" cy="55" r="2.5" />
    </>
  ),
  "smart-home": (
    <>
      <path d="M18 50 50 22l32 28" />
      <path d="M27 44v34h46V44" />
      <path d="M42 62a11 11 0 0 1 16 0M37 56a18 18 0 0 1 26 0" />
      <circle cx="50" cy="68" r="2.5" />
    </>
  ),
  "tv-display": (
    <>
      <rect x="14" y="22" width="72" height="46" rx="4" />
      <path d="M38 80h24M50 68v12" />
    </>
  ),
  drones: (
    <>
      <circle cx="26" cy="30" r="10" />
      <circle cx="74" cy="30" r="10" />
      <circle cx="26" cy="72" r="10" />
      <circle cx="74" cy="72" r="10" />
      <path d="M33 37l10 9M67 37l-10 9M33 65l10-9M67 65l-10-9" />
      <rect x="40" y="42" width="20" height="18" rx="5" />
    </>
  ),
  accessories: (
    <>
      <path d="M38 16v16M62 16v16" />
      <path d="M28 32h44v14a22 22 0 0 1-44 0z" />
      <path d="M50 68v16" />
    </>
  ),
};

export function ProductArt({ art, category, size = "md", label }: {
  art: Art;
  category: string;
  size?: "sm" | "md" | "lg";
  label?: string;
}) {
  const id = `g-${art.from.slice(1)}-${art.to.slice(1)}`;
  return (
    <div className={`product-art product-art--${size}`} role="img" aria-label={label ?? category}>
      <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={art.from} />
            <stop offset="1" stopColor={art.to} />
          </linearGradient>
          <radialGradient id={`${id}-shine`} cx="0.25" cy="0.15" r="0.9">
            <stop offset="0" stopColor="#fff" stopOpacity="0.35" />
            <stop offset="0.6" stopColor="#fff" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect width="100" height="100" fill={`url(#${id})`} />
        <rect width="100" height="100" fill={`url(#${id}-shine)`} />
        <g fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.95"
           transform="translate(15 15) scale(0.7)">
          {GLYPHS[category] ?? GLYPHS.accessories}
        </g>
      </svg>
    </div>
  );
}

export function CategoryGlyph({ category }: { category: string }) {
  return (
    <svg viewBox="0 0 100 100" className="glyph" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round">
        {GLYPHS[category] ?? GLYPHS.accessories}
      </g>
    </svg>
  );
}
