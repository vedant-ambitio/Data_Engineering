import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Data Monitoring Dashboard",
  description: "Course Data Verification System",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
