import "./globals.css";
import Link from "next/link";
import { UserProvider } from "../lib/user";

export const metadata = { title: "Adaptive Study Platform" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body>
        <UserProvider>
          <nav className="nav">
            <Link href="/">Home</Link>
            <Link href="/dashboard">Dashboard</Link>
            <Link href="/documents/upload">Upload</Link>
            <Link href="/progress">Progress</Link>
          </nav>
          {children}
        </UserProvider>
      </body>
    </html>
  );
}
