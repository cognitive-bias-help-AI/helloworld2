import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "투자 판단 근거 점검", description: "로컬 검토 UI" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body>{children}</body></html>;
}
