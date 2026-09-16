"use client";

import { useEffect } from "react";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main className="container">
      <div className="card">
        <h1>Something went wrong</h1>
        <p className="muted">{error.message || "The API may be unreachable. Check your connection and try again."}</p>
        <button className="btn" onClick={reset}>Try again</button>
      </div>
    </main>
  );
}
