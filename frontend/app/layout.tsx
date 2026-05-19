import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ClawEmail",
  description: "ClawEmail migration workspace",
  icons: {
    icon: "/favicon.ico",
    shortcut: "/favicon.ico"
  }
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
