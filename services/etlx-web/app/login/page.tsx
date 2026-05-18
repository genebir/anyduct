"use client";

import { FormEvent, Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/components/providers/auth-provider";
import { ApiError } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const next = params.get("next") ?? "/workspaces";

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await signIn(email, password);
      toast.success("Signed in");
      router.replace(next);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Sign-in failed.";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex w-full max-w-md flex-col gap-6 rounded-xl border border-border-subtle bg-surface p-8 shadow-lg">
      <div>
        <div className="text-xs font-semibold uppercase tracking-widest text-accent">
          etlx
        </div>
        <h1 className="mt-2 text-2xl font-semibold text-text">Welcome back</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Sign in to manage pipelines, schedules, and runs.
        </p>
      </div>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Email
          </span>
          <Input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Password
          </span>
          <Input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <Button type="submit" loading={submitting} className="mt-2 h-11">
          Sign in
        </Button>
      </form>
      <p className="text-xs text-text-muted">
        Trouble signing in? Ask your workspace owner or visit
        <code className="ml-1 rounded-sm bg-overlay px-1 py-0.5 font-mono text-[11px] text-text-secondary">
          /auth/oidc/providers
        </code>{" "}
        for SSO options.
      </p>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
