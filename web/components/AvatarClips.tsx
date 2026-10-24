"use client";

import { useEffect, useState } from "react";
import type { AvatarClip } from "@/lib/avatars";

const MAKING = new Set(["queued", "rendering"]);

/**
 * One clip per call, both people side by side: the renter's call ("With you"), then each listing call.
 * A phone scrolls the row sideways. A clip that failed or was skipped is left out.
 */
export function AvatarClips({
  clips,
  videoUrl,
  loadVideo,
  className = "",
}: {
  clips: AvatarClip[];
  videoUrl?: (callId: string) => string;
  loadVideo?: (callId: string) => Promise<Blob | string>;
  className?: string;
}) {
  const shown = clips.filter((clip) => clip.status === "ready" || MAKING.has(clip.status));
  if (shown.length === 0) return null;
  return (
    <div className={`-mx-1 flex gap-3 overflow-x-auto px-1 pb-1 ${className}`}>
      {shown.map((clip) => (
        <figure key={clip.call_id} className="w-[280px] shrink-0">
          <div className="aspect-[3/2] overflow-hidden rounded-[var(--radius-card)] bg-map-land">
            {clip.status === "ready" ? (
              <ClipVideo callId={clip.call_id} videoUrl={videoUrl} loadVideo={loadVideo} />
            ) : (
              <p className="flex h-full items-center px-3 text-center text-meta text-ink-2">Making the clip</p>
            )}
          </div>
          <figcaption className="mt-1 truncate text-meta text-ink-2">
            {clip.role === "renter" ? "With you" : clip.address || "A listing"}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

function ClipVideo({
  callId,
  videoUrl,
  loadVideo,
}: {
  callId: string;
  videoUrl?: (callId: string) => string;
  loadVideo?: (callId: string) => Promise<Blob | string>;
}) {
  const direct = videoUrl?.(callId);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!loadVideo || direct) return;
    let object: string | undefined;
    let gone = false;
    loadVideo(callId).then((body) => {
      if (gone || typeof body === "string") return;
      object = URL.createObjectURL(body);
      setBlobUrl(object);
    });
    return () => {
      gone = true;
      if (object) URL.revokeObjectURL(object);
    };
  }, [callId, direct, loadVideo]);

  const src = direct || blobUrl;
  if (!src) return <p className="flex h-full items-center px-3 text-center text-meta text-ink-2">Making the clip</p>;
  return <video src={src} controls playsInline preload="metadata" className="h-full w-full bg-map-land object-cover" />;
}
