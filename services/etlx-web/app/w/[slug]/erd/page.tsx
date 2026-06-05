"use client";

/**
 * /w/[slug]/erd — Phase AGX (2026-06-05).
 *
 * Interactive ERD designer: draw tables, columns and relationships by
 * hand, export to SQL DDL. Client-side (localStorage auto-save). The
 * designer fills the shell's content column, so this page is a thin host.
 */

import { useParams } from "next/navigation";
import { ErdDesigner } from "@/components/erd/erd-designer";

export default function ErdPage() {
  const { slug } = useParams<{ slug: string }>();
  return <ErdDesigner slug={slug} />;
}
