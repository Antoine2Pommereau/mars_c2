import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

/** Vues d'un même sujet, une à la fois : photo AIS et image satellite d'un navire, par exemple. */
export default function Slideshow({ slides }: { slides: { key: string; node: ReactNode }[] }) {
  const [i, setI] = useState(0);
  if (slides.length === 0) return null;
  if (slides.length === 1) return <>{slides[0].node}</>;
  const cur = Math.min(i, slides.length - 1);
  const go = (step: number) => setI((n) => (n + step + slides.length) % slides.length);
  return (
    <div className="relative mt-3">
      {slides[cur].node}
      <button onClick={() => go(-1)} aria-label="Vue précédente"
        className="absolute left-1.5 top-[45%] -translate-y-1/2 rounded-full border border-hair bg-panel/80 p-1 text-ink hover:border-signal">
        <ChevronLeft size={16} />
      </button>
      <button onClick={() => go(1)} aria-label="Vue suivante"
        className="absolute right-1.5 top-[45%] -translate-y-1/2 rounded-full border border-hair bg-panel/80 p-1 text-ink hover:border-signal">
        <ChevronRight size={16} />
      </button>
      <div className="mt-1 flex justify-center gap-1.5">
        {slides.map((s, n) => (
          <button key={s.key} onClick={() => setI(n)} aria-label={`Vue ${n + 1}`}
            className={`h-1.5 w-1.5 rounded-full ${n === cur ? "bg-signal" : "bg-hair"}`} />
        ))}
      </div>
    </div>
  );
}
