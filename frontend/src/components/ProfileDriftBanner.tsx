"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { getProfileDrift, type ProfileDriftSignal } from "@/lib/api";

const DISMISS_KEY = "radar.profile-drift-dismissed-until";
const DISMISS_DAYS = 30;

function isDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY);
    if (!raw) return false;
    const until = parseInt(raw, 10);
    return Number.isFinite(until) && until > Date.now();
  } catch {
    return false;
  }
}

function setDismissed() {
  if (typeof window === "undefined") return;
  try {
    const until = Date.now() + DISMISS_DAYS * 24 * 60 * 60 * 1000;
    window.localStorage.setItem(DISMISS_KEY, String(until));
  } catch {
    // localStorage indisponível (private mode) — degrada silenciosamente
  }
}

export function ProfileDriftBanner() {
  const { getToken } = useAuth();
  const [signal, setSignal] = useState<ProfileDriftSignal | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    if (isDismissed()) {
      setHidden(true);
      return;
    }
    let cancelled = false;
    (async () => {
      const token = await getToken();
      if (cancelled || !token) return;
      try {
        const drift = await getProfileDrift(token);
        if (!cancelled) setSignal(drift);
      } catch {
        // Endpoint indisponível (pre-migration 011) ou erro — banner não
        // aparece, sem ruído.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  if (hidden || !signal?.stale || !signal.recommendation) return null;

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-amber-900 font-sans">
          Perfil pode estar desatualizado
        </p>
        <p className="text-xs text-amber-800 font-sans mt-0.5 leading-relaxed">
          {signal.recommendation}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <Link
          href="/"
          className="rounded-md bg-amber-900 text-white px-3 py-1.5 text-xs font-medium font-sans hover:opacity-90"
        >
          Revisar perfil
        </Link>
        <button
          type="button"
          onClick={() => {
            setDismissed();
            setHidden(true);
          }}
          className="text-xs text-amber-900 hover:underline font-sans"
        >
          Dispensar por 30 dias
        </button>
      </div>
    </div>
  );
}
