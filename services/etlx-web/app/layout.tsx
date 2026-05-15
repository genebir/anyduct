import type { ReactNode } from "react";

export const metadata = {
  title: "etlx-web",
  description: "etl-plugins web UI — Step 7.1 placeholder",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
