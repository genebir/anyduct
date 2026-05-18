import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Toaster } from "sonner";
import { AuthProvider } from "@/components/providers/auth-provider";
import { ThemeProvider } from "@/components/providers/theme-provider";
import { WorkspaceProvider } from "@/components/providers/workspace-provider";
import { AppShell } from "@/components/shell/app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    template: "%s · etlx",
    default: "etlx",
  },
  description: "Visual ETL pipelines on top of etl-plugins.",
};

export const viewport: Viewport = {
  themeColor: "#0a1228",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="font-sans antialiased">
        <ThemeProvider>
          <AuthProvider>
            <WorkspaceProvider>
              <AppShell>{children}</AppShell>
            </WorkspaceProvider>
          </AuthProvider>
        </ThemeProvider>
        <Toaster
          theme="dark"
          position="top-right"
          toastOptions={{
            className:
              "!bg-elevated !text-text !border !border-border-subtle !rounded-lg",
          }}
        />
      </body>
    </html>
  );
}
