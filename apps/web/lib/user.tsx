"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";

const KEY = "study-user-id";
const Ctx = createContext<{ userId: string | null; setUserId: (v: string) => void; clear: () => void }>({
  userId: null,
  setUserId: () => {},
  clear: () => {},
});

export function UserProvider({ children }: { children: ReactNode }) {
  const [userId, setUserIdState] = useState<string | null>(null);
  useEffect(() => {
    try {
      setUserIdState(localStorage.getItem(KEY));
    } catch {
      setUserIdState(null);
    }
  }, []);
  const setUserId = (v: string) => {
    try {
      localStorage.setItem(KEY, v);
    } catch {}
    setUserIdState(v);
  };
  const clear = () => {
    try {
      localStorage.removeItem(KEY);
    } catch {}
    setUserIdState(null);
  };
  return <Ctx.Provider value={{ userId, setUserId, clear }}>{children}</Ctx.Provider>;
}

export const useUser = () => useContext(Ctx);

/** Renders children only when signed in (dev placeholder until Phase 7 JWT). */
export function RequireUser({ children }: { children: (userId: string) => ReactNode }) {
  const { userId } = useUser();
  if (!userId) {
    return (
      <main className="container">
        <div className="card">
          <h1>Sign in</h1>
          <p className="muted">Enter any user id to continue (dev auth — Phase 7 adds JWT).</p>
          <Link className="btn" href="/">Go to sign in</Link>
        </div>
      </main>
    );
  }
  return <>{children(userId)}</>;
}
