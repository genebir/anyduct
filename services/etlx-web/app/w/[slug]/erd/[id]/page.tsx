"use client";

/**
 * /w/[slug]/erd/[id] — Phase AHC. Editor for a single saved ERD diagram
 * (the list lives at /w/[slug]/erd, like pipelines/migrations).
 */

import { useParams } from "next/navigation";
import { ErdDesigner } from "@/components/erd/erd-designer";

export default function ErdEditorPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  return <ErdDesigner slug={slug} docId={id} />;
}
