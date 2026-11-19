import { useEffect, useRef, useState, type ReactNode } from "react";
import type { AccessStatus, MatchLabel, VerificationState, VersionLabel } from "../types";

const MATCH_HELP: Record<MatchLabel, string> = {
  Exact: "Identity confirmed by a matching identifier (DOI/arXiv ID) or by title plus corroborating fields.",
  Probable: "Very likely the paper you want, but not fully confirmed. Add an author or year to confirm.",
  Similar: "A related or similarly titled paper – not confirmed to be the one you searched for.",
  Unverified: "Found by keyword relevance; identity was not assessed.",
};

export function MatchBadge({ label }: { label: MatchLabel }) {
  return (
    <span className={`badge match-${label.toLowerCase()}`} title={MATCH_HELP[label]}>
      {label === "Exact" ? "✓ " : ""}
      {label} match
    </span>
  );
}

const ACCESS_CLASS: Record<AccessStatus, string> = {
  "Open Access": "access-open",
  Closed: "access-closed",
  "Alternative Version Found": "access-alt",
  Unknown: "access-unknown",
};

export function AccessBadge({ status }: { status: AccessStatus }) {
  return <span className={`badge ${ACCESS_CLASS[status]}`}>{status}</span>;
}

export function VersionBadge({ version }: { version: VersionLabel }) {
  const cls = version === "Version of Record" ? "ver-vor" : version === "Unknown" ? "ver-unknown" : "ver-alt";
  return <span className={`badge ${cls}`}>{version}</span>;
}

export function VerificationBadge({ state }: { state: VerificationState }) {
  const cls: Record<VerificationState, string> = {
    "Not Checked": "vs-none",
    Verified: "vs-ok",
    "Verification Limited": "vs-limited",
    Rejected: "vs-bad",
    Failed: "vs-bad",
  };
  return <span className={`badge ${cls[state]}`}>{state}</span>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span className="spinner" role="status" aria-label={label} />;
}

export function ErrorBox({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="alert alert-error" role="alert">
      <span>{children}</span>
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Notice({ children, kind = "info" }: { children: ReactNode; kind?: "info" | "warn" }) {
  return <div className={`alert alert-${kind}`}>{children}</div>;
}

/** Poll an async function while `active` is true. */
export function usePoll(fn: () => Promise<boolean | void>, active: boolean, intervalMs = 600) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const done = await fnRef.current();
        if (done || stopped) return;
      } catch {
        /* keep polling; errors surface in fn */
      }
      if (!stopped) timer = window.setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [active, intervalMs]);
}

export function useCopy(): [(text: string) => Promise<void>, boolean] {
  const [copied, setCopied] = useState(false);
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };
  return [copy, copied];
}

export function downloadText(filename: string, text: string, type = "text/plain") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function ConfirmButton({
  label, confirmLabel, onConfirm, className = "btn btn-small btn-danger", disabled,
}: { label: string; confirmLabel: string; onConfirm: () => void; className?: string; disabled?: boolean }) {
  const [arming, setArming] = useState(false);
  useEffect(() => {
    if (!arming) return;
    const t = window.setTimeout(() => setArming(false), 4000);
    return () => window.clearTimeout(t);
  }, [arming]);
  return (
    <button
      type="button"
      className={className}
      disabled={disabled}
      onClick={() => (arming ? (setArming(false), onConfirm()) : setArming(true))}
    >
      {arming ? confirmLabel : label}
    </button>
  );
}
