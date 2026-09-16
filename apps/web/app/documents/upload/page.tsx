"use client";

import { useRef, useState } from "react";
import { getDocumentStatus, uploadDocument } from "../../../lib/api";
import { RequireUser } from "../../../lib/user";

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function UploadInner({ userId }: { userId: string }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [docId, setDocId] = useState<string | null>(null);

  async function pollUntilReady(id: string) {
    for (let i = 0; i < 120; i++) {
      const s = await getDocumentStatus(userId, id);
      setStatus(`Pipeline: ${s.status}`);
      if (s.status === "ready") return;
      if (s.status === "failed") throw new Error(s.error_message ?? "processing failed");
      await sleep(2000);
    }
    throw new Error("timed out waiting for processing");
  }

  async function onUpload() {
    setError("");
    setDocId(null);
    const f = fileRef.current?.files?.[0];
    if (!f) {
      setError("Choose a PDF first.");
      return;
    }
    try {
      setStatus("Uploading…");
      const res = await uploadDocument(userId, f, f.name);
      setDocId(res.document_id);
      if (res.deduped) {
        setStatus(`Duplicate detected — linked existing document. Status: ${res.status}`);
        return;
      }
      await pollUntilReady(res.document_id);
    } catch (e) {
      setError(String(e));
      setStatus("");
    }
  }

  return (
    <main className="container">
      <div className="card">
        <h1>Upload PDF</h1>
        <input ref={fileRef} type="file" accept="application/pdf" />
        <button className="btn" onClick={onUpload}>Upload & process</button>
        {status && <p className="muted">{status}</p>}
        {error && <p className="error">{error}</p>}
        {docId && status.includes("ready") && (
          <div className="row">
            <a className="btn" href={`/study/${docId}`}>Study now</a>
          </div>
        )}
      </div>
    </main>
  );
}

export default function Upload() {
  return <RequireUser>{(userId) => <UploadInner userId={userId} />}</RequireUser>;
}
