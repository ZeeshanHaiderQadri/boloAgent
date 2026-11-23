"use client";

import { ArrowUpRight, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import type { TaskView } from "../lib/taskView";

function embedUrl(path: string) {
  return `${path}${path.includes("?") ? "&" : "?"}embed=1`;
}

/** Renders a real dashboard route in the workspace. `?embed=1` makes the app
 * shell drop its nav/header inside the frame, so the pane shows just the page. */
export function WorkspacePageView({ view }: { view: TaskView }) {
  const [reloadKey, setReloadKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const src = embedUrl(view.path);
  // The agent can retry a tool several times in a row, rewriting the URL each
  // time; wait for the path to settle so the frame reloads once, not per step.
  const [shownSrc, setShownSrc] = useState(src);

  useEffect(() => {
    if (src === shownSrc) return;
    const timer = setTimeout(() => setShownSrc(src), 700);
    return () => clearTimeout(timer);
  }, [src, shownSrc]);

  useEffect(() => {
    setLoading(true);
  }, [shownSrc, reloadKey]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-1.5 text-[11px] text-muted-foreground">
        <span className="truncate font-medium text-foreground/80">
          {view.title}
        </span>
        <span className="hidden truncate text-muted-foreground/60 sm:inline">
          {view.path}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => setReloadKey((key) => key + 1)}
            aria-label="Refresh workspace page"
            title="Refresh this page"
            className="rounded-md p-1 transition-colors duration-300 hover:text-foreground"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <a
            href={view.path}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open ${view.title} in a new tab`}
            title="Open in a new tab"
            className="rounded-md p-1 transition-colors duration-300 hover:text-foreground"
          >
            <ArrowUpRight className="h-3.5 w-3.5" />
          </a>
        </div>
      </div>
      <div className="relative flex min-h-0 flex-1">
        {loading && (
          <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-center justify-center gap-2 bg-background/70 py-2 text-xs text-muted-foreground backdrop-blur-sm">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-cta motion-reduce:animate-none" />
            Loading {view.title.toLowerCase()}…
          </div>
        )}
        <iframe
          key={`${shownSrc}:${reloadKey}`}
          src={shownSrc}
          title={`Workspace page: ${view.title}`}
          onLoad={() => setLoading(false)}
          className={cn(
            "min-h-0 w-full flex-1 border-0 bg-background transition-opacity duration-500",
            loading ? "opacity-0" : "opacity-100",
          )}
        />
      </div>
    </div>
  );
}
