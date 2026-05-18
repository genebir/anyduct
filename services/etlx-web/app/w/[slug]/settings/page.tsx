"use client";

import { useParams } from "next/navigation";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";

export default function SettingsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);

  return (
    <>
      <Header
        title="Settings"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
      />
      <main className="mx-auto w-full max-w-3xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <Card>
          <CardHeader
            title="Workspace"
            description="Identity and accent color. Editing UI lands in Step 10.6."
          />
          {ws ? (
            <dl className="grid gap-4 sm:grid-cols-2">
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Name
                </dt>
                <dd className="mt-1 text-sm text-text">{ws.name}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Slug
                </dt>
                <dd className="mt-1 text-sm font-mono text-text-secondary">
                  {ws.slug}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Color
                </dt>
                <dd className="mt-1 flex items-center gap-2 text-sm">
                  <span
                    aria-hidden
                    className="inline-block h-4 w-4 rounded-sm"
                    style={{ background: ws.color_hex }}
                  />
                  <span className="font-mono text-text-secondary">
                    {ws.color_hex}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Your role
                </dt>
                <dd className="mt-1 text-sm capitalize text-text">
                  {ws.role ?? "SuperAdmin bypass"}
                </dd>
              </div>
            </dl>
          ) : null}
        </Card>
      </main>
    </>
  );
}
