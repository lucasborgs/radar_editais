import Link from "next/link";
import { cn } from "@/lib/utils";

type Journey = "explore" | "radar" | "projects";

const JOURNEYS: Array<{ id: Journey; href: string; label: string }> = [
  { id: "explore", href: "/", label: "Explorar" },
  { id: "radar", href: "/radar", label: "Radar" },
  { id: "projects", href: "/projects", label: "Projetos" },
];

export function JourneyNavigation({ active }: { active: Journey }) {
  return (
    <nav
      aria-label="Jornadas principais"
      className="mb-8 flex items-center justify-between border-b border-border pb-3 text-sm"
    >
      <Link href="/" className="font-semibold text-content-primary">
        Radar Editais
      </Link>
      <div className="flex items-center gap-1">
        {JOURNEYS.map((journey) =>
          journey.id === active ? (
            <span
              key={journey.id}
              aria-current="page"
              className="rounded-lg bg-primary/10 px-3 py-1.5 font-medium text-primary"
            >
              {journey.label}
            </span>
          ) : (
            <Link
              key={journey.id}
              href={journey.href}
              className={cn(
                "rounded-lg px-3 py-1.5 text-content-secondary",
                "hover:bg-surface hover:text-content-primary",
              )}
            >
              {journey.label}
            </Link>
          ),
        )}
      </div>
    </nav>
  );
}
