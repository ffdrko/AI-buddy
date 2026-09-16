"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { login, register } from "./api";

const UID_KEY = "study-user-id";
const TOKEN_KEY = "study-token";
const REFRESH_KEY = "study-refresh-token";

interface Ctx {
  userId: string | null;
  signIn: (email: string, password: string, mode: "login" | "register") => Promise<void>;
  clear: () => void;
}

const Ctx = createContext<Ctx>({ userId: null, signIn: async () => {}, clear: () => {} });

function storageGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}

function storageSet(k: string, v: string) {
  try {
    localStorage.setItem(k, v);
  } catch {}
}

function storageDel(k: string) {
  try {
    localStorage.removeItem(k);
  } catch {}
}

export function UserProvider({ children }: { children: ReactNode }) {
  const [userId, setUserIdState] = useState<string | null>(null);
  useEffect(() => {
    if (storageGet(TOKEN_KEY)) setUserIdState(storageGet(UID_KEY));
  }, []);
  const signIn = async (email: string, password: string, mode: "login" | "register") => {
    const res = mode === "login" ? await login(email, password) : await register(email, password);
    storageSet(TOKEN_KEY, res.access_token);
    storageSet(REFRESH_KEY, res.refresh_token);
    storageSet(UID_KEY, res.user_id);
    setUserIdState(res.user_id);
  };
  const clear = () => {
    storageDel(TOKEN_KEY);
    storageDel(REFRESH_KEY);
    storageDel(UID_KEY);
    setUserIdState(null);
  };
  return <Ctx.Provider value={{ userId, signIn, clear }}>{children}</Ctx.Provider>;
}

export const useUser = () => useContext(Ctx);

/** Renders children only when signed in (JWT, Phase 7). */
export function RequireUser({ children }: { children: (userId: string) => ReactNode }) {
  const { userId } = useUser();
  if (!userId) {
    return (
      <main className="container">
        <div className="card">
          <h1>Sign in</h1>
          <p className="muted">Your session expired or you signed out.</p>
          <Link className="btn" href="/">Go to sign in</Link>
        </div>
      </main>
    );
  }
  return <>{children(userId)}</>;
}
