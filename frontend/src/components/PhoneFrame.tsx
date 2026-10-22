import { forwardRef, ReactNode } from "react";
import { Bookmark, Heart, MessageCircle, Music2, Send } from "lucide-react";

/** A phone-shaped player with optional mock platform UI to check safe zones. */
export const PhoneFrame = forwardRef<HTMLVideoElement, {
  src: string; poster?: string; safeZones?: boolean; platform?: "instagram" | "youtube"; caption?: string;
  autoPlay?: boolean; muted?: boolean; onEnded?: () => void; children?: ReactNode; className?: string; overlay?: ReactNode;
  maxWidth?: number;
}>(function PhoneFrame({ src, poster, safeZones = false, platform = "instagram", caption, autoPlay = true, muted = false, onEnded, children, className = "", overlay, maxWidth = 340 }, ref) {
  return (
    <div style={{ width: maxWidth, maxWidth: "100%" }} className={`relative mx-auto aspect-[9/19.5] rounded-[44px] border-[10px] border-[#0b0b0b] bg-black shadow-2xl ring-1 ring-white/10 ${className}`}>
      <div className="absolute left-1/2 top-2 z-20 h-6 w-24 -translate-x-1/2 rounded-full bg-[#0b0b0b]" />
      <div className="absolute inset-0 overflow-hidden rounded-[34px]">
        <div className="absolute inset-x-0 top-1/2 aspect-[9/16] -translate-y-1/2">
          {src ? (
            <video ref={ref} key={src} src={src} poster={poster} className="h-full w-full object-cover" playsInline
              autoPlay={autoPlay} muted={muted} loop={!onEnded} onEnded={onEnded} controls={false}
              onLoadedData={(e) => {
                const v = e.currentTarget;
                // Browsers block autoplay with sound until the page has been clicked; fall back to muted.
                if (autoPlay) v.play().catch(() => { v.muted = true; v.play().catch(() => {}); });
              }}
              onClick={(e) => { const v = e.currentTarget; v.muted = false; v.paused ? v.play() : v.pause(); }} />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-white/50">Rendering…</div>
          )}
          {overlay}
          {safeZones && (
            <div className="pointer-events-none absolute inset-0 text-white">
              <div className="absolute inset-x-0 top-0 h-[7.8%] bg-black/25" />
              <div className="absolute inset-x-0 bottom-0 h-[18.75%] bg-gradient-to-t from-black/60 to-transparent" />
              <div className="absolute bottom-[21%] right-2 flex flex-col items-center gap-4 drop-shadow">
                {platform === "instagram" ? (
                  <><Heart size={22} /><MessageCircle size={22} /><Send size={22} /><Bookmark size={22} /></>
                ) : (
                  <><Heart size={22} /><MessageCircle size={22} /><Send size={22} /></>
                )}
              </div>
              <div className="absolute bottom-[5%] left-3 right-14 text-[11px] leading-snug drop-shadow">
                <div className="font-semibold">@yourpage</div>
                <div className="line-clamp-2 opacity-90">{caption}</div>
                <div className="mt-1 flex items-center gap-1 opacity-80"><Music2 size={11} /> Original audio</div>
              </div>
            </div>
          )}
        </div>
      </div>
      {children}
    </div>
  );
});
